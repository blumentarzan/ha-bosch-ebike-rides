/* eBike Rides – UI. All URLs are relative so it works behind HA Ingress. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const fmt = (n, d = 0) => (n ?? 0).toLocaleString(I18N.locale, { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtDate = (date, opts) => date.toLocaleString(I18N.locale, opts);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const monthShort = (m) => new Date(2000, m, 1).toLocaleString(I18N.locale, { month: "short" });
// Neutral start view (central Europe) until rides are loaded.
const DEFAULT_VIEW = [[50, 10], 4];
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
// OSM's tile policy requires a Referer; HA's frontend may otherwise strip it.
const TILE_OPTS = { maxZoom: 19, attribution: "© OpenStreetMap contributors", referrerPolicy: "strict-origin-when-cross-origin" };

const S = {
  state: null, tab: "map", mapMode: "heat", group: localStorageGet("group") || "month", metric: "km", cum: false,
  dayEnd: null,   // last day (YYYY-MM-DD) of the 7-day window in the day view; null = today
  daySel: null,   // day whose rides are listed below the chart
  off: new Set(JSON.parse(localStorageGet("off") || "[]")),
  map: null, layer: null, chart: null, fitted: false,
  yearMetric: "km", reviewYear: null, yearChart: null, profile: null,
};

function localStorageGet(k) { try { return localStorage.getItem("ebr." + k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem("ebr." + k, v); } catch { /* ignore */ } }

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

