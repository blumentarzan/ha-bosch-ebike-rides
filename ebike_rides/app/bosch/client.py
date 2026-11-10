"""Bosch eBike cloud clients, normalised to one data model.

``MobileClient``  - eBike Flow app API (one-bike-app token), works for all accounts.
``DataActClient`` - official EU Data Act API (euda-* token).

Both return the same plain dicts (see ``normalize_*``), so the sync layer and
the database never see which API a ride came from. All parsing is defensive:
Bosch has moved fields between flat and ``attributes``-wrapped shapes before.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import aiohttp

_LOGGER = logging.getLogger(__name__)

MOBILE_PROFILE_BASE = "https://obc-rider-profile.prod.connected-biking.cloud"
MOBILE_ACTIVITY_BASE = "https://obc-rider-activity.prod.connected-biking.cloud"
DATAACT_BASE = "https://api.bosch-ebike.com"

TokenProvider = Callable[[bool], Awaitable[str]]  # force_refresh -> access token


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class AuthExpired(ApiError):
    pass


# ----------------------------------------------------------------- helpers


def _attrs(obj: Any) -> dict:
    if not isinstance(obj, dict):
        return {}
    inner = obj.get("attributes")
    return inner if isinstance(inner, dict) else obj


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):  # Data Act: {"average": x, "maximum": y} etc.
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first(*values: float | None) -> float | None:
    for v in values:
        if v is not None:
            return v
    return None


def _sub(obj: dict, key: str, sub: str) -> float | None:
    v = obj.get(key)
    return _num(v.get(sub)) if isinstance(v, dict) else None


def to_epoch(value: Any) -> int | None:
    """Epoch seconds from epoch s/ms or an ISO string."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value / 1000) if value > 10_000_000_000 else int(value)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    return None


# ----------------------------------------------------------- diagnostics

# Keys whose values never belong in a log (matched as substrings, lower case).
_SECRET_KEYS = (
    "token", "secret", "password", "email", "phone", "serial", "vin", "frame",
    "firstname", "lastname", "username", "address", "street", "city", "zip",
    "title", "description", "note",
)
# Keys redacted only on an exact match (``brandName``/``modelName`` stay).
_SECRET_EXACT = {"name", "nickname", "displayname"}
# Keys holding positions: numbers below them are rounded to ~10 km.
_COORD_KEYS = ("lat", "lon", "lng", "coord", "position", "location", "geometry")


