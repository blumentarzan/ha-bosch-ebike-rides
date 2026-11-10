"""Login handling and the periodic sync of every account."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import aiohttp

from . import attribution, geo, stats
from .bosch import auth
from .bosch.client import ApiError, AuthExpired, make_client
from .db import DB
from .ha import HABridge

_LOGGER = logging.getLogger(__name__)


class SyncManager:
    def __init__(
        self,
        db: DB,
        session: aiohttp.ClientSession,
        ha: HABridge,
        interval_min: int = 30,
        detail_delay_s: float = 0.5,
    ) -> None:
        self.db = db
        self.session = session
        self.ha = ha
        self.interval = max(5, interval_min) * 60
        self.detail_delay = detail_delay_s
        self._locks: dict[int, asyncio.Lock] = {}
        self.progress: dict[int, dict] = {}
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ login
    def start_login(self, account_id: int) -> str:
        acc = self.db.account(account_id)
        if not acc:
            raise auth.AuthError("unknown account")
        pending = auth.start_login(acc["source"], acc.get("client_id"))
        self.db.save_pending(
            pending.state, account_id, pending.verifier, pending.client_id, pending.redirect_uri
        )
        return pending.url

    async def finish_login(self, account_id: int, pasted: str) -> None:
        row = self.db.latest_pending(account_id)
        if not row:
            raise auth.AuthError("Please click 'Log in' first")
        code = auth.extract_code(pasted, row["state"])
        pending = auth.PendingLogin(
            row["state"], row["verifier"], row["client_id"], row["redirect_uri"], ""
        )
        tokens = await auth.exchange_code(self.session, pending, code)
        self.db.update_account(
            account_id,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
            status="ok",
            last_error=None,
        )
        self.db.clear_pending(account_id)
        self.trigger(account_id)

    def _token_provider(self, account_id: int):
        async def provide(force: bool) -> str:
            acc = self.db.account(account_id) or {}
            if not force and acc.get("access_token") and acc.get("expires_at", 0) > time.time():
                return acc["access_token"]
            cid, _ = auth.client_for(acc["source"], acc.get("client_id"))
            tokens = await auth.refresh(self.session, cid, acc.get("refresh_token") or "")
            self.db.update_account(
                account_id,
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                expires_at=tokens.expires_at,
            )
            return tokens.access_token

        return provide

    # ------------------------------------------------------------------ sync
    def trigger(self, account_id: int | None = None) -> None:
        """Start a sync now (one account or all) without waiting for it."""
        targets = [account_id] if account_id else [a["id"] for a in self.db.accounts()]
        for aid in targets:
            task = asyncio.get_running_loop().create_task(self.sync_account(aid))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def idle(self) -> None:
        """Wait until every triggered sync has finished."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def sync_account(self, account_id: int) -> None:
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        if lock.locked():
            return
        async with lock:
            acc = self.db.account(account_id)
            if not acc or not acc.get("refresh_token"):
                return
            prog = self.progress[account_id] = {"phase": "rides", "done": 0, "total": 0}
            try:
                client = make_client(acc["source"], self.session, self._token_provider(account_id))
                bikes = await client.bikes()
                self.db.upsert_bikes(account_id, bikes)
                rides = await client.activities()
                is_first_import = not self.db.q(
                    "SELECT 1 FROM activities WHERE account_id=? LIMIT 1", (account_id,)
                )
                known = {
                    r["id"] for r in self.db.q(
                        "SELECT id FROM activities WHERE account_id=?", (account_id,)
                    )
                }
                self.db.upsert_activities(account_id, rides)
                all_bikes = self.db.bikes(account_id)
                self.db.set_bike_ids(account_id, attribution.attribute(all_bikes, rides))

                todo = self.db.pending_tracks(account_id)
                prog.update(phase="tracks", total=len(todo))
                for i, act_id in enumerate(todo, 1):
                    try:
                        points = await client.track(act_id)
                    except AuthExpired:
                        raise
                    except ApiError as err:
                        _LOGGER.warning("Track %s: %s", act_id, err)
                        self.db.mark_track(account_id, act_id, "error")
                        continue
                    await asyncio.to_thread(self._store_track, account_id, act_id, points)
                    prog["done"] = i
                    await asyncio.sleep(self.detail_delay)

                self.db.update_account(
                    account_id, status="ok", last_error=None, last_sync=time.time()
                )
                new = [r for r in rides if r["id"] not in known]
                if not is_first_import:
                    for r in new:
                        await self.ha.new_ride(acc["name"], r)
                await self.publish(account_id)
                _LOGGER.info(
                    "%s: %d rides (%d new), %d tracks fetched",
                    acc["name"], len(rides), len(new), len(todo),
                )
            except (AuthExpired, auth.AuthError) as err:
                _LOGGER.warning("%s: login required (%s)", acc["name"], err)
                self.db.update_account(account_id, status="login_required", last_error=str(err))
            except Exception as err:  # noqa: BLE001 - keep the loop alive, show it in the UI
                _LOGGER.exception("%s: sync failed", acc["name"])
                self.db.update_account(account_id, status="error", last_error=str(err)[:300])
            finally:
                self.progress.pop(account_id, None)

    def _store_track(self, account_id: int, act_id: str, points: list) -> None:
        latlon = [(p[0], p[1]) for p in points]
        simple = [[round(a, 5), round(b, 5)] for a, b in geo.simplify(latlon)]
        cells = geo.encode_cells(geo.cells_for_track(points))
        self.db.save_track(account_id, act_id, points, simple, cells)
        # Rides without a distance summary get one from the track.
        self.db.x(
            "UPDATE activities SET distance_m=? WHERE account_id=? AND id=? "
            "AND (distance_m IS NULL OR distance_m=0)",
            (geo.track_length_m(points), account_id, act_id),
        )

    async def publish(self, account_id: int) -> None:
        if not self.ha.enabled:
            return
        acc = self.db.account(account_id)
        rides = self.db.q("SELECT * FROM activities a WHERE account_id=?", (account_id,))
        totals = stats.rider_totals(rides, int(time.time()))
        last = max(rides, key=lambda r: r["start_ts"], default=None)
        if last:
            last = dict(last)
            last["start_iso"] = datetime.fromtimestamp(
                last["start_ts"], timezone.utc
            ).isoformat()
        await self.ha.publish_rider(acc["name"], totals, last)

    # ------------------------------------------------------------------ loop
    async def run_forever(self) -> None:
        await asyncio.sleep(5)
        while True:
            for acc in self.db.accounts():
                await self.sync_account(acc["id"])
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self.run_forever())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
