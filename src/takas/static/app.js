const TONES = 5; // route inks in style.css: red, blue, green, yellow, orange
const $ = (s) => document.querySelector(s);
let HOME, start; // both come from /api/meta
let map, marker, layers = [], walkLines = [], current = [];

const KEY_START = "takas.start", KEY_SET = "takas.settings";
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
  $("#loops-walk").checked = !!set.loops_walk;
  if (meta.degraded) {
    const b = $("#banner");
    b.hidden = false;
    b.innerHTML = "<h3>Degraded elevation</h3><p>Copernicus canopy DEM, not FABDEM. Gain figures are less reliable and the hill finder is off.</p>";
  }
  $("#form-day [name=date]").value = new Date(Date.now() - new Date().getTimezoneOffset() * 6e4).toISOString().slice(0, 10);
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
["#walk-min", "#loops-walk", "#uphill-pace"].forEach((id) => $(id).addEventListener("change", () =>
  store(KEY_SET, { walk_min: walkMin(), loops_walk: loopsWalk(), uphill_pace: $("#uphill-pace").value.trim() })));

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
    warm_min: num(f, "warm_min"), cool_min: num(f, "cool_min"), start: startBody(),
    walk_max_min: walkMin(), uphill_pace: upPace(),
  })));
});
$("#form-day").addEventListener("submit", async (e) => {
  e.preventDefault();
  const date = e.target.elements.date.value;
  const res = await call("Loading day", `/api/day/${date}?lat=${start[0]}&lon=${start[1]}&walk_max_min=${walkMin()}&loops_walk=${loopsWalk()}${upPace() ? `&uphill_pace=${upPace()}` : ""}`);
  if (!res) return;
  const d = res.day;
  $("#day-info").innerHTML = `<div class="eyebrow">${d.date} · ${d.type} · ${d.km ?? "?"} km</div>
    <h3>${d.session}</h3><p>${d.structure}</p>`;
  render(res);
});

function clearMap() { [...layers, ...walkLines, ...overlay].forEach((l) => l.remove()); layers = []; walkLines = []; overlay = []; }

function fmtTime(s) { const m = Math.round(s / 60); return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`; }
const stat = (label, fig, unit = "") => `<div><span>${label}</span><b>${fig}${unit ? ` <small>${unit}</small>` : ""}</b></div>`;

function render(res) {
  clearMap();
  if (!res) return;
  const cands = [...(res.candidates || [])];
  if (res.fallback) cands.push({ ...res.fallback.candidate, fallback: res.fallback });
  current = cands;
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
      ${c.repeat ? `<p class="eat"><b>Repeats</b>${c.climb.reps} × run up ${fmtTime(c.repeat.up_s)} at ${fmtPace(c.repeat.pace)}/km (${c.repeat.up_m} m), walk down (${fmtTime(c.repeat.down_s)})</p>` : ""}
      <div class="links"><a href="/api/gpx/${c.id}" download>GPX${c.walk ? " · run only" : ""}</a>
      ${c.walk ? `<a href="/api/gpx/${c.id}?walk=1" download>GPX · walk + run</a>` : ""}</div>`;
    el.addEventListener("click", () => select(i));
    $("#cards").appendChild(el);
  });
  if (cands.length) {
    map.fitBounds(L.latLngBounds(cands.flatMap((c) => [...(c.walk ? c.walk.coords : []), ...c.coords])), { padding: [30, 30] });
    select(0);
  }
}

let overlay = [];
function select(i) {
  overlay.forEach((l) => l.remove()); overlay = [];
  document.querySelectorAll(".cand").forEach((el, k) => el.classList.toggle("sel", k === i));
  layers.forEach((l, k) => l.setStyle({ weight: k === i ? 6 : 3.5, opacity: k === i ? 1 : 0.8 }));
  const c = current[i];
  // paper-coloured casing so the chosen route reads over the others and the grey basemap
  overlay.push(L.polyline(c.coords, { className: "rt-halo", weight: 11, opacity: 1, interactive: false }).addTo(map));
  overlay[0].bringToFront();
  layers[i].bringToFront();
  if (c.segments) {
    // uphill repeats striped like the plan's quality-session cells
    for (const s of c.segments.filter((s) => s.name === "up")) {
      const pts = c.coords.filter((_, k) => c.dist[k] >= s.from_m - 0.5 && c.dist[k] <= s.to_m + 0.5);
      overlay.push(L.polyline(pts, { className: "rt-up", weight: 3, dashArray: "3 5", interactive: false }).addTo(map));
    }
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
