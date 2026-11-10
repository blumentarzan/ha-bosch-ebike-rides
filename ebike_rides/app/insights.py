"""Records & streaks, explorer (first-ridden cells), year comparison and review.

All functions take plain ride dicts as returned by ``DB.filtered`` (sorted by
``start_ts``) and the account list, and return JSON-ready dicts.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .geo import cell_center, decode_cells
from .stats import local_dt

FASTEST_MIN_KM = 5.0  # an "average speed" record needs a real ride, not a test spin
CELL_KM = 0.025       # one heatmap cell ~ 25 m of road


def _km(r: dict) -> float:
    return (r.get("distance_m") or 0) / 1000


def _day(r: dict) -> date:
    return local_dt(r["start_ts"], r.get("tz")).date()


def _ride_ref(r: dict, value: float) -> dict:
    return {"value": round(value, 1), "id": r["id"], "account_id": r["account_id"], "ts": r["start_ts"]}


# ---------------------------------------------------------------- streaks


def day_runs(days: set[date], today: date) -> dict:
    """Longest and current run of consecutive riding days.

    The current run still counts when the last ride was yesterday (today is
    not over yet)."""
    best = {"value": 0, "from": None, "to": None}
    run_start = prev = None
    run = 0
    for d in sorted(days):
        if prev is not None and (d - prev).days == 1:
            run += 1
        else:
            run, run_start = 1, d
        if run > best["value"]:
            best = {"value": run, "from": run_start.isoformat(), "to": d.isoformat()}
        prev = d
    current = run if prev is not None and (today - prev).days <= 1 else 0
    return {"longest": best, "current": current}


def week_runs(days: set[date], today: date) -> dict:
    """Longest and current run of consecutive ISO weeks with at least one ride."""
    mondays = sorted({d - timedelta(days=d.weekday()) for d in days})
    best = {"value": 0, "from": None, "to": None}
    run_start = prev = None
    run = 0
    for m in mondays:
        if prev is not None and (m - prev).days == 7:
            run += 1
        else:
            run, run_start = 1, m
        if run > best["value"]:
            best = {"value": run, "from": run_start.isoformat(), "to": m.isoformat()}
        prev = m
    this_monday = today - timedelta(days=today.weekday())
    current = run if prev is not None and (this_monday - prev).days <= 7 else 0
    return {"longest": best, "current": current}


# ---------------------------------------------------------------- records


def records(rides: list[dict], accounts: list[dict], today: date) -> list[dict]:
    """Per rider: best single rides, best day/week/month and streaks."""
    per: dict[int, list[dict]] = defaultdict(list)
    for r in rides:
        per[r["account_id"]].append(r)
    out = []
    for acc in accounts:
        rs = per.get(acc["id"])
        if not rs:
            continue
        rec: dict = {}

        def best(key: str, fn, pool=rs):
            cand = [(fn(r), r) for r in pool]
            cand = [(v, r) for v, r in cand if v]
            if cand:
                v, r = max(cand, key=lambda x: x[0])
                rec[key] = _ride_ref(r, v)

        best("longest_km", _km)
        best("climb_m", lambda r: r.get("elevation_gain_m") or 0)
        best("fastest_kmh", lambda r: r.get("avg_speed_kmh") or 0,
             [r for r in rs if _km(r) >= FASTEST_MIN_KM])
        best("top_speed_kmh", lambda r: r.get("max_speed_kmh") or 0)
        best("longest_min", lambda r: (r.get("duration_s") or 0) / 60)

        by_day: dict[date, float] = defaultdict(float)
        by_week: dict[date, float] = defaultdict(float)
        by_month: dict[str, float] = defaultdict(float)
        for r in rs:
            d = _day(r)
            by_day[d] += _km(r)
            by_week[d - timedelta(days=d.weekday())] += _km(r)
            by_month[f"{d.year}-{d.month:02d}"] += _km(r)
        d, v = max(by_day.items(), key=lambda kv: kv[1])
        rec["best_day_km"] = {"value": round(v, 1), "key": d.isoformat()}
        d, v = max(by_week.items(), key=lambda kv: kv[1])
        rec["best_week_km"] = {"value": round(v, 1), "key": d.isoformat()}
        m, v = max(by_month.items(), key=lambda kv: kv[1])
        rec["best_month_km"] = {"value": round(v, 1), "key": m}

        days = set(by_day)
        rec["day_streak"] = day_runs(days, today)
        rec["week_streak"] = week_runs(days, today)
        rec["first_ts"] = rs[0]["start_ts"]
        rec["last_ts"] = rs[-1]["start_ts"]
        rec["riding_days"] = len(days)
        out.append({"id": acc["id"], "name": acc["name"], "color": acc["color"], "records": rec})
    return out


# ---------------------------------------------------------------- explorer


def explore(history: list[dict], accounts: list[dict], start: int | None,
            max_cells: int = 60000) -> dict:
    """Cells ridden for the first time at or after ``start``.

    ``history`` holds every ride up to the end of the period, with its encoded
    cells as ``geo``, sorted by time. "New" means nobody of the selected
    riders had been there before (map) or that rider had not (per rider)."""
    first_any: dict[tuple[int, int], int] = {}
    first_rider: dict[int, dict[tuple[int, int], int]] = defaultdict(dict)
    for r in history:
        ts = r["start_ts"]
        mine = first_rider[r["account_id"]]
        for key in decode_cells(r.get("geo")):
            first_any.setdefault(key, ts)
            mine.setdefault(key, ts)

    def is_new(ts: int) -> bool:
        return start is None or ts >= start

    riders = []
    for acc in accounts:
        mine = first_rider.get(acc["id"])
        if not mine:
            continue
        new = sum(1 for ts in mine.values() if is_new(ts))
        riders.append({
            "id": acc["id"], "name": acc["name"], "color": acc["color"],
            "cells": len(mine), "new_cells": new,
            "km": round(len(mine) * CELL_KM, 1), "new_km": round(new * CELL_KM, 1),
        })
    items = list(first_any.items()) if max_cells else []
    if len(items) > max_cells:  # keep all new cells, thin out the known ones
        new_items = [kv for kv in items if is_new(kv[1])]
        old_items = [kv for kv in items if not is_new(kv[1])]
        items = new_items + old_items[: max(0, max_cells - len(new_items))]
    cells = [[*cell_center(k), 1 if is_new(ts) else 0] for k, ts in items]
    new_total = sum(1 for ts in first_any.values() if is_new(ts))
    return {
        "cells": cells, "riders": riders, "total": len(first_any), "new": new_total,
        "new_km": round(new_total * CELL_KM, 1), "all_new": start is None,
    }


# ---------------------------------------------------------------- years


METRIC_FN = {
    "km": _km,
    "rides": lambda r: 1,
    "hours": lambda r: (r.get("duration_s") or 0) / 3600,
    "elevation": lambda r: r.get("elevation_gain_m") or 0,
}


def year_curves(rides: list[dict], metric: str, today: date) -> dict:
    """Cumulative value per day of year for every year with rides (all riders summed)."""
    fn = METRIC_FN[metric]
    daily: dict[int, list[float]] = {}
    for r in rides:
        d = _day(r)
        arr = daily.setdefault(d.year, [0.0] * 366)
        arr[d.timetuple().tm_yday - 1] += fn(r)
    years = []
    for y in sorted(daily, reverse=True):
        acc, cum = 0.0, []
        last = 366 if y < today.year else today.timetuple().tm_yday
        for i, v in enumerate(daily[y]):
            acc += v
            cum.append(round(acc, 2) if i < last else None)
        years.append({"year": y, "values": cum, "total": round(acc, 1)})
    # Same day of year: how does this year compare with last year?
    pace = None
    cur = next((y for y in years if y["year"] == today.year), None)
    prev = next((y for y in years if y["year"] == today.year - 1), None)
    if cur and prev:
        i = today.timetuple().tm_yday - 1
        pace = {"now": cur["values"][i], "last_year": prev["values"][i]}
    return {"metric": metric, "years": years, "today_doy": today.timetuple().tm_yday, "pace": pace}


def review(rides: list[dict], accounts: list[dict], explored: dict, year: int, today: date) -> dict:
    """Year in review per rider: totals, monthly km, top month, records, exploring."""
    per: dict[int, list[dict]] = defaultdict(list)
    for r in rides:
        per[r["account_id"]].append(r)
    recs = {e["id"]: e["records"] for e in records(rides, accounts, today)}
    explored_by = {e["id"]: e for e in explored.get("riders", [])}
    out = []
    for acc in accounts:
        rs = per.get(acc["id"])
        if not rs:
            continue
        months = [0.0] * 12
        for r in rs:
            months[_day(r).month - 1] += _km(r)
        top = max(range(12), key=lambda i: months[i])
        ex = explored_by.get(acc["id"], {})
        out.append({
            "id": acc["id"], "name": acc["name"], "color": acc["color"],
            "km": round(sum(months), 1), "rides": len(rs),
            "hours": round(sum((r.get("duration_s") or 0) for r in rs) / 3600, 1),
            "elevation": round(sum((r.get("elevation_gain_m") or 0) for r in rs)),
            "months": [round(m, 1) for m in months],
            "top_month": {"month": top + 1, "km": round(months[top], 1)},
            "new_km": ex.get("new_km", 0), "records": recs.get(acc["id"], {}),
        })
    out.sort(key=lambda e: e["km"], reverse=True)
    return {"year": year, "riders": out}
