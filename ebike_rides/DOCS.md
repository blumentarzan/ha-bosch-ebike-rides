# eBike Rides

Fetches all rides from the Bosch eBike Flow cloud – for any number of Bosch accounts (family) – and shows them together:

- **Heatmap**: routes ridden more often glow stronger. Counted per *ride*, not per GPS point – waiting at a red light does not create a hotspot.
- **Lines view**: all rides overlaid, one colour per person, click to open a ride.
- **New roads**: the map's *New* view shows in orange what was ridden for the first time in the selected period, with the km of new and of all distinct roads (≈ 25 m grid).
- **Statistics & comparison**: km, rides, hours, elevation per day / week / month / year / weekday / time of day, optionally cumulative; the day view pages through one week at a time and can jump to any date. Leaderboard with longest ride, riding days, week streak.
- **Records & streaks** per person: longest ride, most climbing, fastest average (rides from 5 km), top speed, longest moving time, best day / week / month, longest and current day and week streaks.
- **Year tab**: cumulative curves of every year on one chart and how this year compares with the same day last year; a year in review per person (monthly km, best month, longest ride, streaks, newly explored km).
- **Ride details**: map, elevation and speed profile (hovering the profile marks the spot on the map and the other way round), power, cadence, calories, GPX.
- **Filters**: period, people, bike.
- **Export**: every ride as GPX, or all (filtered) rides as a ZIP.
- **Home Assistant**: sensors per person and an event per new ride.

The interface follows your Home Assistant language (English and German; everything else falls back to English).

Data is stored in `/data/rides.db` inside the add-on and is part of every Home Assistant backup. If the Bosch service ever goes away, everything already stored stays available.

## Add an account

**Accounts** tab → name, colour, login type → *Add*. The login dialog opens afterwards.

### Flow app login (default, all countries)

Uses the same login as the eBike Flow app. At the end Bosch redirects to `onebikeapp-ios://…`, which a browser cannot open. So, on a **computer**:

1. Open the developer tools (F12) **before** logging in → **Console** tab.
2. In the dialog click *Open Bosch login* and sign in with the SingleKey ID.
3. Chrome and Edge then show a red line `Failed to launch 'onebikeapp-ios://…'` in the console – copy the whole line. In other browsers use the **Network** tab: entry `oauth2redirect` → right-click → *Copy URL*.
4. Paste it into the dialog – it confirms when it found the login code – → *Connect*.

The code is only valid for a few minutes. After that the add-on renews access by itself; you only need to log in again when the account shows “login required”.

### EU Data Act (official interface)

Only for accounts in the EU. In the [Bosch Data Act portal](https://portal.bosch-ebike.com/data-act/app) create a client application (redirect URI `http://localhost:8888/callback`, do **not** tick *Confidential client*), share the data with this app on <https://flow.bosch-ebike.com> and enter the `euda-…` client ID in the add-on. During login the browser ends up on an unreachable localhost page – copy the address from the address bar and paste it.

## First sync

After login all rides and then every GPS track are loaded one by one (progress in the *Accounts* tab). With a few hundred rides this takes a few minutes. Afterwards new rides are checked every `sync_interval_minutes`.

## Bike attribution

Rides are attributed via Bosch's bike ID, otherwise via the odometer, and automatically when there is only one bike. Anything that cannot be attributed can be assigned to a bike by hand in the ride view.

## Home Assistant

With `publish_sensors: true`, per person:

- `sensor.ebike_<name>_km_this_year` (attributes: rides, km month/week/total)
- `sensor.ebike_<name>_km_this_month`, `…_km_this_week`, `…_rides_this_year`
- `sensor.ebike_<name>_days_since_last_ride`
- `sensor.ebike_<name>_<bike>_battery` – only while Bosch reports a charge level for the bike (often it does not)
- `sensor.ebike_<name>_last_ride`
- Event `ebike_rides_new_activity` (`rider`, `distance_km`, `title`, …) – not during the initial import.

The sensors are set after every sync, and again at the next sync after an HA restart.

## Options

| Option | Default | |
|---|---|---|
| `sync_interval_minutes` | 30 | Time between two syncs |
| `detail_delay_seconds` | 0.5 | Pause between two track downloads (spares the Bosch API) |
| `publish_sensors` | true | Sensors & event in HA |
| `log_level` | info | `debug` logs what Bosch returns, see below |

## Troubleshooting: wrong or missing values

If a ride shows 0 km, no elevation, a wrong time or sits on the wrong bike, the Bosch API probably answers with field names or units this add-on does not know yet.

1. Set `log_level` to `debug`, **save**, and restart the add-on. The first log line must read `... listening on :8099 (log level DEBUG)`; if it says `INFO`, the option was not saved.
2. Start a sync (Accounts tab).
3. Open the add-on **Log**. For each account it shows one raw answer per endpoint (`bikes raw`, `activities raw`, `track raw`) and what was parsed from it (`first ride parsed`, `track parsed`).
4. Open an issue on GitHub with those lines and what looks wrong.

These lines are made safe to share: tokens, e-mail addresses, bike names, ride titles and serial numbers are replaced by `<redacted>`, positions are rounded to about 10 km and lists are cut to their first two entries. Have a quick look before posting anyway.

Even at `info` level the add-on warns when values look wrong, e.g. rides without a distance, a distance that looks like km instead of m, or ride details without GPS points. Set `log_level` back to `info` afterwards.

## Map tiles

Map tiles come from the OpenStreetMap tile servers and are subject to their [tile usage policy](https://operations.osmfoundation.org/policies/tiles/).

## Note

Unofficial project, not affiliated with Bosch. The Flow app login uses the same interface as the app and may change without notice; a daily GitHub watcher reports changes in the projects that track this interface.
