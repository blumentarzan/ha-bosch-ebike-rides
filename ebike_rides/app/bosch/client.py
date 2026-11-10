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


def normalize_bike(raw: dict) -> dict | None:
    a = _attrs(raw)
    bike_id = raw.get("id") or raw.get("bikeId") or a.get("id") or a.get("bikeId")
    if not isinstance(bike_id, str) or not bike_id:
        return None
    du = a.get("driveUnit") if isinstance(a.get("driveUnit"), dict) else {}
    name = a.get("name") or a.get("bikeName") or " ".join(
        x for x in (a.get("brandName"), a.get("modelName")) if x
    )
    return {
        "id": bike_id,
        "name": name or "eBike",
        "odometer_m": _first(_num(du.get("odometer")), _num(a.get("odometer"))),
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
        return [b for b in (normalize_bike(x) for x in _bike_list(resp)) if b]

    async def activities(self) -> list[dict]:
        items: list = []
        page = 0
        while True:
            q = f"?size={self.PAGE}" + (f"&page={page}" if page else "")
            resp = await self._get(f"{MOBILE_ACTIVITY_BASE}/v1/activity{q}")
            if not isinstance(resp, dict):
                break
            batch = resp.get("data") or []
            items.extend(batch)
            pages = (resp.get("meta") or {}).get("pages") or 1
            page += 1
            if page >= pages or not batch or page > 200:
                break
        return [a for a in (normalize_activity(x) for x in items) if a]

    async def track(self, activity_id: str) -> list[list[float | None]]:
        resp = await self._get(f"{MOBILE_ACTIVITY_BASE}/v1/activity/{activity_id}/detail")
        return normalize_track(resp)


class DataActClient(_BaseClient):
    PAGE = 50

    async def bikes(self) -> list[dict]:
        resp = await self._get(f"{DATAACT_BASE}/bike-profile/smart-system/v1/bikes")
        return [b for b in (normalize_bike(x) for x in _bike_list(resp)) if b]

    async def activities(self) -> list[dict]:
        items: list = []
        offset = 0
        while True:
            resp = await self._get(
                f"{DATAACT_BASE}/activity/smart-system/v1/activities"
                f"?limit={self.PAGE}&offset={offset}&sort=-startTime"
            )
            if not isinstance(resp, dict):
                break
            batch = resp.get("activitySummaries") or []
            items.extend(batch)
            total = (resp.get("pagination") or {}).get("total") or 0
            offset += self.PAGE
            if not batch or offset >= total or offset > 20_000:
                break
        return [a for a in (normalize_activity(x) for x in items) if a]

    async def track(self, activity_id: str) -> list[list[float | None]]:
        resp = await self._get(
            f"{DATAACT_BASE}/activity/smart-system/v1/activities/{activity_id}/details"
        )
        return normalize_track(resp)


def make_client(source: str, session: aiohttp.ClientSession, token: TokenProvider) -> _BaseClient:
    return (DataActClient if source == "dataact" else MobileClient)(session, token)
