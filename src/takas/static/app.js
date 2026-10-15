const TONES = 5; // route inks in style.css: red, blue, green, yellow, orange
const $ = (s) => document.querySelector(s);
let HOME, start; // both come from /api/meta
let map, marker, layers = [], walkLines = [], current = [];

const KEY_START = "takas.start", KEY_SET = "takas.settings";
// what was looked at goes to the server's activity log (data/logs, see `takas logs`)
const track = (event, data) => fetch("/api/log", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ event, data }), keepalive: true,
}).catch(() => {});
window.addEventListener("error", (e) => track("error", { message: String(e.message), source: e.filename, line: e.lineno }));
window.addEventListener("unhandledrejection", (e) => track("error", { message: String(e.reason) }));
const store = (k, v) => localStorage.setItem(k, JSON.stringify(v));
const load = (k) => { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } };

function setStart(p, save = true) {
  start = [p[0], p[1]];
  marker.setLatLng(start);
  $("#start-info").textContent = `${start[0].toFixed(5)}, ${start[1].toFixed(5)}`;
  if (save) store(KEY_START, start);
}

function init(meta) {
  HOME = meta.home;
  start = [...HOME];
  if (!meta.features?.plan) document.querySelector('#tabs button[data-tab="day"]').remove();
  map = L.map("map").setView(HOME, 13);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "© OpenStreetMap contributors",
  }).addTo(map);
  const pin = L.divIcon({ className: "start-pin", iconSize: [18, 18] });
  marker = L.marker(HOME, { draggable: true, title: "Start (drag to move)", icon: pin }).addTo(map);
  marker.on("dragend", () => { const p = marker.getLatLng(); setStart([p.lat, p.lng]); });
  map.on("click", (e) => setStart([e.latlng.lat, e.latlng.lng]));
  const [s, w, n, e] = meta.bbox, saved = load(KEY_START);
  const inside = Array.isArray(saved) && saved[0] >= s && saved[0] <= n && saved[1] >= w && saved[1] <= e;
  if (inside) map.setView(saved, 13);
  setStart(inside ? saved : HOME, false);
  const set = load(KEY_SET) || {};
  if (set.walk_min != null) $("#walk-min").value = set.walk_min;
  if (set.uphill_pace) $("#uphill-pace").value = set.uphill_pace;
  if (set.warmup_km) $("#warmup-km").value = set.warmup_km;
  $("#loops-walk").checked = !!set.loops_walk;
  if (meta.degraded) {
    const b = $("#banner");
    b.hidden = false;
    b.innerHTML = "<h3>Degraded elevation</h3><p>Copernicus canopy DEM, not FABDEM. Gain figures are less reliable and the hill finder is off.</p>";
  }
  $("#form-day [name=date]").value = new Date(Date.now() - new Date().getTimezoneOffset() * 6e4).toISOString().slice(0, 10);
  showStale(meta);
  setInterval(() => fetch("/api/meta").then((r) => r.json()).then(showStale).catch(() => {}), 60000);
}

// a running server keeps the code and map it started with; say so when newer ones are on disk
function showStale(meta) {
  const el = $("#stale");
  el.hidden = !meta.stale;
  if (meta.stale) {
    el.innerHTML = `<h3>Restart takas</h3><p>This server started at ${meta.server_started_at.slice(11, 16)}, before
      ${meta.stale_files.join(", ")} changed on disk. Stop it and run <code>uv run takas serve</code> again to use them.</p>`;
  }
}

document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("#tabs button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  document.querySelectorAll("form[data-tab]").forEach((f) => { f.hidden = f.dataset.tab !== b.dataset.tab; });
}));
$("#reset-home").addEventListener("click", () => {
  localStorage.removeItem(KEY_START);
  setStart(HOME, false);
  map.setView(HOME, 13);
});
const walkMin = () => Math.max(0, Number($("#walk-min").value) || 0);
const loopsWalk = () => $("#loops-walk").checked;
// "5:30" -> 330 s/km; null when blank or malformed so the server falls back to its grade model
const upPace = () => { const m = /^(\d{1,2}):(\d{2})$/.exec($("#uphill-pace").value.trim()); return m ? +m[1] * 60 + +m[2] : null; };
const fmtPace = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
const warmupKm = () => Number($("#warmup-km").value) || null;
["#walk-min", "#loops-walk", "#uphill-pace", "#warmup-km"].forEach((id) => $(id).addEventListener("change", () =>
  store(KEY_SET, { walk_min: walkMin(), loops_walk: loopsWalk(), uphill_pace: $("#uphill-pace").value.trim(),
                   warmup_km: warmupKm() })));

