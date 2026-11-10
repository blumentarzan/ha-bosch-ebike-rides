"""Optional bridge to Home Assistant Core (sensors + an event per new ride).

Uses the Supervisor proxy, so it only works inside the add-on
(``homeassistant_api: true``). Outside, it silently does nothing.
"""

from __future__ import annotations

import logging
import os
import re

import aiohttp

_LOGGER = logging.getLogger(__name__)
CORE = "http://supervisor/core/api"


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower().translate(str.maketrans("äöüß", "aous")))
    return s.strip("_") or "rider"


class HABridge:
    def __init__(self, session: aiohttp.ClientSession, enabled: bool) -> None:
        self._session = session
        self._token = os.environ.get("SUPERVISOR_TOKEN")
        self.enabled = enabled and bool(self._token)
        self._language: str | None = None

    async def language(self) -> str | None:
        """HA's configured language (e.g. "de", "en-GB"); UI fallback when the
        frontend cannot read the user's own setting. Cached after first success."""
        if self._language or not self._token:
            return self._language
        try:
            async with self._session.get(
                f"{CORE}/config",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status < 400:
                    self._language = (await resp.json()).get("language")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("HA language: %s", err)
        return self._language

    async def _post(self, path: str, payload: dict) -> None:
        if not self.enabled:
            return
        try:
            async with self._session.post(
                f"{CORE}{path}",
                json=payload,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status >= 400:
                    _LOGGER.debug("HA %s -> %s", path, resp.status)
        except Exception as err:  # noqa: BLE001 - HA being down must not break syncing
            _LOGGER.debug("HA bridge: %s", err)

    async def _sensor(self, entity: str, state, unit: str | None, name: str, icon: str,
                      device_class: str | None = None, state_class: str | None = None,
                      **attrs) -> None:
        attributes = {"friendly_name": name, "icon": icon, **attrs}
        if unit:
            attributes["unit_of_measurement"] = unit
        if device_class:
            attributes["device_class"] = device_class
        if state_class:
            attributes["state_class"] = state_class
        await self._post(f"/states/{entity}", {"state": state, "attributes": attributes})

    async def publish_rider(self, name: str, totals: dict, last: dict | None,
                            bikes: list[dict] | None = None) -> None:
        base = f"sensor.ebike_{slug(name)}"
        km = lambda k: round(totals.get(k, 0), 1)  # noqa: E731
        await self._sensor(
            f"{base}_km_this_year", km("year_km"), "km", f"{name} eBike km this year", "mdi:bike",
            "distance", "total_increasing",
            rides_this_year=totals.get("year_rides", 0), km_this_month=km("month_km"),
            km_this_week=km("week_km"), km_total=km("total_km"),
        )
        await self._sensor(f"{base}_km_this_month", km("month_km"), "km",
                           f"{name} eBike km this month", "mdi:calendar-month",
                           "distance", "total_increasing")
        await self._sensor(f"{base}_km_this_week", km("week_km"), "km",
                           f"{name} eBike km this week", "mdi:calendar-week",
                           "distance", "total_increasing")
        await self._sensor(f"{base}_rides_this_year", totals.get("year_rides", 0), "rides",
                           f"{name} eBike rides this year", "mdi:counter",
                           None, "total_increasing")
        if totals.get("days_since_last") is not None:
            await self._sensor(f"{base}_days_since_last_ride", totals["days_since_last"], "d",
                               f"{name} eBike days since last ride", "mdi:calendar-clock",
                               "duration", "measurement")
        for bike in bikes or []:
            if bike.get("battery_pct") is None:
                continue
            await self._sensor(
                f"{base}_{slug(bike.get('name') or bike['id'][:8])}_battery",
                round(bike["battery_pct"]), "%", f"{name} {bike.get('name') or 'eBike'} battery",
                "mdi:battery", "battery", "measurement",
            )
        if last:
            await self._sensor(
                f"{base}_last_ride", round((last.get("distance_m") or 0) / 1000, 1), "km",
                f"{name} eBike last ride", "mdi:map-marker-path",
                start=last.get("start_iso"), title=last.get("title"),
                duration_min=round((last.get("duration_s") or 0) / 60),
                elevation_gain_m=last.get("elevation_gain_m"),
            )

    async def new_ride(self, rider: str, ride: dict) -> None:
        await self._post(
            "/events/ebike_rides_new_activity",
            {
                "rider": rider,
                "activity_id": ride["id"],
                "distance_km": round((ride.get("distance_m") or 0) / 1000, 2),
                "start_ts": ride.get("start_ts"),
                "title": ride.get("title"),
            },
        )
