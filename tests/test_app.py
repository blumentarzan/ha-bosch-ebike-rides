import json
import logging
import re
import sys
from datetime import timedelta
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ebike_rides"))
sys.path.insert(0, str(ROOT / "tests"))

from app import attribution, geo, stats  # noqa: E402
from app.bosch import auth  # noqa: E402
from app.bosch.client import (  # noqa: E402
    check_activities, normalize_activity, normalize_bike, normalize_track, redact,
)
from app.db import DB  # noqa: E402
from app.ha import HABridge  # noqa: E402
from app.main import _version, build_app  # noqa: E402
from app.sync import SyncManager  # noqa: E402

import fake_bosch  # noqa: E402


# ------------------------------------------------------------------ units


def test_extract_code_variants():
    url = f"{auth.MOBILE_REDIRECT}?state=abc&session_state=x&code=c0de.1.2"
    assert auth.extract_code(url, "abc") == "c0de.1.2"
    assert auth.extract_code("http://localhost:8888/callback?code=zz&state=abc", "abc") == "zz"
    assert auth.extract_code("  bare.code.value ") == "bare.code.value"
    with pytest.raises(auth.AuthError):
        auth.extract_code(url, "other-state")
    with pytest.raises(auth.AuthError):
        auth.extract_code("https://x/?error=access_denied")


def test_extract_code_from_console_and_curl():
    console = (f"Failed to launch '{auth.MOBILE_REDIRECT}?state=abc&session_state=x&code=c0de.1.2' "
               "because the scheme does not have a registered handler.")
    assert auth.extract_code(console, "abc") == "c0de.1.2"
    curl = f"curl '{auth.MOBILE_REDIRECT}?state=abc&code=c0de.1.2' -H 'accept: */*'"
    assert auth.extract_code(curl, "abc") == "c0de.1.2"
    assert auth.extract_code(f"{auth.MOBILE_REDIRECT}?code=c%2Ed&state=abc", "abc") == "c.d"
    with pytest.raises(auth.AuthError):
        auth.extract_code("Failed to launch something else entirely")


def test_login_url_has_pkce():
    p = auth.start_login("mobile")
    assert "code_challenge=" in p.url and "client_id=one-bike-app" in p.url
    d = auth.start_login("dataact", "euda-123")
    assert "client_id=euda-123" in d.url and "localhost%3A8888" in d.url
    with pytest.raises(auth.AuthError):
        auth.start_login("dataact", "")


def test_normalize_both_shapes_and_zero_values():
    mobile = {"id": "a", "attributes": {"startTime": 1_700_000_000, "distance": 1000,
                                         "elevationGain": 0, "bikeId": "b"}}
    act = normalize_activity(mobile)
    assert act["start_ts"] == 1_700_000_000 and act["elevation_gain_m"] == 0.0
    dataact = {"id": "x", "startTime": "2025-05-01T06:00:00Z", "distance": 5000,
               "elevation": {"gain": 12}, "speed": {"average": 19.5}}
    act = normalize_activity(dataact)
    assert act["start_ts"] == 1746079200 and act["elevation_gain_m"] == 12
    assert act["avg_speed_kmh"] == 19.5
    assert normalize_track({"activityDetails": [{"latitude": 48.1, "longitude": 11.5},
                                                {"latitude": 0, "longitude": 0}]}) == [[48.1, 11.5, None, None]]


