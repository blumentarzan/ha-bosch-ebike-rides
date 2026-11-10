"""A fake Bosch cloud for tests and UI previews.

Serves the Keycloak token endpoint plus the mobile and Data Act APIs with
generated rides around Munich city centre (public landmarks only): a repeated
commute (so the heatmap has a hot corridor) plus random leisure loops, for two
accounts.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from aiohttp import web

HOME = (48.1374, 11.5755)  # Marienplatz
WORK = (48.2650, 11.6710)  # Garching research campus
LOOPS = [(48.17, 11.50), (48.10, 11.65), (48.20, 11.62), (48.08, 11.52)]


def _path(a, b, n, wobble, rnd):
    pts = []
    for i in range(n + 1):
        f = i / n
        lat = a[0] + (b[0] - a[0]) * f + math.sin(f * math.pi * 3) * 0.004
        lon = a[1] + (b[1] - a[1]) * f + math.sin(f * math.pi * 2) * 0.006
        pts.append((lat + rnd.gauss(0, wobble), lon + rnd.gauss(0, wobble)))
    return pts


def _loop(center, rnd, n=180):
    r = 0.02 + 0.03 * (LOOPS.index(center) / len(LOOPS)) + rnd.gauss(0, 0.0002)
    pts = [HOME]
    for i in range(n):
        t = i / n * 2 * math.pi
        pts.append((center[0] + r * math.sin(t) + rnd.gauss(0, 0.00003),
                    center[1] + r * 1.4 * math.cos(t) + rnd.gauss(0, 0.00003)))
    pts.append(HOME)
    return pts


def generate(account: int, seed: int, count: int, bikes: list[str]):
    rnd = random.Random(seed)
    start = datetime(2025, 3, 1, 7, 0, tzinfo=timezone.utc)
    rides, tracks = [], {}
    odo = {b: 1000.0 * (i + 1) for i, b in enumerate(bikes)}
    for i in range(count):
        day = start + timedelta(days=int(i * 600 / count), hours=rnd.choice([0, 1, 9, 10]))
        if rnd.random() < 0.6:
            pts = _path(HOME, WORK, 220, 0.00003, rnd)
            if rnd.random() < 0.5:
                pts = pts[::-1]
            title = "Commute"
        else:
            pts = _loop(rnd.choices(LOOPS, weights=[6, 3, 2, 1])[0], rnd)
            title = "Evening loop"
        dist = sum(
            6371000 * math.hypot(
                math.radians(b[0] - a[0]), math.radians(b[1] - a[1]) * math.cos(math.radians(a[0]))
            )
            for a, b in zip(pts, pts[1:])
        )
        dur = dist / (22 / 3.6)
        bike = bikes[i % len(bikes)]
        aid = f"acc{account}-ride{i:04d}"
        rides.append(
            {
                "id": aid,
                "type": "activitySummary",
                "attributes": {
                    "startTime": int(day.timestamp()),
                    "endTime": int(day.timestamp() + dur),
                    "timeZoneOfActivity": "Europe/Berlin",
                    "title": title,
                    "distance": round(dist),
                    "durationWithoutStops": round(dur),
                    "averageSpeed": 22.0,
                    "maximumSpeed": 38.5,
                    "elevationGain": rnd.randint(50, 600),
                    "elevationLoss": rnd.randint(50, 600),
                    "averageRiderPower": rnd.randint(90, 180),
                    "startOdometer": round(odo[bike]),
                    # first account omits bikeId to exercise odometer attribution
                    "bikeId": bike if account != 1 else None,
                },
            }
        )
        odo[bike] += dist
        tracks[aid] = [
            {"s": 0, "v": 22.0, "h": 450 + 30 * math.sin(k / 15), "lat": round(p[0], 6), "lon": round(p[1], 6)}
            for k, p in enumerate(pts)
        ]
    bike_list = [
        {"id": b, "attributes": {"name": f"Bike {b[-1]}", "driveUnit": {"odometer": round(odo[b])}}}
        for b in bikes
    ]
    rides.reverse()  # API answers newest first
    return bike_list, rides, tracks


def make_app(n1: int = 160, n2: int = 70) -> web.Application:
    data = {
        "tok-1": generate(1, 1, n1, ["bikeA1", "bikeA2"]),
        "tok-2": generate(2, 2, n2, ["bikeB1"]),
    }
    codes = {"code-1": "tok-1", "code-2": "tok-2"}
    app = web.Application()
    app["calls"] = []

    def _acct(req):
        tok = req.headers.get("Authorization", "").removeprefix("Bearer ")
        base = tok.split(".")[0]
        if base not in data:
            raise web.HTTPUnauthorized()
        return data[base]

    async def token(req):
        form = await req.post()
        if form.get("grant_type") == "authorization_code":
            base = codes.get(form.get("code"))
            if not base or not form.get("code_verifier"):
                return web.json_response({"error": "invalid_grant"}, status=400)
        else:
            base = (form.get("refresh_token") or "").removeprefix("r-").split(".")[0]
            if base not in data:
                return web.json_response({"error": "invalid_grant"}, status=400)
        n = random.randint(1, 10**6)
        return web.json_response(
            {"access_token": f"{base}.{n}", "refresh_token": f"r-{base}.{n}", "expires_in": 300}
        )

    async def m_bikes(req):
        bikes, _, _ = _acct(req)
        return web.json_response({"data": {"attributes": {"bikes": bikes}}})

    async def m_acts(req):
        _, rides, _ = _acct(req)
        size = int(req.query.get("size", 20))
        page = int(req.query.get("page", 0))
        pages = max(1, math.ceil(len(rides) / size))
        req.app["calls"].append(("list", page))
        return web.json_response(
            {"data": rides[page * size:(page + 1) * size], "meta": {"pages": pages}}
        )

    async def m_detail(req):
        _, _, tracks = _acct(req)
        t = tracks.get(req.match_info["id"])
        if t is None:
            raise web.HTTPNotFound()
        return web.json_response({"data": {"attributes": {"activityData": t}}})

    async def d_acts(req):
        _, rides, _ = _acct(req)
        limit, offset = int(req.query["limit"]), int(req.query["offset"])
        flat = [{"id": r["id"], **r["attributes"]} for r in rides]
        return web.json_response(
            {"activitySummaries": flat[offset:offset + limit], "pagination": {"total": len(flat)}}
        )

    async def d_detail(req):
        _, _, tracks = _acct(req)
        t = tracks.get(req.match_info["id"]) or []
        return web.json_response(
            {"activityDetails": [{"latitude": p["lat"], "longitude": p["lon"], "altitude": p["h"]} for p in t]}
        )

    async def d_bikes(req):
        bikes, _, _ = _acct(req)
        return web.json_response({"bikes": [{"id": b["id"], **b["attributes"]} for b in bikes]})

    app.router.add_post("/token", token)
    app.router.add_get("/m/v1/bike-profile", m_bikes)
    app.router.add_get("/a/v1/activity", m_acts)
    app.router.add_get("/a/v1/activity/{id}/detail", m_detail)
    app.router.add_get("/d/activity/smart-system/v1/activities", d_acts)
    app.router.add_get("/d/activity/smart-system/v1/activities/{id}/details", d_detail)
    app.router.add_get("/d/bike-profile/smart-system/v1/bikes", d_bikes)
    return app


def patch_urls(base: str) -> None:
    from app.bosch import auth, client

    auth.TOKEN_URL = f"{base}/token"
    client.MOBILE_PROFILE_BASE = f"{base}/m"
    client.MOBILE_ACTIVITY_BASE = f"{base}/a"
    client.DATAACT_BASE = f"{base}/d"