const startBody = () => ({ lat: start[0], lon: start[1] });
const num = (f, n) => { const v = f.elements[n].value; return v === "" ? null : Number(v); };
const note = (label, text) => `<p class="chgnote"><b>${label}</b>${text}</p>`;

async function call(label, url, opts) {
  $("#status").textContent = `${label}…`;
  $("#cards").innerHTML = ""; $("#notes").innerHTML = ""; $("#profile").innerHTML = "";
  const t = performance.now();
  try {
    const r = await fetch(url, opts);
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || r.statusText);
    $("#status").textContent = `${((performance.now() - t) / 1000).toFixed(1)} s`;
    return body;
  } catch (e) {
    $("#status").textContent = "";
    $("#notes").innerHTML = note("Error", String(e.message || e));
    return null;
  }
}
const post = (b) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });

$("#form-loop").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  $("#day-info").innerHTML = "";
  render(await call("Generating", "/api/loop", post({
    km: num(f, "km"), profile: f.elements.profile.value, terrain: f.elements.terrain.value,
    gain_target: num(f, "gain_target"), lit: f.elements.lit.checked, start: startBody(),
    walk_max_min: loopsWalk() ? walkMin() : 0,
  })));
});
$("#form-hills").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  $("#day-info").innerHTML = "";
  render(await call("Searching climbs", "/api/hills", post({
    reps: num(f, "reps"), minutes: num(f, "minutes"), steep: f.elements.steep.checked,
    warmup_km: warmupKm(), cool_min: num(f, "cool_min"), start: startBody(), uphill_pace: upPace(),
  })));
});
$("#form-day").addEventListener("submit", async (e) => {
  e.preventDefault();
  const date = e.target.elements.date.value;
  const res = await call("Loading day", `/api/day/${date}?lat=${start[0]}&lon=${start[1]}&walk_max_min=${walkMin()}&loops_walk=${loopsWalk()}${upPace() ? `&uphill_pace=${upPace()}` : ""}${warmupKm() ? `&warmup_km=${warmupKm()}` : ""}`);
  if (!res) return;
  const d = res.day;
  $("#day-info").innerHTML = `<div class="eyebrow">${d.date} · ${d.type} · ${d.km ?? "?"} km</div>
    <h3>${d.session}</h3><p>${d.structure}</p>`;
  render(res);
});

function clearMap() {
  [...layers, ...walkLines, ...overlay].forEach((l) => l.remove()); layers = []; walkLines = []; overlay = [];
  if (legend) setLegend([]);
}