def test_redact_keeps_shape_drops_personal_data():
    raw = {
        "access_token": "abc", "data": [
            {"id": "r1", "attributes": {"title": "Home to work", "distance": 12345,
                                        "startTime": "2025-05-01T06:00:00Z",
                                        "owner": "someone@example.com",
                                        "startPoint": {"lat": 48.137412, "lon": 11.575512}}},
            {"id": "r2"}, {"id": "r3"},
        ],
        "bike": {"name": "My bike", "brandName": "Brand", "frameNumber": "XYZ"},
        "points": [{"latitude": 48.137412, "longitude": 11.575512, "altitude": 512.3}],
    }
    out = redact(raw)
    first = out["data"][0]["attributes"]
    assert out["access_token"] == "<redacted>"
    assert first["title"] == "<redacted>" and first["owner"] == "<redacted>"
    assert first["distance"] == 12345 and first["startTime"] == "2025-05-01T06:00:00Z"
    assert first["startPoint"] == {"lat": 48.1, "lon": 11.6}
    assert out["data"][2] == "... 1 more"
    assert out["bike"] == {"name": "<redacted>", "brandName": "Brand", "frameNumber": "<redacted>"}
    assert out["points"][0] == {"latitude": 48.1, "longitude": 11.6, "altitude": 512.3}


def test_check_activities_flags_wrong_mapping():
    good = [{"distance_m": 12_000, "duration_s": 2400, "elevation_gain_m": 50,
             "avg_speed_kmh": 18, "start_odometer_m": 1000}] * 3
    assert check_activities(3, good) == []
    km = [dict(r, distance_m=12.0) for r in good]
    assert any("looks like km" in p for p in check_activities(3, km))
    ms = [dict(r, avg_speed_kmh=5.0) for r in good]
    assert any("average speed" in p for p in check_activities(3, ms))
    assert any("skipped" in p for p in check_activities(5, good))
    assert any("none of 4" in p for p in check_activities(4, []))
    no_elev = [dict(r, elevation_gain_m=None) for r in good]
    assert check_activities(3, no_elev) == ["elevation gain missing in all 3 rides"]


def test_version_from_config_yaml():
    config = (ROOT / "ebike_rides" / "config.yaml").read_text()
    assert f'version: "{_version()}"' in config


def test_normalize_bike_mobile_profile_shape():
    raw = {"id": "b1", "type": "ECBSBikeProfileDto", "attributes": {
        "id": "b1", "brandName": "Generic Bikes",
        "driveUnit": {"productName": "Performance Line", "totalDistanceTraveled": 1234567.0},
    }}
    bike = normalize_bike(raw)
    assert bike == {"id": "b1", "name": "Generic Bikes", "battery_pct": None, "odometer_m": 1234567.0}
    raw["attributes"]["batteries"] = [{"batteryLevel": None}, {"batteryLevel": 64}]
    assert normalize_bike(raw)["battery_pct"] == 64


def test_ride_bike_ids():
    rides = [{"bike_id": "old", "start_ts": 10}, {"bike_id": "old", "start_ts": 30},
             {"bike_id": "new", "start_ts": 50}, {"bike_id": None, "start_ts": 60}]
    assert attribution.ride_bike_ids(rides) == {
        "old": {"rides": 2, "first": 10, "last": 30},
        "new": {"rides": 1, "first": 50, "last": 50},
        "none": {"rides": 1, "first": 60, "last": 60},
    }


def test_attribution_by_odometer():
    bikes = [{"id": "A", "odometer_m": 10_000}, {"id": "B", "odometer_m": 50_000}]
    rides = [
        {"id": "r2", "start_ts": 2, "start_odometer_m": 45_000, "distance_m": 5_000},
        {"id": "r1", "start_ts": 1, "start_odometer_m": 7_000, "distance_m": 3_000},
        {"id": "r0", "start_ts": 0, "start_odometer_m": None, "distance_m": 1},
    ]
    assert attribution.attribute(bikes, rides) == {"r2": "B", "r1": "A"}
    assert attribution.attribute(bikes[:1], rides)["r0"] == "A"


def test_heat_counts_rides_not_points():
    t = [[48.1, 11.5], [48.1, 11.5], [48.1, 11.5001]]  # standing still = still one ride
    cells = geo.cells_for_track(t)
    rows = [{"geo": geo.encode_cells(cells)}, {"geo": geo.encode_cells(cells)}]
    h = stats.heat(rows)
    assert h["max"] == 2 and h["rides"] == 2


def test_week_periods_cross_year():
    assert stats.all_periods({"2025-W52", "2026-W02"}, "week") == ["2025-W52", "2026-W01", "2026-W02"]


