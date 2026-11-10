"""Track geometry: simplification for drawing, grid cells for the heatmap.

The heatmap counts in how many *rides* a ~25 m cell was visited (not how
many GPS points fell into it), so standing at a traffic light does not make a
spot glow - only riding the same way again does.
"""

from __future__ import annotations

import math

LAT_STEP = 0.000225  # ~25 m
LON_STEP = 0.00033  # ~25 m around 47° N


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def track_length_m(track: list) -> float:
    return sum(
        haversine_m(a[0], a[1], b[0], b[1]) for a, b in zip(track, track[1:])
    )


def simplify(points: list[tuple[float, float]], tolerance_deg: float = 0.00004) -> list:
    """Iterative Douglas-Peucker (~4 m), keeps drawing light for many rides."""
    n = len(points)
    if n < 3:
        return list(points)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    tol2 = tolerance_deg * tolerance_deg
    while stack:
        s, e = stack.pop()
        (y1, x1), (y2, x2) = points[s], points[e]
        dx, dy = x2 - x1, y2 - y1
        norm = dx * dx + dy * dy
        best, idx = -1.0, -1
        for i in range(s + 1, e):
            y, x = points[i]
            if norm == 0:
                d = (x - x1) ** 2 + (y - y1) ** 2
            else:
                t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / norm))
                d = (x1 + t * dx - x) ** 2 + (y1 + t * dy - y) ** 2
            if d > best:
                best, idx = d, i
        if best > tol2 and idx > 0:
            keep[idx] = True
            stack.append((s, idx))
            stack.append((idx, e))
    return [p for p, k in zip(points, keep) if k]


def cells_for_track(track: list) -> set[tuple[int, int]]:
    """Grid cells a ride passed through, with gaps between GPS points filled in."""
    cells: set[tuple[int, int]] = set()
    prev = None
    for p in track:
        lat, lon = p[0], p[1]
        if prev is not None:
            plat, plon = prev
            steps = int(
                max(abs(lat - plat) / LAT_STEP, abs(lon - plon) / LON_STEP)
            )
            if steps > 400:  # GPS jump (tunnel, signal loss) - don't paint a straight line
                steps = 0
            for k in range(1, steps):
                f = k / steps
                cells.add(
                    (
                        math.floor((plat + (lat - plat) * f) / LAT_STEP),
                        math.floor((plon + (lon - plon) * f) / LON_STEP),
                    )
                )
        cells.add((math.floor(lat / LAT_STEP), math.floor(lon / LON_STEP)))
        prev = (lat, lon)
    return cells


def encode_cells(cells: set[tuple[int, int]]) -> str:
    return ";".join(f"{a},{b}" for a, b in sorted(cells))


def decode_cells(text: str | None):
    if not text:
        return
    for part in text.split(";"):
        a, _, b = part.partition(",")
        yield int(a), int(b)


def cell_center(key: tuple[int, int]) -> tuple[float, float]:
    return (
        round((key[0] + 0.5) * LAT_STEP, 6),
        round((key[1] + 0.5) * LON_STEP, 6),
    )
