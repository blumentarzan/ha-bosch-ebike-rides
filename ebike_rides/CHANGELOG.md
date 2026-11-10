# Changelog

## 0.3.4

- Bikes: read the odometer from the real Flow app bike profile (`driveUnit.totalDistanceTraveled`); it was always empty, which weakened attributing rides to bikes by odometer.
- Log: the start line now shows the add-on version and the active log level (e.g. `eBike Rides 0.3.4 listening on :8099 (log level DEBUG)`), and each sync logs its start at debug level, so it is clear whether debug logging is on.
- Log: a note when rides name a bike id that is not in the bike profile (with per-id ride counts and date ranges at debug level).
- Debug log: one track is fetched as a sample even when all tracks are already stored, plus a count of stored tracks by status.

## 0.3.3

- Diagnostics for the first syncs against the real Bosch API: with `log_level: debug` the log shows one redacted raw answer per endpoint and what was parsed from it, ready to paste into an issue (see "Troubleshooting" in the documentation).
- Warnings at normal log level when values look wrong: rides that could not be parsed, distance or duration missing, distance that looks like km, implausible speed, ride details without GPS points, bikes without an id.

## 0.3.2

- Runtime: Python 3.15 (base image `python:3.15-alpine`).
- CI: updated GitHub Actions (checkout v7, setup-python v7); tests run on Python 3.15.

## 0.3.1

- Add-on config: removed options that only repeated Home Assistant's defaults (add-on linter); English store description.

## 0.3.0

- Accounts: change a person's colour at any time (click the colour dot).
- Map: intensity slider for heatmap and lines, so rarely ridden routes stay visible; remembered per browser.
- Filters: new period "Last 7 days".
- Map: fit to the rides reliably even when the page is still being laid out on load.

## 0.2.0

- UI in English and German, following the Home Assistant user's language (English for all other languages). Add-on documentation now in English.
- Statistics: new **Day** view – one week per page, back/forward by week, jump to any date, click a day to list its rides.
- Map: fixed OpenStreetMap tiles being blocked ("Access blocked") by sending a Referer as required by the OSM tile policy; fixed the map sometimes opening at maximum zoom.
- Neutral defaults: start view, demo/test data and fallback time zone no longer tied to a specific location.

## 0.1.0

- First version: multiple Bosch accounts (Flow-app login or EU Data Act), full backfill of rides and GPS tracks, heatmap and line map, statistics per person by week/month/year/weekday/hour, ride list with manual bike assignment, GPX/ZIP export, Home Assistant sensors and new-ride event.
