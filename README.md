# eBike Rides – Home Assistant Add-on

All your Bosch eBike Flow (Smart System) rides, for the whole family, in one place: a heatmap of the ways you ride most, per-person comparison by week / month / year, GPX export – and the data stays on your Home Assistant.

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fblumentarzan%2Fha-bosch-ebike-rides)

## Install

1. Home Assistant → Settings → Add-ons → Add-on Store → ⋮ → Repositories → add `https://github.com/blumentarzan/ha-bosch-ebike-rides`.
2. Install **eBike Rides**, start it, open it from the sidebar.
3. Tab *Konten*: add one Bosch account per person and log in.

Updates show up in Home Assistant like any other add-on update (bump `version` in `ebike_rides/config.yaml` and push).

See [ebike_rides/DOCS.md](ebike_rides/DOCS.md) for the login steps and options (German).

## How it works

```
Bosch cloud ──(per account: Flow-app API or EU Data Act API)──► sync ──► SQLite (/data)
                                                                          │
                     Ingress web UI (map, stats, rides, accounts) ◄───────┤
                     HA sensors + ebike_rides_new_activity event ◄────────┘
```

- `ebike_rides/app/bosch/` – OAuth (PKCE) and both API clients, normalised to one model.
- `ebike_rides/app/sync.py` – backfill of all rides + tracks, then incremental sync.
- `ebike_rides/app/attribution.py` – which bike a ride was on (bike id → odometer → single bike).
- `ebike_rides/app/geo.py` – track simplification and the ~25 m grid used by the heatmap (counts rides per cell).
- `scripts/upstream_watch.py` + workflow – daily check of the projects that document the Bosch API; opens an issue when endpoints or auth change there.

## Credits

The Bosch endpoints and login flow were learned from [partymola/bosch-flow-mcp](https://github.com/partymola/bosch-flow-mcp) and [Xunil99/ha-bosch-ebike](https://github.com/Xunil99/ha-bosch-ebike) – thank you. No code is copied from them. Bundled: Leaflet, Leaflet.heat, Chart.js (licenses in `ebike_rides/app/static/vendor`). Map tiles © OpenStreetMap contributors.

Unofficial, not affiliated with Bosch. "Bosch" and "eBike Flow" are trademarks of their owners.

## Development

```
pip install -r ebike_rides/requirements.txt pytest pytest-asyncio
python -m pytest            # runs against a fake Bosch cloud (tests/fake_bosch.py)
python scripts/preview.py   # UI with generated demo rides on http://localhost:8099
```

License: MIT