def test_day_periods_fill_gaps():
    assert stats.all_periods({"2025-12-30", "2026-01-02"}, "day") == [
        "2025-12-30", "2025-12-31", "2026-01-01", "2026-01-02"]


# ------------------------------------------------------------------ end to end


@pytest.fixture
async def env(tmp_path):
    fake = TestServer(fake_bosch.make_app())
    await fake.start_server()
    fake_bosch.patch_urls(str(fake.make_url("")).rstrip("/"))
    db = DB(tmp_path / "rides.db")
    session = aiohttp.ClientSession()
    mgr = SyncManager(db, session, HABridge(session, False), detail_delay_s=0)
    server = TestServer(build_app(db, mgr))
    await server.start_server()
    client = aiohttp.ClientSession(base_url=str(server.make_url("")))
    yield db, mgr, client, fake
    await client.close()
    await server.close()
    await session.close()
    await fake.close()


async def _login(client, mgr, name, source, code, client_id=None):
    r = await client.post("/api/accounts", json={"name": name, "source": source, "client_id": client_id})
    acc = await r.json()
    r = await client.post(f"/api/accounts/{acc['id']}/login")
    url = (await r.json())["url"]
    state = url.split("state=")[1].split("&")[0]
    pasted = f"{auth.MOBILE_REDIRECT}?state={state}&code={code}"
    r = await client.post(f"/api/accounts/{acc['id']}/login/finish", json={"pasted": pasted})
    assert r.status == 200, await r.text()
    await mgr.idle()
    return acc["id"]


async def test_full_flow_two_accounts(env):
    db, mgr, client, fake = env
    a1 = await _login(client, mgr, "Alex", "mobile", "code-1")
    a2 = await _login(client, mgr, "Sam", "dataact", "code-2", client_id="euda-test")
    await mgr.sync_account(a1)
    await mgr.sync_account(a2)

    state = await (await client.get("/api/state")).json()
    assert {a["status"] for a in state["accounts"]} == {"ok"}
    assert "access_token" not in state["accounts"][0]
    assert state["range"]["n"] == 230 and state["range"]["missing"] == 0
    # account 1 has no bikeId on rides -> attributed by odometer to both bikes
    per_bike = {b["id"]: b["rides"] for b in state["bikes"]}
    assert per_bike["bikeA1"] + per_bike["bikeA2"] == 160 and per_bike["bikeA1"] > 0 and per_bike["bikeA2"] > 0
    assert per_bike["bikeB1"] == 70
    # mobile list fetched with one page of 200
    assert ("list", 0) in fake.app["calls"]

    s = await (await client.get("/api/stats?group=year")).json()
    names = [r["name"] for r in s["series"]["riders"]]
    assert names == ["Alex", "Sam"] and s["series"]["periods"][0] == "2025"
    lb = s["leaderboard"]
    assert lb[0]["name"] == "Alex" and lb[0]["rides"] == 160

    w = await (await client.get(f"/api/stats?group=week&accounts={a2}&from=2025-06-01&to=2025-06-30")).json()
    assert [r["name"] for r in w["series"]["riders"]] == ["Sam"]

    # day view: a fixed 7-day window, days without rides included as 0
    first = min(r["start_ts"] for r in (await (await client.get("/api/rides")).json())["rides"])
    day = stats.local_dt(first).date().isoformat()
    to = (stats.local_dt(first) + timedelta(days=6)).date().isoformat()
    dv = await (await client.get(f"/api/stats?group=day&from={day}&to={to}")).json()
    assert len(dv["series"]["periods"]) == 7 and dv["series"]["periods"][0] == day
    assert sum(sum(r["rides"]) for r in dv["series"]["riders"]) >= 1

    st = await (await client.get("/api/state")).json()
    assert "ha_language" in st

    h = await (await client.get("/api/heat")).json()
    assert h["rides"] == 230 and h["max"] > 50  # the commute corridor glows

    lines = await (await client.get(f"/api/lines?accounts={a1}&bikes=bikeA1")).json()
    assert 0 < len(lines["lines"]) < 160

    rides = (await (await client.get("/api/rides")).json())["rides"]
    one = rides[0]
    g = await client.get(f"/api/rides/{one['account_id']}/{one['id']}/gpx")
    body = await g.text()
    assert g.status == 200 and body.count("<trkpt") > 100

    z = await client.get(f"/api/export.zip?accounts={a2}")
    assert z.status == 200 and len(await z.read()) > 10_000

    # second sync: nothing new, no track refetch
    before = len(fake.app["calls"])
    await mgr.sync_account(a1)
    assert db.pending_tracks(a1) == [] and len(fake.app["calls"]) == before + 1


