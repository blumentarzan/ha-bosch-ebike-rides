/* UI strings (English default, German) and language detection.
 *
 * Language follows Home Assistant: the logged-in user's profile language read
 * from the HA frontend that hosts the Ingress iframe (same origin), then HA's
 * stored language choice, then HA's system language (sent by the backend).
 * Anything that is not German falls back to English. */
"use strict";

const STRINGS = {
  en: {
    "tab.map": "Map", "tab.stats": "Statistics", "tab.rides": "Rides", "tab.accounts": "Accounts",
    "range.year": "This year", "range.lastyear": "Last year", "range.12m": "Last 12 months",
    "range.month": "This month", "range.all": "All time", "range.custom": "Custom range …",
    "filter.all_bikes": "All bikes",
    "map.heat": "Heatmap", "map.lines": "Lines",
    "map.heat_info": "{n} rides · most ridden {max}×",
    "map.lines_info": "{n} rides – overlaid, click to open",
    "group.day": "Day", "group.week": "Week", "group.month": "Month", "group.year": "Year",
    "day.prev": "Previous week", "day.next": "Next week", "day.today": "Today", "day.pick": "Go to date",
    "day.yesterday": "Yesterday",
    "day.none": "No rides on this day.", "day.sum": "{n} rides · {km} km · {min} min",
    "day.sum_one": "1 ride · {km} km · {min} min",
    "day.span": "{from} – {to} · click a day for its rides",
    "group.weekday": "Weekday", "group.hour": "Time of day",
    "metric.rides": "Rides", "metric.hours": "Hours", "metric.elevation": "Elevation",
    "stats.cumulative": "cumulative", "stats.compare": "Comparison",
    "kpi.km": "Kilometres", "kpi.rides": "Rides", "kpi.hours": "Hours",
    "kpi.elevation": "Elevation (m)", "kpi.longest": "Longest ride",
    "col.person": "Person", "col.rides": "Rides", "col.avg_km": "Ø km", "col.hours": "Hours",
    "col.elevation": "Elevation", "col.longest": "Longest", "col.max_speed": "Max km/h",
    "col.days": "Riding days", "col.streak": "Week streak",
    "col.date": "Date", "col.start": "Start", "col.bike": "Bike", "col.title": "Title", "col.time": "Time",
    "col.elev_short": "Elev.", "col.avg_speed": "Ø km/h",
    "stats.empty": "No rides in this period",
    "week.prefix": "W",
    "rides.export": "All as GPX (ZIP)", "rides.count": "{n} rides",
    "ride.default_title": "Ride",
    "ride.distance": "Distance", "ride.duration": "Moving time", "ride.elevation": "Elevation",
    "ride.avg_speed": "Ø speed", "ride.max": "Max", "ride.avg_power": "Ø power",
    "ride.bike_unknown": "Bike: unknown", "ride.bike": "Bike: {name}",
    "status.ok": "connected", "status.login_required": "login required", "status.error": "error",
    "acc.src_short_mobile": "Flow app", "acc.src_short_dataact": "Data Act",
    "acc.relogin": "Log in again", "acc.login": "Log in", "acc.sync": "Sync now", "acc.remove": "Remove",
    "acc.progress_tracks": "Loading GPS tracks: {done}/{total}", "acc.progress_list": "Loading rides …",
    "acc.last_sync": "Last sync: {when}",
    "acc.bike_line": "{n} rides", "acc.unassigned": "{n} rides without a bike (assign them under “Rides”)",
    "acc.empty": "No account yet. Add your first Bosch account below.",
    "acc.bridge": "Per-person sensors (sensor.ebike_<name>_km_this_year, …_last_ride) and the event ebike_rides_new_activity are provided in Home Assistant.",
    "acc.confirm_delete": "Delete account “{name}” and all stored rides?",
    "acc.add_title": "Add account", "acc.name_ph": "Name (e.g. Alex)", "acc.color": "Colour",
    "acc.src_mobile": "Flow app login (all countries)", "acc.src_dataact": "EU Data Act (own client ID)",
    "acc.client_id_ph": "Client ID (Data Act only)", "acc.add": "Add",
    "login.title": "Log in to Bosch", "login.open": "1. Open Bosch login",
    "login.paste_ph": "2. Paste the redirect URL here (contains code=…)",
    "login.connect": "3. Connect",
    "login.dataact_steps": [
      "Open the login and sign in with the SingleKey ID of <b>{name}</b>.",
      "The browser then shows “site can’t be reached” (localhost) – that is expected. Copy the full address from the address bar.",
      "Paste it here and connect.",
    ],
    "login.mobile_steps": [
      "On a computer: open the browser developer tools (F12) → “Network” tab.",
      "Open the login and sign in with the SingleKey ID of <b>{name}</b>.",
      "At the end Bosch tries to open the app (onebikeapp-ios://…), which fails. Find the <code>oauth2redirect</code> entry in the network list, right-click → copy URL.",
      "Paste it here and connect. The code is only valid for a few minutes.",
    ],
    "common.cancel": "Cancel", "common.close": "Close",
    "weekdays": ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
  },
  de: {
    "tab.map": "Karte", "tab.stats": "Statistik", "tab.rides": "Fahrten", "tab.accounts": "Konten",
    "range.year": "Dieses Jahr", "range.lastyear": "Letztes Jahr", "range.12m": "Letzte 12 Monate",
    "range.month": "Dieser Monat", "range.all": "Alles", "range.custom": "Zeitraum …",
    "filter.all_bikes": "Alle Bikes",
    "map.heat": "Heatmap", "map.lines": "Linien",
    "map.heat_info": "{n} Fahrten · am häufigsten {max}× befahren",
    "map.lines_info": "{n} Fahrten – überlagert, Klick öffnet Details",
    "group.day": "Tag", "group.week": "Woche", "group.month": "Monat", "group.year": "Jahr",
    "day.prev": "Woche zurück", "day.next": "Woche vor", "day.today": "Heute", "day.pick": "Zu Datum springen",
    "day.yesterday": "Gestern",
    "day.none": "Keine Fahrten an diesem Tag.", "day.sum": "{n} Fahrten · {km} km · {min} min",
    "day.sum_one": "1 Fahrt · {km} km · {min} min",
    "day.span": "{from} – {to} · Tag anklicken zeigt seine Fahrten",
    "group.weekday": "Wochentag", "group.hour": "Uhrzeit",
    "metric.rides": "Fahrten", "metric.hours": "Stunden", "metric.elevation": "Höhenmeter",
    "stats.cumulative": "kumuliert", "stats.compare": "Vergleich",
    "kpi.km": "Kilometer", "kpi.rides": "Fahrten", "kpi.hours": "Stunden",
    "kpi.elevation": "Höhenmeter", "kpi.longest": "Längste Fahrt",
    "col.person": "Person", "col.rides": "Fahrten", "col.avg_km": "Ø km", "col.hours": "Stunden",
    "col.elevation": "Höhenmeter", "col.longest": "Längste", "col.max_speed": "Max km/h",
    "col.days": "Fahrtage", "col.streak": "Wochen-Serie",
    "col.date": "Datum", "col.start": "Start", "col.bike": "Bike", "col.title": "Titel", "col.time": "Zeit",
    "col.elev_short": "Hm", "col.avg_speed": "Ø km/h",
    "stats.empty": "Keine Fahrten im Zeitraum",
    "week.prefix": "KW",
    "rides.export": "Alle als GPX (ZIP)", "rides.count": "{n} Fahrten",
    "ride.default_title": "Fahrt",
    "ride.distance": "Distanz", "ride.duration": "Fahrzeit", "ride.elevation": "Höhenmeter",
    "ride.avg_speed": "Ø Tempo", "ride.max": "Max", "ride.avg_power": "Ø Leistung",
    "ride.bike_unknown": "Bike: unbekannt", "ride.bike": "Bike: {name}",
    "status.ok": "verbunden", "status.login_required": "Login nötig", "status.error": "Fehler",
    "acc.src_short_mobile": "Flow-App", "acc.src_short_dataact": "Data Act",
    "acc.relogin": "Neu anmelden", "acc.login": "Anmelden", "acc.sync": "Jetzt synchronisieren", "acc.remove": "Entfernen",
    "acc.progress_tracks": "GPS-Tracks laden: {done}/{total}", "acc.progress_list": "Fahrten werden geladen …",
    "acc.last_sync": "Letzter Sync: {when}",
    "acc.bike_line": "{n} Fahrten", "acc.unassigned": "{n} Fahrten ohne Bike-Zuordnung (in „Fahrten“ zuweisen)",
    "acc.empty": "Noch kein Konto. Füge unten dein erstes Bosch-Konto hinzu.",
    "acc.bridge": "Sensoren pro Person (sensor.ebike_<name>_km_this_year, …_last_ride) und das Event ebike_rides_new_activity werden in Home Assistant bereitgestellt.",
    "acc.confirm_delete": "Konto „{name}“ und alle gespeicherten Fahrten löschen?",
    "acc.add_title": "Konto hinzufügen", "acc.name_ph": "Name (z.B. Alex)", "acc.color": "Farbe",
    "acc.src_mobile": "Flow-App-Login (alle Länder)", "acc.src_dataact": "EU Data Act (eigene Client-ID)",
    "acc.client_id_ph": "Client-ID (nur Data Act)", "acc.add": "Hinzufügen",
    "login.title": "Bei Bosch anmelden", "login.open": "1. Bosch-Login öffnen",
    "login.paste_ph": "2. Umleitungs-URL hier einfügen (enthält code=…)",
    "login.connect": "3. Verbinden",
    "login.dataact_steps": [
      "Login öffnen und mit der SingleKey ID von <b>{name}</b> anmelden.",
      "Danach zeigt der Browser „Seite nicht erreichbar“ (localhost) – das ist richtig. Die komplette Adresse aus der Adresszeile kopieren.",
      "Hier einfügen und verbinden.",
    ],
    "login.mobile_steps": [
      "Am Computer: Browser-Entwicklertools öffnen (F12) → Tab „Netzwerk“.",
      "Login öffnen und mit der SingleKey ID von <b>{name}</b> anmelden.",
      "Am Ende versucht Bosch, die App zu öffnen (onebikeapp-ios://…) – das schlägt fehl. In der Netzwerk-Liste den Eintrag <code>oauth2redirect</code> suchen, Rechtsklick → URL kopieren.",
      "Hier einfügen und verbinden. Der Login gilt nur für ein paar Minuten.",
    ],
    "common.cancel": "Abbrechen", "common.close": "Schliessen",
    "weekdays": ["", "Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
  },
};

const I18N = {
  lang: "en",
  locale: "en",   // BCP 47 tag for number/date formatting, e.g. "de-CH"
  fromFrontend: false,
};

function _frontendLanguage() {
  // Ingress iframe shares the HA origin, so the parent frontend is readable.
  try {
    const hass = window.parent?.document?.querySelector("home-assistant")?.hass;
    const l = hass?.locale?.language || hass?.language;
    if (l) return l;
  } catch { /* cross-origin or no HA around us */ }
  try {
    const raw = window.localStorage.getItem("selectedLanguage");
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return null;
}

function setLanguage(tag) {
  const t = String(tag || "en").replace("_", "-");
  I18N.lang = t.toLowerCase().startsWith("de") ? "de" : "en";
  try { I18N.locale = Intl.NumberFormat.supportedLocalesOf([t]).length ? t : I18N.lang; }
  catch { I18N.locale = I18N.lang; }
  document.documentElement.lang = I18N.lang;
  applyStatic();
}

/** Called once the backend state arrives; HA's system language is the last fallback. */
function initLanguage(serverLang) {
  const fe = _frontendLanguage();
  I18N.fromFrontend = !!fe;
  setLanguage(fe || serverLang || "en");
}

function t(key, vars) {
  let s = STRINGS[I18N.lang][key] ?? STRINGS.en[key] ?? key;
  if (vars && typeof s === "string") s = s.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");
  return s;
}

function applyStatic() {
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => { el.placeholder = t(el.dataset.i18nPh); });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle); });
}

setLanguage(_frontendLanguage() || "en");
