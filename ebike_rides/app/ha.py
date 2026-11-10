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

    async def publish_rider(self, name: str, totals: dict, last: dict | None) -> None:
        base = f"sensor.ebike_{slug(name)}"
        await self._post(
            f"/states/{base}_km_this_year",
            {
                "state": round(totals.get("year_km", 0), 1),
                "attributes": {
                    "unit_of_measurement": "km",
                    "device_class": "distance",
                    "state_class": "total_increasing",
                    "icon": "mdi:bike",
                    "friendly_name": f"{name} eBike km this year",
                    "rides_this_year": totals.get("year_rides", 0),
                    "km_this_month": round(totals.get("month_km", 0), 1),
                    "km_this_week": round(totals.get("week_km", 0), 1),
                    "km_total": round(totals.get("total_km", 0), 1),
                },
            },
        )
        if last:
            await self._post(
                f"/states/{base}_last_ride",
                {
                    "state": round((last.get("distance_m") or 0) / 1000, 1),
                    "attributes": {
                        "unit_of_measurement": "km",
                        "icon": "mdi:map-marker-path",
                        "friendly_name": f"{name} eBike last ride",
                        "start": last.get("start_iso"),
                        "title": last.get("title"),
                        "duration_min": round((last.get("duration_s") or 0) / 60),
                        "elevation_gain_m": last.get("elevation_gain_m"),
                    },
                },
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
