# Changelog

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
