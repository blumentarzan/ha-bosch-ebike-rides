# ha-bosch-ebike-rides

Home Assistant **add-on** (not an integration) that syncs all Bosch eBike Flow (Smart System) rides of several Bosch accounts (family), stores them locally and shows a heatmap, per-person statistics and GPX export via Ingress.

Maintainer: GitHub `blumentarzan`. UI in English (default) and German, following the HA user's language (`static/i18n.js`); add-on docs English; code/comments English. Target: HA OS, amd64/aarch64.

The repo is public: no personal names, locations (home/work coordinates), e-mail addresses or infrastructure details in code, tests, demo data or docs. Demo/test data uses generic names (Alex, Sam) and Munich landmarks.

## Layout

- `repository.yaml` – HA add-on repository manifest
- `ebike_rides/` – the add-on (`icon.png` 128×128 / `logo.png` 250×100, rendered from `app/static/icon.svg`; `config.yaml`, `Dockerfile` from `python:3.15-alpine`, `run.sh`, `DOCS.md`, `translations/`)
- `ebike_rides/app/`
  - `bosch/auth.py` – PKCE login for two sources: `mobile` (public `one-bike-app` client, redirect `onebikeapp-ios://…`) and `dataact` (user's own `euda-…` client, redirect `http://localhost:8888/callback`). User pastes the redirect URL back.
  - `bosch/client.py` – `MobileClient` / `DataActClient`, both normalised to one model (`normalize_bike/activity/track`). Parsing is defensive (flat vs. `attributes`-wrapped, epoch vs. ISO).
  - `sync.py` – per-account lock, token refresh, full backfill (rides → tracks), incremental sync loop, HA bridge calls
  - `attribution.py` – ride → bike: `bikeId` → odometer rewind (1.5 km tolerance) → single bike
  - `geo.py` – Douglas-Peucker simplification; ~25 m grid cells per ride for the heatmap
  - `stats.py` – series per rider (day/week/month/year/weekday/hour), leaderboard, heat aggregation, totals
  - `db.py` – SQLite at `/data/rides.db` (accounts, pending_logins, bikes, activities, tracks)
  - `main.py` – aiohttp app on :8099; Ingress-only guard when `SUPERVISOR_TOKEN` is set
  - `ha.py` – sensors `sensor.ebike_<name>_km_this_year` / `_last_ride`, event `ebike_rides_new_activity`
  - `static/` – vanilla JS UI (Leaflet, Chart.js vendored), custom canvas heat layer
- `tests/fake_bosch.py` – fake Bosch cloud (token endpoint, mobile + Data Act APIs, generated rides)
- `scripts/upstream_watch.py` + `.github/upstream.json` – daily check of upstream projects

## Decisions (keep unless the maintainer says otherwise)

- Add-on, not HACS integration: heavy backfill and rendering stay out of HA Core; updates via add-on repository.
- Own API client instead of depending on `bosch-flow-mcp` (single-account, sync, MCP-shaped). Stay current via the upstream watcher (opens an issue), not via a dependency.
- Heatmap counts **rides per cell**, not GPS points; rendered per cell (no additive blur).
- Multi-account is core: every query filters by account; colours per person.
- Raw tracks stored compressed so nothing depends on Bosch staying available; GPX export always possible.
- No code copied from upstream (Xunil99/ha-bosch-ebike, partymola/bosch-flow-mcp); repo is MIT.

## Commands

```
pip install -r ebike_rides/requirements.txt pytest pytest-asyncio
python -m pytest -q
python scripts/preview.py          # demo UI with fake data on http://localhost:8099
python scripts/upstream_watch.py   # check upstream (needs GITHUB_TOKEN for rate limits)
python scripts/upstream_watch.py --update   # accept upstream state after adapting
docker build -t ebike-rides ./ebike_rides
```

Release: bump `version` in `ebike_rides/config.yaml` (and the Dockerfile label), add a `CHANGELOG.md` entry, push. HA shows the update.

## Open points

- **Real Bosch API, mobile source (checked 2026-11, one account):** activity list matches `normalize_activity` (`attributes`-wrapped, epoch seconds, distance m, speed km/h, `meta.pages`, page size 200). Bike profile is `{"data": [ECBSBikeProfileDto]}` with the odometer in `driveUnit.totalDistanceTraveled` (m); `fake_bosch.py` mirrors it. Rides may carry a `bikeId` that is not in the bike profile (cause unknown: other id scheme or replaced bike) – the sync logs it; check with the per-id debug line before changing attribution.
- Still unchecked against real answers: mobile track detail shape (sampled at debug level since 0.3.4), the whole Data Act source. With `log_level: debug` the client logs one redacted raw answer per endpoint (`redact()`/`check_activities()` in `bosch/client.py`). When fixing a mapping, add the real (redacted) shape to `tests/fake_bosch.py`.
- Data Act source: unclear whether Swiss accounts get data (EU only); mobile source is the default.
- Mobile login needs the redirect URL from browser DevTools (Network → `oauth2redirect`); consider a smoother flow later.
- Ideas: privacy zone around home (hide start/end), per-bike odometer chart, yearly goal per person, weather overlay.
