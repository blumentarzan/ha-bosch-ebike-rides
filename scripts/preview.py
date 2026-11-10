#!/usr/bin/env python3
"""Run the UI locally against the fake Bosch cloud with two demo riders."""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "ebike_rides"), str(ROOT / "tests")]

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402

import fake_bosch  # noqa: E402
from app.bosch import auth  # noqa: E402
from app.db import DB  # noqa: E402
from app.ha import HABridge  # noqa: E402
from app.main import build_app  # noqa: E402
from app.sync import SyncManager  # noqa: E402


async def run() -> None:
    fake = web.AppRunner(fake_bosch.make_app())
    await fake.setup()
    await web.TCPSite(fake, "127.0.0.1", 8199).start()
    fake_bosch.patch_urls("http://127.0.0.1:8199")
    data = ROOT / "preview-data"
    data.mkdir(exist_ok=True)
    db = DB(data / "rides.db")
    session = aiohttp.ClientSession()
    mgr = SyncManager(db, session, HABridge(session, False), detail_delay_s=0)
    if not db.accounts():
        for name, src, code, cid, color in (
            ("Alex", "mobile", "code-1", None, "#2f7ed8"),
            ("Sam", "dataact", "code-2", "euda-demo", "#e8590c"),
        ):
            aid = db.add_account(name, src, cid, color)
            mgr.start_login(aid)
            pend = db.latest_pending(aid)
            await mgr.finish_login(aid, f"{auth.MOBILE_REDIRECT}?state={pend['state']}&code={code}")
        await mgr.idle()
    runner = web.AppRunner(build_app(db, mgr))
    await runner.setup()
    port = int(os.environ.get("PORT", 8099))
    await web.TCPSite(runner, "127.0.0.1", port).start()
    print(f"preview on http://localhost:{port}")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(run())