async def test_expired_refresh_needs_login(env):
    db, mgr, client, _ = env
    aid = await _login(client, mgr, "Guest", "mobile", "code-1")
    db.update_account(aid, access_token=None, expires_at=0, refresh_token="r-unknown.1")
    await mgr.sync_account(aid)
    acc = db.account(aid)
    assert acc["status"] == "login_required"


async def test_change_account_colour(env):
    _, _, client, _ = env
    r = await client.post("/api/accounts", json={"name": "X", "source": "mobile", "color": "#123456"})
    created = await r.json()
    aid = created["id"]
    r = await client.patch(f"/api/accounts/{aid}", json={"color": "#ff8800"})
    assert r.status == 200 and (await r.json())["color"] == "#ff8800"
    acc = (await (await client.get("/api/state")).json())["accounts"][0]
    # a colour change must not touch the login
    assert acc["color"] == "#ff8800" and acc["status"] == created["status"]
    r = await client.patch(f"/api/accounts/{aid}", json={"color": "red; x"})
    assert r.status == 400


async def test_wrong_state_rejected(env):
    _, _, client, _ = env
    r = await client.post("/api/accounts", json={"name": "X", "source": "mobile"})
    aid = (await r.json())["id"]
    await client.post(f"/api/accounts/{aid}/login")
    r = await client.post(f"/api/accounts/{aid}/login/finish",
                          json={"pasted": f"{auth.MOBILE_REDIRECT}?state=nope&code=code-1"})
    assert r.status == 400


async def test_debug_log_samples_are_redacted(env, caplog):
    db, mgr, client, fake = env
    caplog.set_level(logging.DEBUG, logger="app.bosch.client")
    await _login(client, mgr, "Alex", "mobile", "code-1")
    await _login(client, mgr, "Sam", "dataact", "code-2", client_id="euda-test")
    records = [r for r in caplog.records if r.name == "app.bosch.client"]
    text = "\n".join(r.getMessage() for r in records)
    for cls in ("MobileClient", "DataActClient"):
        for endpoint in ("bikes", "activities", "track"):
            assert f"{cls} {endpoint} raw:" in text
        assert f"{cls} first ride parsed:" in text
        assert f"{cls} track parsed:" in text
    # fake data parses cleanly -> no mapping warnings
    assert not [r for r in records if r.levelno >= logging.WARNING]
    # one sample per endpoint per sync, not one per ride
    assert text.count("MobileClient track raw:") == 1
    # nothing that could locate anyone: no precise coordinates, no ride titles
    assert not re.search(r"4[78]\.\d{2,}", text)
    assert "Commute" not in text and "Evening loop" not in text


async def test_unparseable_track_warns_once(env, caplog):
    db, mgr, client, fake = env
    caplog.set_level(logging.WARNING, logger="app.bosch.client")
    fake_bosch.BROKEN_TRACKS = True
    try:
        await _login(client, mgr, "Alex", "mobile", "code-1")
    finally:
        fake_bosch.BROKEN_TRACKS = False
    warnings = [r.getMessage() for r in caplog.records if "no GPS points" in r.getMessage()]
    assert len(warnings) == 1 and "keys:" in warnings[0]


