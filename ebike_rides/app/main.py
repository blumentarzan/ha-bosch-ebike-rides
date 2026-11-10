"""Web server (Home Assistant Ingress) and entry point."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import zipfile
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

from . import gpx, stats
from .bosch import auth
from .db import DB, Filter
from .ha import HABridge
from .sync import SyncManager

_LOGGER = logging.getLogger("ebike_rides")
STATIC = Path(__file__).parent / "static"
INGRESS_IP = "172.30.32.2"
PUBLIC_ACCOUNT_FIELDS = (
    "id", "name", "color", "source", "client_id", "status", "last_error", "last_sync",
)


def _options() -> dict:
    path = Path(os.environ.get("OPTIONS_PATH", "/data/options.json"))
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- helpers


def _ids(value: str | None, cast=int) -> list | None:
    if not value:
        return None
    out = []
    for part in value.split(","):
        part = part.strip()
        if part:
            out.append(cast(part))
    return out or None


def _date_ts(value: str | None, end: bool = False) -> int | None:
    if not value:
        return None
    d = datetime.fromisoformat(value)
    if d.tzinfo is None:
        d = d.replace(tzinfo=stats._zone(None))
    ts = int(d.timestamp())
    return ts + 86400 if end and len(value) == 10 else ts


def _filter(req: web.Request) -> Filter:
    q = req.query
    return Filter(
        accounts=_ids(q.get("accounts")),
        bikes=_ids(q.get("bikes"), str),
        start=_date_ts(q.get("from")),
        end=_date_ts(q.get("to"), end=True),
    )


def _public(acc: dict) -> dict:
    return {k: acc.get(k) for k in PUBLIC_ACCOUNT_FIELDS}


def _bad(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"error": msg}, status=status)


# ---------------------------------------------------------------- app


def build_app(db: DB, sync: SyncManager, ingress_only: bool = False) -> web.Application:
    @web.middleware
    async def guard(request: web.Request, handler):
        if ingress_only and request.remote not in (INGRESS_IP, "127.0.0.1"):
            return web.Response(status=403, text="Ingress only")
        try:
            return await handler(request)
        except auth.AuthError as err:
            return _bad(str(err))
        except (ValueError, KeyError) as err:
            return _bad(f"invalid request: {err}")

    app = web.Application(middlewares=[guard])
    r = web.RouteTableDef()

    @r.get("/")
    async def index(_req):
        return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @r.get("/api/state")
    async def state(_req):
        years = db.q(
            "SELECT MIN(start_ts) lo, MAX(start_ts) hi, COUNT(*) n, "
            "SUM(track_status='ok') tracks, SUM(track_status IN ('pending','error')) missing "
            "FROM activities"
        )[0]
        return web.json_response(
            {
                "accounts": [_public(a) for a in db.accounts()],
                "bikes": db.q(
                    "SELECT b.*, (SELECT COUNT(*) FROM activities a WHERE a.account_id=b.account_id "
                    "AND a.bike_id=b.id) rides FROM bikes b ORDER BY account_id, name"
                ),
                "unassigned": db.q(
                    "SELECT account_id, COUNT(*) n FROM activities WHERE bike_id IS NULL "
                    "OR bike_id NOT IN (SELECT id FROM bikes) GROUP BY account_id"
                ),
                "progress": sync.progress,
                "range": years,
                "ha_bridge": sync.ha.enabled,
                "ha_language": await sync.ha.language(),
            }
        )

    # ------------------------------------------------------------ accounts
    @r.post("/api/accounts")
    async def add_account(req):
        body = await req.json()
        name = (body.get("name") or "").strip()
        source = body.get("source") or "mobile"
        if not name:
            return _bad("Name fehlt")
        if source not in auth.SOURCES:
            return _bad("unknown source")
        if source == "dataact" and not (body.get("client_id") or "").strip():
            return _bad("Data Act braucht eine Client-ID")
        aid = db.add_account(name, source, (body.get("client_id") or "").strip(), body.get("color"))
        return web.json_response(_public(db.account(aid)))

    @r.patch("/api/accounts/{id}")
    async def edit_account(req):
        aid = int(req.match_info["id"])
        body = await req.json()
        fields = {k: body[k] for k in ("name", "color", "source", "client_id") if k in body}
        if "source" in fields and fields["source"] not in auth.SOURCES:
            return _bad("unknown source")
        if "source" in fields or "client_id" in fields:
            fields.update(access_token=None, refresh_token=None, status="login_required")
        db.update_account(aid, **fields)
        return web.json_response(_public(db.account(aid)))

    @r.delete("/api/accounts/{id}")
    async def delete_account(req):
        db.delete_account(int(req.match_info["id"]))
        return web.json_response({"ok": True})

    @r.post("/api/accounts/{id}/login")
    async def login(req):
        return web.json_response({"url": sync.start_login(int(req.match_info["id"]))})

    @r.post("/api/accounts/{id}/login/finish")
    async def login_finish(req):
        body = await req.json()
        await sync.finish_login(int(req.match_info["id"]), body.get("pasted", ""))
        return web.json_response({"ok": True})

    @r.post("/api/accounts/{id}/sync")
    async def sync_one(req):
        sync.trigger(int(req.match_info["id"]))
        return web.json_response({"ok": True})

    @r.post("/api/sync")
    async def sync_all(_req):
        sync.trigger()
        return web.json_response({"ok": True})

    # ------------------------------------------------------------ data
    @r.get("/api/rides")
    async def rides(req):
        rows = db.filtered(_filter(req))
        return web.json_response({"rides": rows[::-1]})

    @r.patch("/api/rides/{acc}/{id}")
    async def assign(req):
        body = await req.json()
        db.x(
            "UPDATE activities SET bike_id=? WHERE account_id=? AND id=?",
            (body.get("bike_id") or None, int(req.match_info["acc"]), req.match_info["id"]),
        )
        return web.json_response({"ok": True})

    @r.get("/api/stats")
    async def stat(req):
        group = req.query.get("group", "month")
        rows = db.filtered(_filter(req))
        accounts = db.accounts()
        span = None
        if group == "day" and req.query.get("from") and req.query.get("to"):
            span = stats.day_range(
                datetime.fromisoformat(req.query["from"]).date(),
                datetime.fromisoformat(req.query["to"]).date(),
            )
        data = await asyncio.to_thread(
            lambda: {
                "series": stats.series(rows, accounts, group, span),
                "leaderboard": stats.leaderboard(rows, accounts),
            }
        )
        return web.json_response(data)

    @r.get("/api/heat")
    async def heat(req):
        rows = db.filtered(_filter(req), with_geo="cells")
        return web.json_response(await asyncio.to_thread(stats.heat, rows))

    @r.get("/api/lines")
    async def lines(req):
        rows = db.filtered(_filter(req), with_geo="simple")
        colors = {a["id"]: a["color"] for a in db.accounts()}
        out = [
            {"a": r["account_id"], "id": r["id"], "c": colors.get(r["account_id"]),
             "p": json.loads(r["geo"])}
            for r in rows if r.get("geo")
        ]
        return web.json_response({"lines": out})

    @r.get("/api/rides/{acc}/{id}/track")
    async def track(req):
        pts = db.track_points(int(req.match_info["acc"]), req.match_info["id"])
        return web.json_response({"points": pts})

    @r.get("/api/rides/{acc}/{id}/gpx")
    async def ride_gpx(req):
        acc, aid = int(req.match_info["acc"]), req.match_info["id"]
        row = db.q("SELECT * FROM activities WHERE account_id=? AND id=?", (acc, aid))
        if not row:
            raise web.HTTPNotFound()
        body = gpx.to_gpx(row[0], db.track_points(acc, aid))
        return web.Response(
            body=body,
            content_type="application/gpx+xml",
            headers={"Content-Disposition": f'attachment; filename="{gpx.filename(row[0])}"'},
        )

    @r.get("/api/export.zip")
    async def export(req):
        rows = db.filtered(_filter(req))
        names = {a["id"]: a["name"] for a in db.accounts()}

        def build() -> bytes:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for row in rows:
                    pts = db.track_points(row["account_id"], row["id"])
                    if pts:
                        folder = names.get(row["account_id"], "rider")
                        z.writestr(f"{folder}/{gpx.filename(row)}", gpx.to_gpx(row, pts))
            return buf.getvalue()

        return web.Response(
            body=await asyncio.to_thread(build),
            content_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="ebike-rides.zip"'},
        )

    app.add_routes(r)
    app.router.add_static("/static/", STATIC)
    return app


async def amain() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    opts = _options()
    db = DB(os.environ.get("DB_PATH", "/data/rides.db"))
    session = aiohttp.ClientSession(headers={"User-Agent": "ha-bosch-ebike-rides"})
    ha = HABridge(session, bool(opts.get("publish_sensors", True)))
    sync = SyncManager(
        db, session, ha,
        interval_min=int(opts.get("sync_interval_minutes", 30)),
        detail_delay_s=float(opts.get("detail_delay_seconds", 0.5)),
    )
    app = build_app(db, sync, ingress_only=bool(os.environ.get("SUPERVISOR_TOKEN")))
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    port = int(os.environ.get("PORT", "8099"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    _LOGGER.info("eBike Rides listening on :%d", port)
    sync.start()
    try:
        await asyncio.Event().wait()
    finally:
        await sync.stop()
        await session.close()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
