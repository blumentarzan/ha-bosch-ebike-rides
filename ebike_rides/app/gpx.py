"""GPX export - so the raw data is never locked into this add-on."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from xml.sax.saxutils import escape


def filename(ride: dict) -> str:
    d = datetime.fromtimestamp(ride["start_ts"], timezone.utc).strftime("%Y-%m-%d_%H%M")
    title = re.sub(r"[^A-Za-z0-9äöüÄÖÜ_-]+", "-", ride.get("title") or "ride").strip("-")[:40]
    return f"{d}_{title or 'ride'}.gpx"


def to_gpx(ride: dict, points: list) -> str:
    start, end = ride["start_ts"], ride.get("end_ts") or ride["start_ts"]
    n = max(len(points) - 1, 1)
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="ha-bosch-ebike-rides" xmlns="http://www.topografix.com/GPX/1/1">',
        f"<trk><name>{escape(ride.get('title') or filename(ride)[:-4])}</name><trkseg>",
    ]
    for i, p in enumerate(points):
        # Bosch tracks carry no per-point time; spread start..end evenly so
        # tools that need timestamps (Strava, Garmin) accept the file.
        t = datetime.fromtimestamp(start + (end - start) * i / n, timezone.utc)
        ele = f"<ele>{p[2]:.1f}</ele>" if len(p) > 2 and p[2] is not None else ""
        out.append(
            f'<trkpt lat="{p[0]}" lon="{p[1]}">{ele}<time>{t:%Y-%m-%dT%H:%M:%SZ}</time></trkpt>'
        )
    out.append("</trkseg></trk></gpx>")
    return "\n".join(out)