async def test_second_sync_samples_track_and_counts(env, caplog):
    db, mgr, client, fake = env
    a1 = await _login(client, mgr, "Alex", "mobile", "code-1")
    caplog.clear()
    caplog.set_level(logging.DEBUG)
    await mgr.sync_account(a1)  # nothing pending any more
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "0 tracks fetched" in text
    assert "MobileClient track raw:" in text  # sampled although nothing was pending
    assert "Alex: tracks stored: 160 ok" in text
    caplog.clear()
    await mgr.sync_account(a1)
    assert "track raw:" not in "\n".join(r.getMessage() for r in caplog.records)  # once per start


async def test_rides_with_unknown_bike_id_are_reported(env, caplog):
    db, mgr, client, fake = env
    caplog.set_level(logging.DEBUG, logger="app.sync")
    bikes = [{"id": "profile-bike"}, {"id": "second-bike"}]
    rides = [{"bike_id": "ride-bike", "start_ts": 1_700_000_000},
             {"bike_id": "profile-bike", "start_ts": 1_710_000_000}]
    mgr._log_bike_ids("Alex", bikes[:1], rides)  # one bike: re-registered, debug only
    assert not [r for r in caplog.records if r.levelno >= logging.INFO]
    mgr._log_bike_ids("Sam", bikes, rides)
    mgr._log_bike_ids("Sam", bikes, rides)
    msgs = [(r.levelno, r.getMessage()) for r in caplog.records]
    infos = [m for lvl, m in msgs if lvl == logging.INFO]
    assert infos == ["Sam: 1 of 2 rides name a bike id that is not in the bike profile; "
                     "they are attributed by odometer (or to the only bike)"]
    detail = [m for lvl, m in msgs if "bike ids on rides" in m][0]
    assert "ride-bik (not in profile): 1 rides 2023-11-14..2023-11-14" in detail
    assert "profile-: 1 rides" in detail


# ------------------------------------------------------------------ insights

from datetime import date, datetime  # noqa: E402

from app import insights  # noqa: E402


def test_day_and_week_runs():
    d = date(2026, 3, 2)  # a Monday
    days = {d, d + timedelta(1), d + timedelta(2), d + timedelta(10), d + timedelta(11)}
    runs = insights.day_runs(days, today=d + timedelta(12))
    assert runs["longest"] == {"value": 3, "from": "2026-03-02", "to": "2026-03-04"}
    assert runs["current"] == 2  # last ride yesterday still counts
    assert insights.day_runs(days, today=d + timedelta(13))["current"] == 0
    weeks = insights.week_runs(days | {d + timedelta(21)}, today=d + timedelta(22))
    assert weeks["longest"]["value"] == 2 and weeks["current"] == 1  # weeks 1, 2 and 4


def test_records_pick_best_rides():
    accs = [{"id": 1, "name": "Alex", "color": "#000"}]
    base = int(datetime(2026, 5, 4, 8).timestamp())
    rides = [
        {"id": "a", "account_id": 1, "start_ts": base, "distance_m": 30_000, "elevation_gain_m": 100,
         "avg_speed_kmh": 20, "max_speed_kmh": 40, "duration_s": 5400},
        {"id": "b", "account_id": 1, "start_ts": base + 3600, "distance_m": 2_000, "elevation_gain_m": 300,
         "avg_speed_kmh": 30, "max_speed_kmh": 30, "duration_s": 300},
        {"id": "c", "account_id": 1, "start_ts": base + 86400, "distance_m": 10_000, "elevation_gain_m": 0,
         "avg_speed_kmh": 25, "max_speed_kmh": 35, "duration_s": 1500},
    ]
    rec = insights.records(rides, accs, date(2026, 5, 5))[0]["records"]
    assert rec["longest_km"]["id"] == "a" and rec["longest_km"]["value"] == 30
    assert rec["climb_m"]["id"] == "b"
    assert rec["fastest_kmh"]["id"] == "c"  # "b" is faster but shorter than 5 km
    assert rec["best_day_km"] == {"value": 32.0, "key": "2026-05-04"}
    assert rec["day_streak"]["longest"]["value"] == 2 and rec["day_streak"]["current"] == 2