def _round_coords(obj: Any) -> Any:
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return round(obj, 1)
    if isinstance(obj, list):
        return [_round_coords(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _round_coords(v) for k, v in obj.items()}
    return obj


def redact(obj: Any, list_items: int = 2, _depth: int = 0) -> Any:
    """Copy of an API answer that is safe to paste into a public issue.

    Keeps every key and the type/unit of every value (that is what we need to
    check the field mapping), but drops secrets and personal text, rounds
    positions to one decimal and shortens lists to their first items.
    """
    if _depth > 10:
        return "..."
    if isinstance(obj, dict):
        out: dict = {}
        for k, v in obj.items():
            kl = str(k).lower()
            secret = kl in _SECRET_EXACT or any(s in kl for s in _SECRET_KEYS)
            if secret and not isinstance(v, (dict, list)):
                out[k] = "<redacted>" if v not in (None, "") else v
            elif any(s in kl for s in _COORD_KEYS):
                out[k] = redact(_round_coords(v), list_items, _depth + 1)
            else:
                out[k] = redact(v, list_items, _depth + 1)
        return out
    if isinstance(obj, list):
        items = [redact(x, list_items, _depth + 1) for x in obj[:list_items]]
        if len(obj) > list_items:
            items.append(f"... {len(obj) - list_items} more")
        return items
    if isinstance(obj, str) and "@" in obj:
        return "<redacted>"
    return obj


def _dump(obj: Any, limit: int = 4000) -> str:
    import json

    text = json.dumps(redact(obj), default=str, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f"... ({len(text)} chars)"


def _median(values: list[float]) -> float | None:
    vals = sorted(v for v in values if v)
    return vals[len(vals) // 2] if vals else None


def check_activities(raw_count: int, rides: list[dict]) -> list[str]:
    """Plausibility problems of a parsed ride list (hints for a wrong mapping)."""
    problems: list[str] = []
    if raw_count and not rides:
        problems.append(f"none of {raw_count} rides could be parsed (no id or startTime)")
        return problems
    if raw_count > len(rides):
        problems.append(f"{raw_count - len(rides)} of {raw_count} rides skipped (no id or startTime)")
    if not rides:
        return problems
    for field, label in (
        ("distance_m", "distance"), ("duration_s", "duration"),
        ("elevation_gain_m", "elevation gain"), ("avg_speed_kmh", "average speed"),
        ("start_odometer_m", "start odometer"),
    ):
        missing = sum(1 for r in rides if not r.get(field))
        if missing == len(rides):
            problems.append(f"{label} missing in all {len(rides)} rides")
        elif field in ("distance_m", "duration_s") and missing:
            problems.append(f"{label} missing in {missing} of {len(rides)} rides")
    dist = _median([r.get("distance_m") or 0 for r in rides])
    if dist is not None and dist < 100:
        problems.append(f"median distance {dist:g} m looks like km, not m")
    speed = _median([r.get("avg_speed_kmh") or 0 for r in rides])
    if speed is not None and (speed < 8 or speed > 60):
        problems.append(f"median average speed {speed:g} km/h is implausible (unit?)")
    return problems


def normalize_bike(raw: dict) -> dict | None:
    a = _attrs(raw)
    bike_id = raw.get("id") or raw.get("bikeId") or a.get("id") or a.get("bikeId")
    if not isinstance(bike_id, str) or not bike_id:
        return None
    du = a.get("driveUnit") if isinstance(a.get("driveUnit"), dict) else {}
    name = a.get("name") or a.get("bikeName") or " ".join(
        x for x in (a.get("brandName"), a.get("modelName")) if x
    )
    batteries = a.get("batteries") if isinstance(a.get("batteries"), list) else []
    levels = [_num(b.get("batteryLevel")) for b in batteries if isinstance(b, dict)]
    levels = [v for v in levels if v is not None]
    return {
        "id": bike_id,
        "name": name or "eBike",
        # null in the profile unless the bike reported recently (seen 2026-11)
        "battery_pct": levels[0] if levels else None,
        # Mobile API (seen 2026-11): driveUnit.totalDistanceTraveled in metres.
        "odometer_m": _first(
            _num(du.get("totalDistanceTraveled")), _num(du.get("odometer")),
            _num(a.get("totalDistanceTraveled")), _num(a.get("odometer")),
        ),
    }


def normalize_activity(raw: dict) -> dict | None:
    a = _attrs(raw)
    act_id = raw.get("id") or a.get("id")
    start = to_epoch(a.get("startTime"))
    if not act_id or start is None:
        return None
    end = to_epoch(a.get("endTime"))
    return {
        "id": str(act_id),
        "bike_id": a.get("bikeId") or raw.get("bikeId"),
        "start_ts": start,
        "end_ts": end,
        "tz": a.get("timeZoneOfActivity") or a.get("timeZone"),
        "title": a.get("title") or None,
        "distance_m": _num(a.get("distance")) or 0.0,
        "duration_s": _num(a.get("durationWithoutStops"))
        or (float(end - start) if end else None),
        "elevation_gain_m": _first(_num(a.get("elevationGain")), _sub(a, "elevation", "gain")),
        "elevation_loss_m": _first(_num(a.get("elevationLoss")), _sub(a, "elevation", "loss")),
        "avg_speed_kmh": _first(_num(a.get("averageSpeed")), _sub(a, "speed", "average")),
        "max_speed_kmh": _first(_num(a.get("maximumSpeed")), _sub(a, "speed", "maximum")),
        "avg_power_w": _first(_num(a.get("averageRiderPower")), _sub(a, "riderPower", "average")),
        "avg_cadence": _first(_num(a.get("averageCadence")), _sub(a, "cadence", "average")),
        "calories": _num(a.get("caloriesBurnt")),
        "start_odometer_m": _num(a.get("startOdometer")),
    }


def normalize_track(raw: Any) -> list[list[float | None]]:
    """[[lat, lon, ele, speed_kmh], ...] from either API's detail answer."""
    points: Any = None
    if isinstance(raw, dict):
        data = raw.get("data")
        points = _attrs(data).get("activityData") if isinstance(data, dict) else None
        if points is None:
            points = raw.get("activityDetails") or raw.get("activityData")
    elif isinstance(raw, list):
        points = raw
    out: list[list[float | None]] = []
    for p in points or []:
        if not isinstance(p, dict):
            continue
        lat = _num(p.get("lat", p.get("latitude")))
        lon = _num(p.get("lon", p.get("longitude")))
        if lat is None or lon is None or (lat == 0 and lon == 0):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        ele = _num(p.get("h", p.get("altitude", p.get("elevation"))))
        spd = _num(p.get("v", p.get("speed")))
        out.append([round(lat, 6), round(lon, 6), ele, spd])
    return out


# ----------------------------------------------------------------- clients


class _BaseClient:
    def __init__(self, session: aiohttp.ClientSession, token: TokenProvider) -> None:
        self._session = session
        self._token = token
        self._sampled: set[str] = set()

    def _sample(self, endpoint: str, resp: Any) -> None:
        """Log one redacted raw answer per endpoint and sync (debug level)."""
        if endpoint in self._sampled or not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        self._sampled.add(endpoint)
        _LOGGER.debug("%s %s raw: %s", type(self).__name__, endpoint, _dump(resp))

    def _check_bikes(self, raw: list, bikes: list[dict]) -> list[dict]:
        if raw and len(bikes) < len(raw):
            _LOGGER.warning(
                "%s: %d of %d bikes could not be parsed (no id) - enable debug log "
                "and report the 'bikes raw' line", type(self).__name__, len(raw) - len(bikes), len(raw),
            )
        _LOGGER.debug("%s bikes parsed: %s", type(self).__name__, _dump(bikes))
        return bikes

    def _check_rides(self, raw: list, rides: list[dict]) -> list[dict]:
        for problem in check_activities(len(raw), rides):
            _LOGGER.warning(
                "%s: %s - enable debug log and report the 'activities raw' line",
                type(self).__name__, problem,
            )
        if rides:
            _LOGGER.debug("%s first ride parsed: %s", type(self).__name__, _dump(rides[0]))
        return rides

    def _check_track(self, activity_id: str, resp: Any, points: list) -> list:
        self._sample("track", resp)
        if resp and not points and "track-empty" not in self._sampled:
            self._sampled.add("track-empty")
            keys = sorted(resp) if isinstance(resp, dict) else type(resp).__name__
            _LOGGER.warning(
                "%s: ride %s has a detail answer but no GPS points were found (keys: %s) "
                "- enable debug log and report the 'track raw' line",
                type(self).__name__, activity_id, keys,
            )
        elif points and "track-parsed" not in self._sampled:
            self._sampled.add("track-parsed")
            _LOGGER.debug(
                "%s track parsed: %d points, first [lat, lon, ele, speed] = %s",
                type(self).__name__, len(points), _round_coords(points[0]),
            )
        return points

    async def _get(self, url: str) -> Any:
        for attempt in range(4):
            force = attempt == 1
            headers = {
                "Authorization": f"Bearer {await self._token(force)}",
                "Accept": "application/json",
            }
            try:
                async with self._session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 401:
                        if attempt == 0:
                            continue  # refresh once and retry
                        raise AuthExpired(401, "token rejected - log in again")
                    if resp.status == 404:
                        return None
                    if resp.status in (429, 502, 503, 504) and attempt < 3:
                        await asyncio.sleep(2 ** (attempt + 1))
                        continue
                    if resp.status >= 400:
                        raise ApiError(resp.status, (await resp.text())[:300])
                    text = await resp.text()
                    return await asyncio.to_thread(_json, text)
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as err:
                if attempt >= 3:
                    raise ApiError(0, f"network: {err}") from err
                await asyncio.sleep(2 ** (attempt + 1))
        raise ApiError(0, "gave up after retries")

    async def bikes(self) -> list[dict]:
        raise NotImplementedError

    async def activities(self) -> list[dict]:
        raise NotImplementedError

    async def track(self, activity_id: str) -> list[list[float | None]]:
        raise NotImplementedError


def _json(text: str) -> Any:
    import json

    return json.loads(text) if text.strip() else {}


def _bike_list(resp: Any) -> list:
    if isinstance(resp, list):
        return resp
    if not isinstance(resp, dict):
        return []
    if isinstance(resp.get("bikes"), list):
        return resp["bikes"]
    data = resp.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        inner = _attrs(data).get("bikes")
        return inner if isinstance(inner, list) else []
    return []


class MobileClient(_BaseClient):
    PAGE = 200

    async def bikes(self) -> list[dict]:
        resp = await self._get(f"{MOBILE_PROFILE_BASE}/v1/bike-profile")
        self._sample("bikes", resp)
        raw = _bike_list(resp)
        return self._check_bikes(raw, [b for b in (normalize_bike(x) for x in raw) if b])

    async def activities(self) -> list[dict]:
        items: list = []
        page = 0
        while True:
            q = f"?size={self.PAGE}" + (f"&page={page}" if page else "")
            resp = await self._get(f"{MOBILE_ACTIVITY_BASE}/v1/activity{q}")
            self._sample("activities", resp)
            if not isinstance(resp, dict):
                break
            batch = resp.get("data") or []
            items.extend(batch)
            pages = (resp.get("meta") or {}).get("pages") or 1
            page += 1
            if page >= pages or not batch or page > 200:
                break
        return self._check_rides(items, [a for a in (normalize_activity(x) for x in items) if a])

    async def track(self, activity_id: str) -> list[list[float | None]]:
        resp = await self._get(f"{MOBILE_ACTIVITY_BASE}/v1/activity/{activity_id}/detail")
        return self._check_track(activity_id, resp, normalize_track(resp))


class DataActClient(_BaseClient):
    PAGE = 50

    async def bikes(self) -> list[dict]:
        resp = await self._get(f"{DATAACT_BASE}/bike-profile/smart-system/v1/bikes")
        self._sample("bikes", resp)
        raw = _bike_list(resp)
        return self._check_bikes(raw, [b for b in (normalize_bike(x) for x in raw) if b])

    async def activities(self) -> list[dict]:
        items: list = []
        offset = 0
        while True:
            resp = await self._get(
                f"{DATAACT_BASE}/activity/smart-system/v1/activities"
                f"?limit={self.PAGE}&offset={offset}&sort=-startTime"
            )
            self._sample("activities", resp)
            if not isinstance(resp, dict):
                break
            batch = resp.get("activitySummaries") or []
            items.extend(batch)
            total = (resp.get("pagination") or {}).get("total") or 0
            offset += self.PAGE
            if not batch or offset >= total or offset > 20_000:
                break
        return self._check_rides(items, [a for a in (normalize_activity(x) for x in items) if a])

    async def track(self, activity_id: str) -> list[list[float | None]]:
        resp = await self._get(
            f"{DATAACT_BASE}/activity/smart-system/v1/activities/{activity_id}/details"
        )
        return self._check_track(activity_id, resp, normalize_track(resp))


def make_client(source: str, session: aiohttp.ClientSession, token: TokenProvider) -> _BaseClient:
    return (DataActClient if source == "dataact" else MobileClient)(session, token)
