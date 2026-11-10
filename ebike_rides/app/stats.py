"""Aggregations for the dashboard: per rider by year / month / week."""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .geo import cell_center, decode_cells

DEFAULT_TZ = os.environ.get("TZ") or "UTC"


def _zone(name: str | None) -> ZoneInfo:
    for candidate in (name, DEFAULT_TZ, "UTC"):
        if candidate:
            try:
                return ZoneInfo(candidate)
            except Exception:  # noqa: BLE001
                continue
    return ZoneInfo("UTC")


def local_dt(ts: int, tz: str | None = None) -> datetime:
    return datetime.fromtimestamp(ts, _zone(tz))


def period_key(ts: int, tz: str | None, group: str) -> str:
    d = local_dt(ts, tz)
    if group == "day":
        return d.date().isoformat()
    if group == "year":
        return f"{d.year}"
    if group == "month":
        return f"{d.year}-{d.month:02d}"
    if group == "week":
        iso = d.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if group == "weekday":
        return str(d.isoweekday())
    if group == "hour":
        return f"{d.hour:02d}"
    raise ValueError(group)


def day_range(first: date, last: date) -> list[str]:
    out = []
    d = first
    while d <= last and len(out) < 3700:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def all_periods(keys: set[str], group: str) -> list[str]:
    """Fill gaps so a month without rides still shows as 0."""
    if not keys:
        return []
    if group == "day":
        return day_range(date.fromisoformat(min(keys)), date.fromisoformat(max(keys)))
    if group == "weekday":
        return [str(i) for i in range(1, 8)]
    if group == "hour":
        return [f"{h:02d}" for h in range(24)]
    lo, hi = min(keys), max(keys)
    if group == "year":
        return [str(y) for y in range(int(lo), int(hi) + 1)]
    out = []
    if group == "month":
        y, m = map(int, lo.split("-"))
        while f"{y}-{m:02d}" <= hi:
            out.append(f"{y}-{m:02d}")
            m += 1
            if m == 13:
                y, m = y + 1, 1
        return out
    # weeks
    y, w = lo.split("-W")
    d = datetime.fromisocalendar(int(y), int(w), 1)
    while True:
        iso = d.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        if key > hi:
            break
        out.append(key)
        d += timedelta(days=7)
        if len(out) > 1000:
            break
    return out


METRICS = {
    "km": lambda r: (r.get("distance_m") or 0) / 1000,
    "rides": lambda r: 1,
    "hours": lambda r: (r.get("duration_s") or 0) / 3600,
    "elevation": lambda r: r.get("elevation_gain_m") or 0,
}


def series(
    rides: list[dict], accounts: list[dict], group: str, span: list[str] | None = None
) -> dict:
    """{periods: [...], riders: [{id,name,color, km:[...], rides:[...], ...}]}

    ``span`` fixes the periods (e.g. the 7 days of the day view) so days
    without rides at either end still show up as 0."""
    buckets: dict[int, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(float))
    )
    keys: set[str] = set()
    for r in rides:
        k = period_key(r["start_ts"], r.get("tz"), group)
        keys.add(k)
        b = buckets[r["account_id"]][k]
        for name, fn in METRICS.items():
            b[name] += fn(r)
    periods = span if span is not None else all_periods(keys, group)
    riders = []
    for acc in accounts:
        data = buckets.get(acc["id"], {})
        if not data and rides:
            continue
        entry = {"id": acc["id"], "name": acc["name"], "color": acc["color"]}
        for name in METRICS:
            entry[name] = [round(data.get(p, {}).get(name, 0), 2) for p in periods]
        riders.append(entry)
    return {"group": group, "periods": periods, "riders": riders}


def leaderboard(rides: list[dict], accounts: list[dict]) -> list[dict]:
    per: dict[int, dict] = {}
    for r in rides:
        p = per.setdefault(
            r["account_id"],
            {"km": 0.0, "rides": 0, "hours": 0.0, "elevation": 0.0, "longest_km": 0.0,
             "max_speed": 0.0, "days": set()},
        )
        km = (r.get("distance_m") or 0) / 1000
        p["km"] += km
        p["rides"] += 1
        p["hours"] += (r.get("duration_s") or 0) / 3600
        p["elevation"] += r.get("elevation_gain_m") or 0
        p["longest_km"] = max(p["longest_km"], km)
        p["max_speed"] = max(p["max_speed"], r.get("max_speed_kmh") or 0)
        p["days"].add(local_dt(r["start_ts"], r.get("tz")).date())
    out = []
    for acc in accounts:
        p = per.get(acc["id"])
        if not p:
            continue
        out.append(
            {
                "id": acc["id"], "name": acc["name"], "color": acc["color"],
                "km": round(p["km"], 1), "rides": p["rides"],
                "hours": round(p["hours"], 1), "elevation": round(p["elevation"]),
                "longest_km": round(p["longest_km"], 1),
                "max_speed": round(p["max_speed"], 1),
                "avg_km": round(p["km"] / p["rides"], 1) if p["rides"] else 0,
                "riding_days": len(p["days"]),
                "streak_weeks": _week_streak(p["days"]),
            }
        )
    out.sort(key=lambda e: e["km"], reverse=True)
    return out


def _week_streak(days: set) -> int:
    """Longest run of consecutive ISO weeks with at least one ride."""
    weeks = sorted({(d.isocalendar().year, d.isocalendar().week) for d in days})
    best = run = 0
    prev = None
    for y, w in weeks:
        monday = datetime.fromisocalendar(y, w, 1)
        if prev is not None and (monday - prev).days == 7:
            run += 1
        else:
            run = 1
        best = max(best, run)
        prev = monday
    return best


def heat(rows: list[dict], max_cells: int = 60000) -> dict:
    """Count rides per grid cell. rows carry the encoded cell list as 'geo'."""
    counts: dict[tuple[int, int], int] = defaultdict(int)
    for r in rows:
        for key in decode_cells(r.get("geo")):
            counts[key] += 1
    items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:max_cells]
    cells = [[*cell_center(k), c] for k, c in items]
    return {"cells": cells, "max": items[0][1] if items else 0, "rides": len(rows)}


def rider_totals(rides: list[dict], now_ts: int) -> dict:
    now = local_dt(now_ts)
    iso = now.isocalendar()
    t = {"year_km": 0.0, "year_rides": 0, "month_km": 0.0, "week_km": 0.0, "total_km": 0.0}
    for r in rides:
        d = local_dt(r["start_ts"], r.get("tz"))
        km = (r.get("distance_m") or 0) / 1000
        t["total_km"] += km
        if d.year == now.year:
            t["year_km"] += km
            t["year_rides"] += 1
            if d.month == now.month:
                t["month_km"] += km
        di = d.isocalendar()
        if (di.year, di.week) == (iso.year, iso.week):
            t["week_km"] += km
    return t