def test_year_curves_and_pace():
    rides = [{"start_ts": int(datetime(y, 1, 10, 12).timestamp()), "distance_m": d}
             for y, d in ((2025, 10_000), (2025, 5_000), (2026, 20_000))]
    rides.append({"start_ts": int(datetime(2025, 6, 1, 12).timestamp()), "distance_m": 7_000})
    c = insights.year_curves(rides, "km", date(2026, 2, 1))
    y26, y25 = c["years"]
    assert (y26["year"], y25["year"]) == (2026, 2025)
    assert y26["values"][31] == 20 and y26["values"][32] is None  # nothing after today
    assert y25["total"] == 22 and len(y25["values"]) == 366
    assert c["pace"] == {"now": 20, "last_year": 15}


async def test_records_explore_years_review_endpoints(env):
    db, mgr, client, fake = env
    a1 = await _login(client, mgr, "Alex", "mobile", "code-1")
    await _login(client, mgr, "Sam", "dataact", "code-2", client_id="euda-test")
    rec = await (await client.get("/api/records")).json()
    assert {r["name"] for r in rec["riders"]} == {"Alex", "Sam"}
    assert rec["riders"][0]["records"]["longest_km"]["value"] > 0

    everything = await (await client.get(f"/api/explore?accounts={a1}")).json()
    assert everything["all_new"] and everything["new"] == everything["total"] > 0
    first, last = db.q("SELECT MIN(start_ts) lo, MAX(start_ts) hi FROM activities WHERE account_id=?", (a1,))[0].values()
    mid = datetime.fromtimestamp((first + last) // 2).date().isoformat()
    later = await (await client.get(f"/api/explore?accounts={a1}&from={mid}")).json()
    assert not later["all_new"] and 0 <= later["new"] < later["total"] == everything["total"]
    assert sum(c[2] for c in later["cells"]) == later["new"]
    assert later["riders"][0]["new_cells"] == later["new"]

    years = await (await client.get("/api/years?metric=rides")).json()
    assert sum(y["values"][365] or y["values"][years["today_doy"] - 1] or 0
               for y in years["years"]) == 230
    assert (await client.get("/api/years?metric=nope")).status == 400

    year = years["years"][0]["year"]
    rv = await (await client.get(f"/api/review?year={year}")).json()
    assert rv["year"] == year and rv["riders"]
    r0 = rv["riders"][0]
    assert abs(sum(r0["months"]) - r0["km"]) < 0.7 and 1 <= r0["top_month"]["month"] <= 12


async def test_sensors_published(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    posted = {}

    async with aiohttp.ClientSession() as session:
        bridge = HABridge(session, True)

        async def fake_post(path, payload):
            posted[path.removeprefix("/states/")] = payload

        bridge._post = fake_post
        totals = {"year_km": 1234.56, "year_rides": 80, "month_km": 99.9, "week_km": 12.3,
                  "total_km": 4000, "days_since_last": 2}
        bikes = [{"id": "b1", "name": "City", "battery_pct": 64.4}, {"id": "b2", "name": "MTB", "battery_pct": None}]
        await bridge.publish_rider("Alex", totals, {"distance_m": 5000, "start_iso": "x"}, bikes)
    assert posted["sensor.ebike_alex_km_this_week"]["state"] == 12.3
    assert posted["sensor.ebike_alex_km_this_month"]["state"] == 99.9
    assert posted["sensor.ebike_alex_rides_this_year"]["state"] == 80
    days = posted["sensor.ebike_alex_days_since_last_ride"]
    assert days["state"] == 2 and days["attributes"]["device_class"] == "duration"
    assert posted["sensor.ebike_alex_city_battery"]["state"] == 64
    assert "sensor.ebike_alex_mtb_battery" not in posted
    assert posted["sensor.ebike_alex_km_this_year"]["attributes"]["rides_this_year"] == 80