/* ------------------------------------------------------------ filters */
function iso(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
const parseDay = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const addDays = (s, n) => { const d = parseDay(s); d.setDate(d.getDate() + n); return iso(d); };
const today = () => iso(new Date());

/** Query string for the current filters; `range` = false leaves the period out. */
function query(range = true) {
  const p = new URLSearchParams();
  const now = new Date();
  const r = range ? $("#f-range").value : "all";
  let from = null, to = null;
  if (r === "7d") from = addDays(today(), -6);  // today plus the 6 days before
  if (r === "year") from = `${now.getFullYear()}-01-01`;
  if (r === "lastyear") { from = `${now.getFullYear() - 1}-01-01`; to = `${now.getFullYear() - 1}-12-31`; }
  if (r === "month") from = iso(new Date(now.getFullYear(), now.getMonth(), 1));
  if (r === "12m") from = iso(new Date(now.getFullYear() - 1, now.getMonth(), now.getDate() + 1));
  if (r === "custom") { from = $("#f-from").value || null; to = $("#f-to").value || null; }
  if (from) p.set("from", from);
  if (to) p.set("to", to);
  const on = (S.state?.accounts || []).filter((a) => !S.off.has(a.id)).map((a) => a.id);
  if (S.state && on.length !== S.state.accounts.length) p.set("accounts", on.length ? on.join(",") : "-1");
  const bike = $("#f-bike").value;
  if (bike) p.set("bikes", bike);
  return p.toString();
}

function renderFilters() {
  const accs = S.state.accounts;
  $("#f-riders").innerHTML = accs.map((a) =>
    `<span class="chip ${S.off.has(a.id) ? "off" : ""}" data-id="${a.id}"><span class="dot" style="background:${esc(a.color)}"></span>${esc(a.name)}</span>`
  ).join("");
  $$("#f-riders .chip").forEach((c) => c.onclick = () => {
    const id = +c.dataset.id;
    S.off.has(id) ? S.off.delete(id) : S.off.add(id);
    localStorageSet("off", JSON.stringify([...S.off]));
    renderFilters(); refresh();
  });
  const sel = $("#f-bike"), cur = sel.value;
  const names = Object.fromEntries(accs.map((a) => [a.id, a.name]));
  sel.innerHTML = `<option value="">${esc(t("filter.all_bikes"))}</option>` + S.state.bikes.map((b) =>
    `<option value="${esc(b.id)}">${esc(b.name)}${accs.length > 1 ? " · " + esc(names[b.account_id]) : ""}</option>`).join("");
  sel.value = cur;
}

/* ------------------------------------------------------------ map */
// Heat colours: rarely ridden = cool blue, most ridden = hot red.
const HEAT_STOPS = [[0, [59, 91, 219]], [0.35, [18, 184, 134]], [0.6, [250, 176, 5]], [0.8, [253, 126, 20]], [1, [224, 49, 49]]];
function heatColor(t) {
  for (let i = 1; i < HEAT_STOPS.length; i++) {
    const [p1, c1] = HEAT_STOPS[i], [p0, c0] = HEAT_STOPS[i - 1];
    if (t <= p1) { const f = (t - p0) / (p1 - p0 || 1); return `rgb(${c0.map((v, k) => Math.round(v + (c1[k] - v) * f)).join(",")})`; }
  }
  return "rgb(224,49,49)";
}
// Intensity slider 1..10 (5 = default) -> k 0.2..2. Higher k lifts rarely
// ridden cells up the colour scale (gamma), draws dots/lines bigger and more opaque.
const intensityK = () => (+$("#map-intensity").value || 5) / 5;
const heatT = (c, max) => (max <= 1 ? 1 : (Math.log(c) / Math.log(max)) ** (1 / intensityK()));
const lineStyle = () => { const k = intensityK(); return { weight: 1.5 + 1.5 * k, opacity: Math.min(1, 0.12 + 0.3 * k) }; };

// Draws every grid cell in the colour of its own ride count (no summing of
// neighbours), so a corridor ridden 200 times stands out against one ridden twice.
// `style(n)` -> [colour, size factor] overrides the heat scale (used by the explorer view).
const CellHeat = L.Layer.extend({
  initialize(cells, max, style) { this.cells = cells.slice().sort((a, b) => a[2] - b[2]); this.max = max; this.style = style; },
  onAdd(map) {
    this._map = map;
    this._canvas = L.DomUtil.create("canvas", "leaflet-zoom-hide");
    map.getPanes().overlayPane.appendChild(this._canvas);
    map.on("moveend zoomend resize", this._draw, this);
    this._draw();
  },
  onRemove(map) { map.off("moveend zoomend resize", this._draw, this); this._canvas.remove(); },
  _draw() {
    const map = this._map, size = map.getSize(), dpr = window.devicePixelRatio || 1;
    const c = this._canvas;
    L.DomUtil.setPosition(c, map.containerPointToLayerPoint([0, 0]));
    c.width = size.x * dpr; c.height = size.y * dpr;
    c.style.width = size.x + "px"; c.style.height = size.y + "px";
    const ctx = c.getContext("2d");
    ctx.scale(dpr, dpr);
    const z = map.getZoom();
    const lat = map.getCenter().lat;
    const k = intensityK();
    const px = Math.max(2.2, Math.min(9, 25 / (156543 * Math.cos(lat * Math.PI / 180) / 2 ** z) * 1.6)) * (0.7 + 0.3 * k);
    const b = map.getBounds().pad(0.05);
    ctx.globalAlpha = Math.min(1, 0.6 + 0.3 * k);
    let last = null;
    for (const [la, lo, n] of this.cells) {
      if (la < b.getSouth() || la > b.getNorth() || lo < b.getWest() || lo > b.getEast()) continue;
      const [col, f] = this.style ? this.style(n) : [heatColor(heatT(n, this.max)), 1];
      if (col !== last) { ctx.fillStyle = col; last = col; }
      const p = map.latLngToContainerPoint([la, lo]);
      ctx.beginPath(); ctx.arc(p.x, p.y, (px * f) / 2, 0, 6.2832); ctx.fill();
    }
  },
});

const EXPLORE_NEW = "#f76707", EXPLORE_OLD = "#868e96";
const exploreStyle = (isNew) => (isNew ? [EXPLORE_NEW, 1.25] : [EXPLORE_OLD, 0.8]);

function renderLegend(max) {
  const el = $("#legend");
  if (S.mapMode === "explore") {
    el.hidden = false;
    el.innerHTML = `<span class="marks"><span><span class="sw" style="background:${EXPLORE_NEW}"></span>${esc(t("map.new"))}</span>` +
      `<span><span class="sw" style="background:${EXPLORE_OLD}"></span>${esc(t("map.known"))}</span></span>`;
    return;
  }
  if (!max) { el.hidden = true; return; }
  el.hidden = false;
  // middle mark = count that lands in the middle of the bar at the current intensity
  const marks = [1, Math.round(max ** (0.5 ** intensityK())), max].filter((v, i, a) => a.indexOf(v) === i);
  el.innerHTML = `<span class="bar" style="background:linear-gradient(90deg,${HEAT_STOPS.map(([p, c]) => `rgb(${c}) ${p * 100}%`).join(",")})"></span>` +
    `<span class="marks">${marks.map((m) => `<span>${m}×</span>`).join("")}</span>`;
}

function initMap() {
  S.map = L.map("map", { preferCanvas: true }).setView(...DEFAULT_VIEW);
  L.tileLayer(TILE_URL, TILE_OPTS).addTo(S.map);
  initMapResizeFit();
}

async function loadMap() {
  if (!S.map) initMap();
  setTimeout(() => S.map.invalidateSize(), 50);
  const q = query();
  if (S.layer) { S.map.removeLayer(S.layer); S.layer = null; }
  let bounds = null;
  if (S.mapMode === "heat") {
    const d = await api(`api/heat?${q}`);
    S.heatMax = d.max;
    S.layer = new CellHeat(d.cells, d.max).addTo(S.map);
    $("#map-info").textContent = t("map.heat_info", { n: fmt(d.rides), max: fmt(d.max) });
    renderLegend(d.max);
    if (d.cells.length) bounds = L.latLngBounds(d.cells.map((p) => [p[0], p[1]]));
  } else if (S.mapMode === "explore") {
    const d = await api(`api/explore?${q}`);
    S.layer = new CellHeat(d.cells, 1, exploreStyle).addTo(S.map);
    $("#map-info").textContent = d.all_new
      ? t("map.explore_all", { total: fmt(d.total * 0.025) })
      : t("map.explore_info", { new: fmt(d.new_km, 1), total: fmt(d.total * 0.025) });
    renderLegend(0);
    const fresh = d.cells.filter((c) => c[2]);
    const pts = (fresh.length && !d.all_new ? fresh : d.cells).map((p) => [p[0], p[1]]);
    if (pts.length) bounds = L.latLngBounds(pts);
  } else {
    const d = await api(`api/lines?${q}`);
    const group = L.featureGroup();
    for (const l of d.lines) {
      L.polyline(l.p, { color: l.c, ...lineStyle(), interactive: true })
        .on("click", () => openRide(l.a, l.id)).addTo(group);
    }
    S.layer = group.addTo(S.map);
    renderLegend(0);
    $("#map-info").textContent = t("map.lines_info", { n: fmt(d.lines.length) });
    if (d.lines.length) bounds = group.getBounds();
  }
  if (bounds && bounds.isValid() && !S.fitted) fitWhenSized(bounds);
}

// fitBounds on a container without a real size (tab just shown, iframe or
// window still being laid out) zooms to the maximum. So fit only once the map
// is big enough; otherwise remember the bounds and fit on the next resize.
function fitWhenSized(bounds) {
  S.map.invalidateSize();
  const size = S.map.getSize();
  if (size.x > 100 && size.y > 100) {
    S.map.fitBounds(bounds, { padding: [20, 20] });
    S.fitted = true;
    S.pendingFit = null;
    return;
  }
  S.pendingFit = bounds;
}

function initMapResizeFit() {
  const retry = () => { if (S.pendingFit && !S.fitted) fitWhenSized(S.pendingFit); };
  S.map.on("resize", retry);
  window.addEventListener("resize", retry);
  if (window.ResizeObserver) new ResizeObserver(retry).observe($("#map"));
}

/* ------------------------------------------------------------ stats */
/** "Today" / "Yesterday" / "Wed, 11/4"; long form: "Today · Tuesday, November 10, 2026". */
function dayName(s, style = "short") {
  const rel = s === today() ? t("day.today") : s === addDays(today(), -1) ? t("day.yesterday") : null;
  if (style === "short") {
    return rel || parseDay(s).toLocaleDateString(I18N.locale, { weekday: "short", day: "numeric", month: "numeric" });
  }
  const full = parseDay(s).toLocaleDateString(I18N.locale, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  return rel ? `${rel} · ${full}` : full;
}

function label(k) {
  if (S.group === "day") return dayName(k);
  if (S.group === "weekday") return t("weekdays")[+k];
  if (S.group === "hour") return `${k}h`;
  if (S.group === "month") { const [y, m] = k.split("-"); return `${monthShort(+m - 1)} ${y.slice(2)}`; }
  if (S.group === "week") return k.replace("-W", ` ${t("week.prefix")}`);
  return k;
}

function syncStatsControls() {
  const day = S.group === "day";
  $$("#st-group button").forEach((x) => x.classList.toggle("active", x.dataset.g === S.group));
  $("#day-nav").hidden = !day;
  $("#day-card").hidden = !day;
  // The day view and the year tab bring their own period; the global period filter would only confuse.
  $("#f-range").hidden = (S.tab === "stats" && day) || S.tab === "year";
  $("#f-custom").hidden = $("#f-range").hidden || $("#f-range").value !== "custom";
}

async function loadStats() {
  syncStatsControls();
  let q;
  if (S.group === "day") {
    const end = S.dayEnd || today();
    const from = addDays(end, -6);
    if (!S.daySel || S.daySel < from || S.daySel > end) S.daySel = end;
    $("#day-date").value = end;
    $("#day-date").max = today();
    $("#day-next").disabled = end >= today();
    $("#day-span").textContent = t("day.span", {
      from: parseDay(from).toLocaleDateString(I18N.locale), to: parseDay(end).toLocaleDateString(I18N.locale) });
    q = `${query(false)}&from=${from}&to=${end}`;
    loadDay();
  } else {
    q = query();
  }
  loadRecords(q);
  const d = await api(`api/stats?group=${S.group}&${q}`);
  const lb = d.leaderboard;
  const sum = (k) => lb.reduce((a, e) => a + e[k], 0);
  $("#kpis").innerHTML = [
    [t("kpi.km"), fmt(sum("km"))], [t("kpi.rides"), fmt(sum("rides"))],
    [t("kpi.hours"), fmt(sum("hours"), 1)], [t("kpi.elevation"), fmt(sum("elevation"))],
    [t("kpi.longest"), fmt(Math.max(0, ...lb.map((e) => e.longest_km)), 1) + " km"],
  ].map(([l, v]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");

  const s = d.series;
  const datasets = s.riders.map((r) => {
    let vals = r[S.metric];
    if (S.cum) { let acc = 0; vals = vals.map((v) => (acc += v)); }
    return { label: r.name, data: vals, backgroundColor: r.color, borderColor: r.color,
      borderRadius: 4, tension: 0.25, pointRadius: 0, fill: false };
  });
  const type = S.cum ? "line" : "bar";
  const css = getComputedStyle(document.documentElement);
  const grid = css.getPropertyValue("--line").trim(), muted = css.getPropertyValue("--muted").trim();
  if (S.chart) S.chart.destroy();
  S.chart = new Chart($("#chart"), {
    type, data: { labels: s.periods.map(label), datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      onClick: (_ev, els) => {
        if (S.group !== "day" || !els.length) return;
        S.daySel = s.periods[els[0].index]; loadDay();
      },
      plugins: { legend: { labels: { color: muted, usePointStyle: true } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.parsed.y, S.metric === "km" || S.metric === "hours" ? 1 : 0)}` } } },
      scales: { x: { grid: { display: false }, ticks: { color: muted, maxRotation: 0, autoSkip: true } },
        y: { grid: { color: grid }, ticks: { color: muted }, beginAtZero: true } },
    },
  });

  const cols = [["name", t("col.person")], ["km", "km", 1], ["rides", t("col.rides")], ["avg_km", t("col.avg_km"), 1],
    ["hours", t("col.hours"), 1], ["elevation", t("col.elevation")], ["longest_km", t("col.longest"), 1],
    ["max_speed", t("col.max_speed"), 1], ["riding_days", t("col.days")], ["streak_weeks", t("col.streak")]];
  $("#board").innerHTML = `<tr>${cols.map(([, h], i) => `<th class="${i ? "n" : ""}">${h}</th>`).join("")}</tr>` +
    (lb.length ? lb.map((e, i) => `<tr>${cols.map(([k, , dec], j) => j === 0
      ? `<td>${["🥇", "🥈", "🥉"][i] || ""} <span class="dot" style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${esc(e.color)}"></span> ${esc(e.name)}</td>`
      : `<td class="n">${fmt(e[k], dec || 0)}</td>`).join("")}</tr>`).join("")
      : `<tr><td colspan="${cols.length}" class="empty">${esc(t("stats.empty"))}</td></tr>`);
}

/* ------------------------------------------------------------ records */
const nDays = (n) => t(n === 1 ? "rec.day" : "rec.days", { n: fmt(n) });
const nWeeks = (n) => t(n === 1 ? "rec.week" : "rec.weeks", { n: fmt(n) });
const shortDate = (d) => d.toLocaleDateString(I18N.locale, { day: "numeric", month: "short", year: "numeric" });
const monthName = (key) => { const [y, m] = key.split("-"); return `${new Date(+y, +m - 1, 1).toLocaleString(I18N.locale, { month: "long" })} ${y}`; };
const runSpan = (r) => (r && r.from ? `${shortDate(parseDay(r.from))} – ${shortDate(parseDay(r.to))}` : "");
const hm = (min) => `${Math.floor(min / 60)} h ${String(Math.round(min % 60)).padStart(2, "0")} min`;

function rideRec(x, show) { return x ? [show(x.value), shortDate(dt(x.ts)), x] : ["–", ""]; }

// [label key, records -> [main text, sub text, ride ref (clickable) or undefined]]
const RECORD_ROWS = [
  ["rec.longest", (r) => rideRec(r.longest_km, (v) => `${fmt(v, 1)} km`)],
  ["rec.climb", (r) => rideRec(r.climb_m, (v) => `${fmt(v)} m`)],
  ["rec.fastest", (r) => rideRec(r.fastest_kmh, (v) => `${fmt(v, 1)} km/h`)],
  ["rec.top_speed", (r) => rideRec(r.top_speed_kmh, (v) => `${fmt(v, 1)} km/h`)],
  ["rec.longest_min", (r) => rideRec(r.longest_min, hm)],
  ["rec.best_day", (r) => [`${fmt(r.best_day_km.value, 1)} km`, shortDate(parseDay(r.best_day_km.key))]],
  ["rec.best_week", (r) => [`${fmt(r.best_week_km.value, 1)} km`, t("rec.week_of", { d: shortDate(parseDay(r.best_week_km.key)) })]],
  ["rec.best_month", (r) => [`${fmt(r.best_month_km.value, 1)} km`, monthName(r.best_month_km.key)]],
  ["rec.day_streak", (r) => [nDays(r.day_streak.longest.value), runSpan(r.day_streak.longest)]],
  ["rec.day_streak_now", (r) => [nDays(r.day_streak.current), ""]],
  ["rec.week_streak", (r) => [nWeeks(r.week_streak.longest.value), runSpan(r.week_streak.longest)]],
  ["rec.week_streak_now", (r) => [nWeeks(r.week_streak.current), ""]],
  ["rec.riding_days", (r) => [fmt(r.riding_days), ""]],
  ["rec.first", (r) => [shortDate(dt(r.first_ts)), ""]],
];

async function loadRecords(q) {
  const { riders } = await api(`api/records?${q}`);
  if (!riders.length) { $("#records").innerHTML = `<tr><td class="empty">${esc(t("stats.empty"))}</td></tr>`; return; }
  const head = riders.map((p) =>
    `<th class="n"><span class="dot" style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${esc(p.color)}"></span> ${esc(p.name)}</th>`).join("");
  const rows = RECORD_ROWS.map(([key, fn]) => `<tr><td>${esc(t(key))}</td>${riders.map((p) => {
    const [main, sub, ride] = fn(p.records);
    const attrs = ride ? ` class="n rec click" data-a="${ride.account_id}" data-id="${esc(ride.id)}"` : ` class="n rec"`;
    return `<td${attrs}><b>${esc(main)}</b>${sub ? `<span class="muted small">${esc(sub)}</span>` : ""}</td>`;
  }).join("")}</tr>`).join("");
  $("#records").innerHTML = `<tr><th></th>${head}</tr>${rows}`;
  $$("#records td.click").forEach((td) => td.onclick = () => openRide(+td.dataset.a, td.dataset.id));
}

/** Rides of the selected day, below the day chart. */
async function loadDay() {
  const day = S.daySel;
  const d = await api(`api/rides?${query(false)}&from=${day}&to=${day}`);
  if (day !== S.daySel) return;  // a newer click won
  $("#day-title").textContent = dayName(day, "long");
  const km = d.rides.reduce((a, r) => a + (r.distance_m || 0), 0) / 1000;
  const min = d.rides.reduce((a, r) => a + (r.duration_s || 0), 0) / 60;
  $("#day-sum").textContent = d.rides.length
    ? t(d.rides.length === 1 ? "day.sum_one" : "day.sum", { n: d.rides.length, km: fmt(km, 1), min: fmt(min) }) : "";
  $("#day-rides").innerHTML = d.rides.length ? ridesTable(d.rides.slice().reverse(), { timeStyle: "short" })
    : `<tr><td class="empty">${esc(t("day.none"))}</td></tr>`;
  $$("#day-rides tr.click").forEach((tr) => tr.onclick = () => openRide(+tr.dataset.a, tr.dataset.id));
}

/* ------------------------------------------------------------ year */
const METRIC_FMT = {
  km: (v) => `${fmt(v)} km`, rides: (v) => `${fmt(v)} ${t("review.rides")}`,
  hours: (v) => `${fmt(v, 1)} h`, elevation: (v) => `${fmt(v)} m`,
};
// 366 day labels of a leap year; only the 1st of each month gets a tick.
const DOY_LABELS = Array.from({ length: 366 }, (_, i) => new Date(2024, 0, i + 1));

async function loadYear() {
  const q = query(false);
  const d = await api(`api/years?metric=${S.yearMetric}&${q}`);
  const css = getComputedStyle(document.documentElement);
  const grid = css.getPropertyValue("--line").trim(), muted = css.getPropertyValue("--muted").trim();
  const accent = css.getPropertyValue("--accent").trim();
  const thisYear = new Date().getFullYear();
  const datasets = d.years.slice(0, 6).map((y, i) => {
    const current = y.year === thisYear;
    const color = current ? accent : `hsl(${190 + i * 25} 15% ${45 + i * 7}%)`;
    return { label: String(y.year), data: y.values, borderColor: color, backgroundColor: color,
      borderWidth: current ? 3 : 1.6, pointRadius: 0, tension: 0.15, spanGaps: false };
  });
  if (S.yearChart) S.yearChart.destroy();
  S.yearChart = new Chart($("#yr-chart"), {
    type: "line", data: { labels: DOY_LABELS, datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: muted, usePointStyle: true } },
        tooltip: { callbacks: {
          title: (items) => DOY_LABELS[items[0].dataIndex].toLocaleDateString(I18N.locale, { day: "numeric", month: "long" }),
          label: (c) => `${c.dataset.label}: ${METRIC_FMT[S.yearMetric](c.parsed.y)}` } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: muted, autoSkip: false, maxRotation: 0,
          callback: (_v, i) => (DOY_LABELS[i].getDate() === 1 ? monthShort(DOY_LABELS[i].getMonth()) : "") } },
        y: { grid: { color: grid }, ticks: { color: muted }, beginAtZero: true },
      },
    },
  });
  const p = d.pace;
  if (p && p.last_year != null) {
    const diff = p.now - p.last_year, f = METRIC_FMT[S.yearMetric];
    $("#yr-pace").innerHTML = esc(t(diff >= 0 ? "year.pace_ahead" : "year.pace_behind",
      { now: f(p.now), diff: f(Math.abs(diff)), prev: f(p.last_year) }));
    $("#yr-pace").className = `pace ${diff >= 0 ? "up" : "down"}`;
  } else { $("#yr-pace").textContent = ""; }

  const years = d.years.map((y) => y.year);
  const sel = $("#yr-year");
  if (!years.includes(+S.reviewYear)) S.reviewYear = years[0] || thisYear;
  sel.innerHTML = years.map((y) => `<option>${y}</option>`).join("");
  sel.value = S.reviewYear;
  await loadReview();
}

async function loadReview() {
  const year = S.reviewYear;
  const d = await api(`api/review?year=${year}&${query(false)}`);
  if (year !== S.reviewYear) return;
  if (!d.riders.length) { $("#review").innerHTML = `<div class="card empty">${esc(t("review.empty", { year }))}</div>`; return; }
  $("#review").innerHTML = d.riders.map((p) => {
    const max = Math.max(1, ...p.months);
    const bars = p.months.map((v, i) =>
      `<div class="bar${i + 1 === p.top_month.month ? " top" : ""}" title="${esc(monthShort(i))}: ${fmt(v, 1)} km">` +
      `<span style="height:${(v / max) * 100}%;background:${esc(p.color)}"></span><i>${esc(monthShort(i).slice(0, 1))}</i></div>`).join("");
    const r = p.records || {};
    const longest = r.longest_km;
    const facts = [
      [t("review.top_month"), p.top_month.km ? `${monthName(`${year}-${String(p.top_month.month).padStart(2, "0")}`)} · ${fmt(p.top_month.km)} km` : "–"],
      [t("review.longest"), longest ? `${fmt(longest.value, 1)} km · ${shortDate(dt(longest.ts))}` : "–", longest],
      [t("review.streak"), r.day_streak ? `${nDays(r.day_streak.longest.value)} · ${nWeeks(r.week_streak.longest.value)}` : "–"],
      [t("review.days"), fmt(r.riding_days || 0)],
      [t("review.explored"), `${fmt(p.new_km)} km`],
    ];
    return `<div class="card rv"><div class="rv-head"><span class="dot" style="background:${esc(p.color)}"></span><b>${esc(p.name)}</b>
        <span class="rv-km">${fmt(p.km)} km</span></div>
      <div class="rv-kpis"><span><b>${fmt(p.rides)}</b> ${esc(t("review.rides"))}</span><span><b>${fmt(p.hours)}</b> ${esc(t("review.hours"))}</span>
        <span><b>${fmt(p.elevation)}</b> ${esc(t("review.elevation"))}</span></div>
      <div class="months">${bars}</div>
      <dl>${facts.map(([l, v, ride]) => `<dt>${esc(l)}</dt><dd${ride ? ` class="click" data-a="${ride.account_id}" data-id="${esc(ride.id)}"` : ""}>${esc(v)}</dd>`).join("")}</dl>
    </div>`;
  }).join("");
  $$("#review dd.click").forEach((el) => el.onclick = () => openRide(+el.dataset.a, el.dataset.id));
}

/* ------------------------------------------------------------ rides */
const accName = (id) => S.state.accounts.find((a) => a.id === id) || { name: "?", color: "#999" };
const bikeName = (acc, id) => (S.state.bikes.find((b) => b.account_id === acc && b.id === id) || {}).name || "–";
const dt = (ts) => new Date(ts * 1000);

async function loadRides() {
  const q = query();
  const d = await api(`api/rides?${q}`);
  $("#export").href = `api/export.zip?${q}`;
  $("#rides-count").textContent = t("rides.count", { n: fmt(d.rides.length) });
  $("#rides").innerHTML = ridesTable(d.rides.slice(0, 1500), { dateStyle: "medium", timeStyle: "short" });
  $$("#rides tr.click").forEach((tr) => tr.onclick = () => openRide(+tr.dataset.a, tr.dataset.id));
}

function ridesTable(rides, dateOpts) {
  return `<tr><th>${t(dateOpts.dateStyle ? "col.date" : "col.start")}</th><th>${t("col.person")}</th><th>${t("col.bike")}</th><th>${t("col.title")}</th>` +
    `<th class="n">km</th><th class="n">${t("col.time")}</th><th class="n">${t("col.elev_short")}</th><th class="n">${t("col.avg_speed")}</th></tr>` +
    rides.map((r) => {
      const a = accName(r.account_id);
      return `<tr class="click" data-a="${r.account_id}" data-id="${esc(r.id)}">
        <td>${fmtDate(dt(r.start_ts), dateOpts)}</td>
        <td><span class="dot" style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${esc(a.color)}"></span> ${esc(a.name)}</td>
        <td>${esc(bikeName(r.account_id, r.bike_id))}</td><td>${esc(r.title || "")}</td>
        <td class="n">${fmt((r.distance_m || 0) / 1000, 1)}</td>
        <td class="n">${fmt((r.duration_s || 0) / 60)} min</td>
        <td class="n">${fmt(r.elevation_gain_m)}</td><td class="n">${fmt(r.avg_speed_kmh, 1)}</td></tr>`;
    }).join("");
}

let rideMap = null;
async function openRide(acc, id) {
  const all = await api(`api/rides?accounts=${acc}`);
  const r = all.rides.find((x) => x.id === id);
  if (!r) return;
  const dlg = $("#ride-dlg");
  $("#ride-title").textContent = `${r.title || t("ride.default_title")} – ${fmtDate(dt(r.start_ts), { dateStyle: "full", timeStyle: "short" })}`;
  $("#ride-facts").innerHTML = [
    [t("ride.distance"), fmt((r.distance_m || 0) / 1000, 1) + " km"], [t("ride.duration"), fmt((r.duration_s || 0) / 60) + " min"],
    [t("ride.elevation"), fmt(r.elevation_gain_m) + " m"], [t("ride.avg_speed"), fmt(r.avg_speed_kmh, 1) + " km/h"],
    [t("ride.max"), fmt(r.max_speed_kmh, 1) + " km/h"], [t("ride.avg_power"), r.avg_power_w != null ? fmt(r.avg_power_w) + " W" : "–"],
    [t("ride.cadence"), r.avg_cadence != null ? fmt(r.avg_cadence) + " rpm" : "–"],
    [t("ride.calories"), r.calories != null ? fmt(r.calories) + " kcal" : "–"],
  ].map(([l, v]) => `<div><span class="muted small">${l}</span><b>${v}</b></div>`).join("");
  $("#ride-gpx").href = `api/rides/${acc}/${encodeURIComponent(id)}/gpx`;
  const sel = $("#ride-bike");
  sel.innerHTML = `<option value="">${esc(t("ride.bike_unknown"))}</option>` + S.state.bikes.filter((b) => b.account_id === acc)
    .map((b) => `<option value="${esc(b.id)}">${esc(t("ride.bike", { name: b.name }))}</option>`).join("");
  sel.value = r.bike_id || "";
  sel.onchange = async () => { await api(`api/rides/${acc}/${encodeURIComponent(id)}`, { method: "PATCH", body: { bike_id: sel.value } }); await loadState(); };
  dlg.showModal();
  const track = await api(`api/rides/${acc}/${encodeURIComponent(id)}/track`);
  if (!rideMap) {
    rideMap = L.map("ride-map");
    L.tileLayer(TILE_URL, TILE_OPTS).addTo(rideMap);
  }
  rideMap.eachLayer((l) => { if (l instanceof L.Polyline || l instanceof L.CircleMarker) rideMap.removeLayer(l); });
  rideMap.invalidateSize();
  if (track.points.length) {
    const line = L.polyline(track.points.map((p) => [p[0], p[1]]), { color: accName(acc).color, weight: 4 }).addTo(rideMap);
    rideMap.fitBounds(line.getBounds(), { padding: [10, 10] });
    renderProfile(track.points, line, accName(acc).color);
  } else {
    rideMap.setView(...DEFAULT_VIEW);
    renderProfile([], null);
  }
}

function haversineKm(a, b) {
  const R = 6371, rad = Math.PI / 180;
  const dLat = (b[0] - a[0]) * rad, dLon = (b[1] - a[1]) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * rad) * Math.cos(b[0] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

/** Elevation + speed over distance below the ride map; hovering either one marks the spot on the other. */
function renderProfile(points, line, color) {
  if (S.profile) { S.profile.destroy(); S.profile = null; }
  const hasEle = points.some((p) => p[2] != null), hasSpeed = points.some((p) => p[3] != null);
  $("#ride-profile-wrap").hidden = !(points.length > 1 && (hasEle || hasSpeed));
  if ($("#ride-profile-wrap").hidden) return;
  // at most ~600 samples: enough for the chart, cheap to hover
  const step = Math.max(1, Math.ceil(points.length / 600));
  const sample = [], dist = [];
  let km = 0;
  points.forEach((p, i) => {
    if (i) km += haversineKm(points[i - 1], p);
    if (i % step === 0 || i === points.length - 1) { sample.push(p); dist.push(km); }
  });
  const css = getComputedStyle(document.documentElement);
  const grid = css.getPropertyValue("--line").trim(), muted = css.getPropertyValue("--muted").trim();
  // track points are [lat, lon, ele, speed]; Leaflet only takes 2 or 3 values
  const pos = (p) => [p[0], p[1]];
  const marker = L.circleMarker(pos(sample[0]), { radius: 6, color: "#fff", weight: 2, fillColor: color, fillOpacity: 1 });
  const show = (i) => { if (i == null) { marker.remove(); return; } marker.setLatLng(pos(sample[i])).addTo(rideMap); };
  const datasets = [];
  if (hasEle) datasets.push({ label: t("ride.profile_ele"), data: sample.map((p, i) => ({ x: dist[i], y: p[2] })), yAxisID: "ele",
    borderColor: muted, backgroundColor: "rgba(134,142,150,.18)", fill: true, pointRadius: 0, borderWidth: 1.5, tension: 0.2 });
  if (hasSpeed) datasets.push({ label: t("ride.profile_speed"), data: sample.map((p, i) => ({ x: dist[i], y: p[3] })), yAxisID: "spd",
    borderColor: color, pointRadius: 0, borderWidth: 1.5, tension: 0.2 });
  S.profile = new Chart($("#ride-profile"), {
    type: "line", data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      onHover: (_ev, els) => show(els.length ? els[0].index : null),
      plugins: { legend: { labels: { color: muted, boxWidth: 12 } },
        tooltip: { callbacks: { title: (items) => `${fmt(dist[items[0].dataIndex], 1)} km`,
          label: (c) => `${c.dataset.label}: ${fmt(c.parsed.y, c.dataset.yAxisID === "spd" ? 1 : 0)}` } } },
      scales: {
        x: { type: "linear", grid: { display: false }, ticks: { color: muted, callback: (v) => `${fmt(v, v < 10 ? 1 : 0)} km` },
          min: 0, max: dist[dist.length - 1] },
        ele: { display: hasEle, position: "left", grid: { color: grid }, ticks: { color: muted } },
        spd: { display: hasSpeed, position: "right", grid: { display: false }, ticks: { color: muted }, beginAtZero: true },
      },
    },
  });
  // map -> chart: nearest sample to the mouse
  $("#ride-profile").onmouseleave = () => show(null);
  line.on("mousemove", (ev) => {
    let best = 0, bestD = Infinity;
    sample.forEach((p, i) => { const d = (p[0] - ev.latlng.lat) ** 2 + (p[1] - ev.latlng.lng) ** 2; if (d < bestD) { bestD = d; best = i; } });
    const active = datasets.map((_, k) => ({ datasetIndex: k, index: best }));
    S.profile.setActiveElements(active);
    S.profile.tooltip.setActiveElements(active, { x: 0, y: 0 });
    S.profile.update("none");
    show(best);
  });
  line.on("mouseout", () => { S.profile?.setActiveElements([]); S.profile?.tooltip.setActiveElements([], { x: 0, y: 0 }); S.profile?.update("none"); show(null); });
}

/* ------------------------------------------------------------ accounts */
function renderAccounts() {
  const st = S.state;
  const unassigned = Object.fromEntries(st.unassigned.map((u) => [u.account_id, u.n]));
  $("#acc-list").innerHTML = st.accounts.length ? st.accounts.map((a) => {
    const p = st.progress[a.id];
    const bikes = st.bikes.filter((b) => b.account_id === a.id);
    return `<div class="card"><div class="acc">
      <div class="who"><input type="color" class="swatch" data-color="${a.id}" value="${esc(a.color)}" title="${esc(t("acc.color_change"))}"><b>${esc(a.name)}</b>
        <span class="badge ${a.status}">${esc(t("status." + a.status))}</span>
        <span class="muted small">${t(a.source === "dataact" ? "acc.src_short_dataact" : "acc.src_short_mobile")}</span></div>
      <div class="row">
        <button class="btn ${a.status === "ok" ? "" : "primary"}" data-act="login" data-id="${a.id}">${t(a.status === "ok" ? "acc.relogin" : "acc.login")}</button>
        <button class="btn" data-act="sync" data-id="${a.id}" ${a.status === "ok" ? "" : "disabled"}>${t("acc.sync")}</button>
        <button class="btn danger" data-act="del" data-id="${a.id}">${t("acc.remove")}</button>
      </div></div>
      ${p ? `<div class="progress">${p.phase === "tracks" ? t("acc.progress_tracks", { done: p.done, total: p.total }) : t("acc.progress_list")}</div>` : ""}
      ${a.last_sync ? `<div class="muted small">${esc(t("acc.last_sync", { when: fmtDate(dt(a.last_sync)) }))}</div>` : ""}
      ${a.last_error ? `<div class="err small">${esc(a.last_error)}</div>` : ""}
      ${bikes.length ? `<div class="bikes">${bikes.map((b) => `🚲 ${esc(b.name)} · ${t("acc.bike_line", { n: fmt(b.rides) })}${b.odometer_m ? ` · ${fmt(b.odometer_m / 1000)} km` : ""}`).join("<br>")}
        ${unassigned[a.id] ? `<br>⚠️ ${esc(t("acc.unassigned", { n: unassigned[a.id] }))}` : ""}</div>` : ""}
    </div>`;
  }).join("") : `<div class="card empty">${esc(t("acc.empty"))}</div>`;
  $$("#acc-list [data-act]").forEach((b) => b.onclick = () => accountAction(b.dataset.act, +b.dataset.id));
  // "change" fires once the picker is closed, so dragging through colours does not spam the API.
  $$("#acc-list input[data-color]").forEach((inp) => inp.onchange = async () => {
    try {
      await api(`api/accounts/${inp.dataset.color}`, { method: "PATCH", body: { color: inp.value } });
      await loadState();
      S.fitted = true;  // keep the current map view
    } catch (e) { alert(e.message); }
  });
  $("#bridge-info").textContent = st.ha_bridge ? t("acc.bridge") : "";
}

async function accountAction(act, id) {
  const a = S.state.accounts.find((x) => x.id === id);
  if (act === "del") {
    if (!confirm(t("acc.confirm_delete", { name: a.name }))) return;
    await api(`api/accounts/${id}`, { method: "DELETE" });
  } else if (act === "sync") {
    await api(`api/accounts/${id}/sync`, { method: "POST" });
  } else if (act === "login") {
    return openLogin(a);
  }
  await loadState();
}

async function openLogin(a) {
  const { url } = await api(`api/accounts/${a.id}/login`, { method: "POST" });
  $("#login-name").textContent = a.name;
  $("#login-link").href = url;
  $("#login-paste").value = "";
  $("#login-err").textContent = "";
  // Step texts are trusted HTML from i18n.js; only the account name is user input.
  $("#login-steps").innerHTML = t(a.source === "dataact" ? "login.dataact_steps" : "login.mobile_steps")
    .map((s) => `<li>${s.replace("{name}", esc(a.name))}</li>`).join("");
  const dlg = $("#login-dlg");
  // Live check of the pasted text, same rules as the backend (code=, state= anywhere in it).
  const expected = new URL(url).searchParams.get("state");
  const param = (s, k) => (s.match(new RegExp(`[?&#]${k}=([^&#\\s'"<>]+)`)) || [])[1];
  $("#login-check").textContent = "";
  $("#login-paste").oninput = () => {
    const s = $("#login-paste").value.trim(), code = param(s, "code"), state = param(s, "state");
    const el = $("#login-check");
    el.className = "small " + (code && (!state || state === expected) ? "ok" : "muted");
    el.textContent = !s ? "" : !code ? t("login.check_none")
      : state && state !== expected ? t("login.check_state") : t("login.check_ok");
  };
  $("#login-ok").onclick = async (ev) => {
    ev.preventDefault();
    try {
      await api(`api/accounts/${a.id}/login/finish`, { method: "POST", body: { pasted: $("#login-paste").value } });
      dlg.close(); await loadState();
    } catch (e) { $("#login-err").textContent = e.message; }
  };
  dlg.showModal();
}

$("#acc-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  try {
    const acc = await api("api/accounts", { method: "POST", body: Object.fromEntries(f) });
    ev.target.reset();
    await loadState();
    openLogin(acc);
  } catch (e) { alert(e.message); }
});
$("#acc-form [name=source]").onchange = (e) => { $("#acc-form [name=client_id]").hidden = e.target.value !== "dataact"; };

/* ------------------------------------------------------------ wiring */
async function loadState() {
  S.state = await api("api/state");
  renderFilters();
  if (S.tab === "accounts") renderAccounts();
  return S.state;
}

async function refresh() {
  try {
    if (S.tab === "map") await loadMap();
    if (S.tab === "stats") await loadStats();
    if (S.tab === "year") await loadYear();
    if (S.tab === "rides") await loadRides();
    if (S.tab === "accounts") renderAccounts();
  } catch (e) { console.error(e); }
}

$$("nav button").forEach((b) => b.onclick = () => {
  $$("nav button").forEach((x) => x.classList.toggle("active", x === b));
  S.tab = b.dataset.tab;
  $$(".tab").forEach((el) => el.classList.toggle("active", el.id === `tab-${S.tab}`));
  $("#filters").hidden = S.tab === "accounts";
  syncStatsControls();
  refresh();
});
// Restyle in place while dragging; no refetch needed.
$("#map-intensity").value = localStorageGet("intensity") || 5;
$("#map-intensity").oninput = (e) => {
  localStorageSet("intensity", e.target.value);
  if (!S.layer) return;
  if (S.mapMode === "heat") { S.layer._draw(); renderLegend(S.heatMax); }
  else S.layer.setStyle(lineStyle());
};
$$("#map-mode button").forEach((b) => b.onclick = () => {
  $$("#map-mode button").forEach((x) => x.classList.toggle("active", x === b));
  S.mapMode = b.dataset.mode; loadMap();
});
$$("#st-group button").forEach((b) => b.onclick = () => {
  S.group = b.dataset.g; localStorageSet("group", S.group); loadStats();
});
// Day view: page one week back/forward, jump to a date, or back to today.
const setDayEnd = (d) => {
  S.dayEnd = !d || d >= today() ? null : d;
  S.daySel = d && d < today() ? d : today();
  loadStats();
};
$("#day-prev").onclick = () => { const e = addDays(S.dayEnd || today(), -7); S.dayEnd = e; S.daySel = e; loadStats(); };
$("#day-next").onclick = () => { const e = addDays(S.dayEnd || today(), 7); setDayEnd(e); };
$("#day-today").onclick = () => setDayEnd(null);
$("#day-date").onchange = (e) => { if (e.target.value) setDayEnd(e.target.value); };
$$("#st-metric button").forEach((b) => b.onclick = () => {
  $$("#st-metric button").forEach((x) => x.classList.toggle("active", x === b));
  S.metric = b.dataset.m; loadStats();
});
$("#st-cum").onchange = (e) => { S.cum = e.target.checked; loadStats(); };
$$("#yr-metric button").forEach((b) => b.onclick = () => {
  $$("#yr-metric button").forEach((x) => x.classList.toggle("active", x === b));
  S.yearMetric = b.dataset.m; loadYear();
});
$("#yr-year").onchange = (e) => { S.reviewYear = +e.target.value; loadReview(); };
$("#f-range").onchange = (e) => { $("#f-custom").hidden = e.target.value !== "custom"; S.fitted = false; refresh(); };
$("#f-from").onchange = $("#f-to").onchange = $("#f-bike").onchange = () => { S.fitted = false; refresh(); };

(async function boot() {
  await loadState();
  initLanguage(S.state.ha_language);
  renderFilters();
  if (!S.state.accounts.length) $("nav button[data-tab=accounts]").click();
  else refresh();
  // Keep progress / status fresh while a sync runs.
  setInterval(async () => {
    const busy = Object.keys(S.state.progress || {}).length;
    if (!busy && S.tab !== "accounts") return;
    await loadState();
    if (!Object.keys(S.state.progress).length && busy) refresh();
  }, 4000);
})();
