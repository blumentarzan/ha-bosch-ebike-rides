"""Which bike was a ride on?

Order of evidence (same idea as Xunil99/ha-bosch-ebike):
1. the ride's own ``bikeId``, if it names one of the account's bikes;
2. otherwise the odometer: walking from the newest ride backwards, a ride
   belongs to the bike whose (rewound) odometer matches the ride's end
   odometer; that bike's odometer is then rewound to the ride's start;
3. an account with exactly one bike gets every remaining ride.
Rides that match nothing stay unassigned (shown as "unknown bike").
"""

from __future__ import annotations

TOLERANCE_M = 1500.0


def ride_bike_ids(rides: list[dict]) -> dict[str, dict]:
    """Bike ids as named by the rides: ``{bike_id: {"rides", "first", "last"}}``.

    Compared with the bike profile this shows whether rides carry another id
    than the profile (all rides foreign) or belong to a bike that is gone
    (only older rides foreign).
    """
    out: dict[str, dict] = {}
    for r in rides:
        bid = r.get("bike_id") or "none"
        e = out.setdefault(bid, {"rides": 0, "first": r["start_ts"], "last": r["start_ts"]})
        e["rides"] += 1
        e["first"] = min(e["first"], r["start_ts"])
        e["last"] = max(e["last"], r["start_ts"])
    return out


def attribute(bikes: list[dict], rides: list[dict]) -> dict[str, str]:
    known = {b["id"] for b in bikes}
    result: dict[str, str] = {}
    odo = {b["id"]: b.get("odometer_m") for b in bikes}

    for r in sorted(rides, key=lambda r: r["start_ts"], reverse=True):
        bid = r.get("bike_id")
        if bid in known:
            result[r["id"]] = bid
            if r.get("start_odometer_m") is not None:
                odo[bid] = r["start_odometer_m"]
            continue
        start = r.get("start_odometer_m")
        if start is not None and len(bikes) > 1:
            end = start + (r.get("distance_m") or 0)
            best, best_d = None, TOLERANCE_M
            for b_id, value in odo.items():
                if value is None:
                    continue
                d = abs(value - end)
                if d <= best_d:
                    best, best_d = b_id, d
            if best:
                result[r["id"]] = best
                odo[best] = start
                continue
        if len(bikes) == 1:
            result[r["id"]] = bikes[0]["id"]
    return result