function fmtTime(s) { const m = Math.round(s / 60); return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`; }
const stat = (label, fig, unit = "") => `<div><span>${label}</span><b>${fig}${unit ? ` <small>${unit}</small>` : ""}</b></div>`;

function render(res) {
  clearMap();
  if (!res) return;
  const cands = [...(res.candidates || [])];
  if (res.fallback) cands.push({ ...res.fallback.candidate, fallback: res.fallback });
  current = cands;
  setLegend(cands);
  $("#notes").innerHTML = (res.notes || []).map((n) => note("Note", n)).join("");
  $("#cards").innerHTML = "";
  cands.forEach((c, i) => {
    const tone = `t${i % TONES}` + (c.fallback ? " dash" : "");
    if (c.walk) walkLines.push(L.polyline(c.walk.coords, { className: "rt-walk", weight: 3.5, dashArray: "0.5 7" }).addTo(map));
    const line = L.polyline(c.coords, { className: `rt ${tone}`, weight: 3, opacity: 0.75, dashArray: c.fallback ? "10 7" : null }).addTo(map);
    line.on("click", (ev) => { L.DomEvent.stopPropagation(ev); select(i); });
    layers.push(line);
    const el = document.createElement("div");
    el.className = "cand" + (c.fallback ? " fallback" : "");
    const title = c.fallback ? `Fallback: ${c.fallback.description}`
      : (c.climb ? `Climb ${c.climb.length.toFixed(0)} m · ${(c.climb.grade * 100).toFixed(1)} %` : `Loop ${c.km.toFixed(1)} km`);
    el.innerHTML = `<h4><i class="sw ${tone}"></i>${title}<span class="rank">#${i + 1}</span></h4>
      <div class="stats">
        ${stat("Distance", c.km.toFixed(2), "km")}${stat("Gain", `+${c.gain}`, "m")}${stat("Time", fmtTime(c.time_s))}
        ${stat("Paved", c.paved_pct.toFixed(0), "%")}${stat("Green", c.green_pct.toFixed(0), "%")}${stat("Retrace", c.retrace_pct.toFixed(0), "%")}
        ${stat("Lit", c.lit_pct.toFixed(0), "%")}${stat("Max grade", c.max_grade.toFixed(0), "%")}${stat("Loss", `−${c.loss}`, "m")}
      </div>
      ${c.walk ? `<p class="eat"><b>Walk first</b>${c.walk.km.toFixed(1)} km (${fmtTime(c.walk.time_s)}) to the run start</p>` : ""}
      ${c.warmup ? `<p class="eat"><b>Session</b>Warm-up ${(c.warmup.m / 1000).toFixed(1)} km to the foot (≈ ${fmtTime(c.warmup.run_s)} running · ${fmtTime(c.warmup.walk_s)} walking) → ${c.climb.reps} repeats → cool-down ${(segLen(c, "cool-down") / 1000).toFixed(1)} km</p>` : ""}
      ${c.repeat ? `<p class="eat"><b>Repeats</b>${c.climb.reps} × run up ${fmtTime(c.repeat.up_s)} at ${fmtPace(c.repeat.pace)}/km (${c.repeat.up_m} m), walk down (${fmtTime(c.repeat.down_s)})</p>` : ""}
      <div class="links"><a href="/api/gpx/${c.id}" download>GPX${c.walk ? " · run only" : ""}</a>
      ${c.walk ? `<a href="/api/gpx/${c.id}?walk=1" download>GPX · walk + run</a>` : ""}</div>`;
    el.addEventListener("click", () => select(i, false, true));
    el.querySelectorAll(".links a").forEach((a) => a.addEventListener("click", (ev) => {
      ev.stopPropagation();
      track("gpx", { id: c.id, walk: a.href.includes("walk=1") });
    }));
    $("#cards").appendChild(el);
  });
  if (cands.length) {
    map.fitBounds(L.latLngBounds(cands.flatMap((c) => [...(c.walk ? c.walk.coords : []), ...c.coords])), { padding: [30, 30] });
    select(0, true);
  }
}

let overlay = [], legend;
const DEG = Math.PI / 180;
// screen bearing, degrees clockwise from north, between two [lat, lon] points
const bearing = (a, b) => Math.atan2((b[1] - a[1]) * Math.cos(a[0] * DEG), b[0] - a[0]) / DEG;
const idxAt = (c, m) => { const k = c.dist.findIndex((d) => d >= m - 0.5); return k < 0 ? c.dist.length - 1 : k; };
const pin = (latlng, html, cls) => L.marker(latlng, {
  icon: L.divIcon({ className: `pin ${cls}`, html: `<span class="lbl">${html}</span>`, iconSize: [10, 10] }),
  interactive: false, keyboard: false,
});
const chevron = (c, k, cls) => {
  const a = c.coords[Math.max(0, k - 2)], b = c.coords[Math.min(c.coords.length - 1, k + 2)];
  return L.marker(c.coords[k], {
    icon: L.divIcon({ className: `arr ${cls}`, html: `<i style="transform:rotate(${bearing(a, b).toFixed(0)}deg)">▲</i>`, iconSize: [16, 16] }),
    interactive: false, keyboard: false,
  });
};
const segLen = (c, name) => (c.segments || []).filter((s) => s.name === name).reduce((m, s) => m + s.to_m - s.from_m, 0);

function setLegend(cands) {
  if (!legend) {
    legend = L.control({ position: "bottomleft" });
    legend.onAdd = () => L.DomUtil.create("div", "map-legend");
    legend.addTo(map);
  }
  const rows = [];
  if (cands.length) rows.push('<div><i class="lg"></i>Run · one colour per candidate · ▲ arrows show the way</div>');
  if (cands.some((c) => c.segments)) rows.push('<div><i class="lg rep"></i>Repeat · run up to ▲ top, walk back to foot</div>');
  if (cands.some((c) => c.walk)) rows.push('<div><i class="lg walk"></i>Walk to where the run starts</div>');
  if (cands.some((c) => c.fallback)) rows.push('<div><i class="lg fb"></i>Fallback: run there, repeat a circuit</div>');
  const el = legend.getContainer();
  el.innerHTML = rows.join("");
  el.hidden = !rows.length;
}

const calm = matchMedia("(prefers-reduced-motion: reduce)");

// fit the map to one candidate: its route plus any walk to where it starts
function focus(c) {
  const b = L.latLngBounds([...(c.walk ? c.walk.coords : []), ...c.coords]);
  if (calm.matches) map.fitBounds(b, { padding: [40, 40] });
  else map.flyToBounds(b, { padding: [40, 40], duration: 0.6 });
}

function select(i, auto = false, fit = false) {
  overlay.forEach((l) => l.remove()); overlay = [];
  track("select", { id: current[i].id, rank: i + 1, auto });
  if (fit) focus(current[i]);
  document.querySelectorAll(".cand").forEach((el, k) => el.classList.toggle("sel", k === i));
  layers.forEach((l, k) => l.setStyle({ weight: k === i ? 6 : 3.5, opacity: k === i ? 1 : 0.8 }));
  const c = current[i];
  const add = (l) => { overlay.push(l.addTo(map)); return l; };
  // paper-coloured casing so the chosen route reads over the others and the grey basemap
  add(L.polyline(c.coords, { className: "rt-halo", weight: 11, opacity: 1, interactive: false })).bringToFront();
  layers[i].bringToFront();
  const up = (c.segments || []).find((s) => s.name === "up");
  const marks = [];  // places already labelled or arrowed, so retraced stretches don't stack arrows
  if (up) {
    // the repeat: striped like the plan's quality-session cells, foot and top labelled, uphill arrow
    const k0 = idxAt(c, up.from_m), k1 = idxAt(c, up.to_m);
    const rise = Math.round(c.elev[k1] - c.elev[k0]);
    add(L.polyline(c.coords.slice(k0, k1 + 1), { className: "rt-up", weight: 3, dashArray: "3 5", bubblingMouseEvents: false }))
      .bindTooltip(`${c.climb.reps} × ${fmtTime(c.repeat.up_s)} uphill · ${c.repeat.up_m} m · +${rise} m · ${(c.climb.grade * 100).toFixed(1)} %<br>`
        + `then walk back down to the foot (${fmtTime(c.repeat.down_s)})`, { sticky: true, className: "tip" });
    add(pin(c.coords[k0], `Foot · ${Math.round(c.elev[k0])} m`, "foot"));
    add(pin(c.coords[k1], `▲ Top · ${Math.round(c.elev[k1])} m`, "top"));
    add(chevron(c, Math.round((k0 + k1) / 2), "up"));
    marks.push(c.coords[k0], c.coords[k1], c.coords[Math.round((k0 + k1) / 2)]);
  }
  if (c.walk) {
    add(pin([c.walk.to.lat, c.walk.to.lon], "Run starts here", "foot"));
    marks.push([c.walk.to.lat, c.walk.to.lon]);
  }
  // direction arrows on the running parts (not the repeat itself), spaced along the route
  const total = c.dist[c.dist.length - 1];
  const step = Math.max(400, total / 14);
  const skip = (m) => (c.segments || []).some((s) => (s.name === "up" || s.name === "walk down") && m >= s.from_m && m <= s.to_m);
  for (let m = step / 2; m < total; m += step) {
    if (skip(m)) continue;
    const k = idxAt(c, m);
    if (marks.some((p) => map.distance(p, c.coords[k]) < 150)) continue;
    add(chevron(c, k, `t${i % TONES}`));
    marks.push(c.coords[k]);
  }
  walkLines.forEach((l) => l.bringToFront());
  profile(c);
}

function profile(c) {
  const W = 340, H = 130, P = 30;
  const xs = c.dist, zs = c.elev;
  const maxX = xs[xs.length - 1];
  const lo = Math.min(...zs), hi = Math.max(...zs);
  const sx = (x) => P + (x / maxX) * (W - P - 6);
  const sy = (z) => H - 16 - ((z - lo) / Math.max(hi - lo, 1)) * (H - 28);
  const path = xs.map((x, k) => `${k ? "L" : "M"}${sx(x).toFixed(1)},${sy(zs[k]).toFixed(1)}`).join("");
  const area = `${path}L${sx(maxX)},${H - 16}L${sx(0)},${H - 16}Z`;
  $("#profile").innerHTML = `<div class="label">Elevation</div><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <path class="area" d="${area}"/><path class="ln" d="${path}"/>
    <text x="0" y="12">${hi.toFixed(0)} M</text><text x="0" y="${H - 18}">${lo.toFixed(0)} M</text>
    <text x="${W - 44}" y="${H - 3}">${(maxX / 1000).toFixed(1)} KM</text></svg>`;
}

fetch("/api/meta").then((r) => r.json()).then(init);
