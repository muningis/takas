# takas

*takas* is Lithuanian for "path". A local running-route generator for Vilnius: pick a distance,
surface and terrain, and it proposes loops from your start point, plus steep hill-repeat sessions.
Routes are computed only from OpenStreetMap and elevation data downloaded to disk, shown on a map
with stats and an elevation profile, and exported as GPX 1.1 for any watch app that imports routes.
Online OSM tiles are used only as the map background.

## Features

- **Loops** of a target distance (±3 %) on `road`, `mixed` or `trail` surfaces; `flat`, `any` or
  `hilly` terrain with an optional gain target; optionally prefer lit streets. Up to four diverse
  candidates, ranked by distance fit, retracing, surface and terrain.
- **Hill repeats**: warm-up + N × M minutes uphill + cool-down, all by time. A hill qualifies when
  it is steep (≥ 8 %, ideally ~10 %; Vert mode ≥ 12 % for power-hiking) and long enough for M
  minutes at *your* uphill pace. You run up, walk down.
- **Warm-up walk**: a run may start up to N minutes' walk away when that reaches a better hill (or,
  if you tick the box, a better loop). The walk is drawn dotted; GPX comes as run-only or walk + run.
- **Start point**: click the map or drag the pin; it is kept in the browser's localStorage.
- **Plan day** (optional, off by default): turn one day of a training-plan file into a route
  request. See [docs/plan.md](docs/plan.md) for the file format and the mapping.

## Setup

```sh
uv sync                 # Python 3.13
uv run takas fetch      # Lithuania OSM extract (~220 MB, md5-checked) + FABDEM tile N54E025 (~20 MB) -> data/
uv run takas build      # data/build/graph.npz (~80 s)
uv run takas serve      # http://127.0.0.1:8742
```

## Configuration

Copy [`takas.example.toml`](takas.example.toml) to `takas.toml` (gitignored) to set your own start
point, the graph radius and the optional Plan day feature. Rebuild the graph after changing `home`
or `radius_km`. Environment overrides: `TAKAS_HOME="lat,lon"`, `TAKAS_FEATURE_PLAN=1`,
`TAKAS_PLAN=/path/to/plan.json`, `TAKAS_CONFIG=/path/to/takas.toml`.

## Refreshing data

```sh
uv run takas fetch --force && uv run takas build
```

Geofabrik updates the extract daily. The DEM never needs refreshing. Restart `takas serve` after a
rebuild.

`takas fetch --dem copernicus` is a **degraded mode**: Copernicus GLO-30 is a surface model, so
forest canopy shows up as fake climbs. The UI shows a banner and the hill finder refuses to run.

## How the data is treated

- Carriageways tagged `sidewalk=separate` are dropped, so routes use the separately mapped
  sidewalks. `oneway` is ignored; only `oneway:foot` counts.
- Bridges and tunnels get linearly interpolated elevation, so river crossings do not read as dips.
- Gain is counted with a 3 m hysteresis, like a watch; "max grade" is the steepest 100 m.

## API

`GET /api/meta` · `POST /api/loop` · `POST /api/hills` · `GET /api/gpx/{id}` ·
`GET /api/day/{YYYY-MM-DD}` (only with the Plan day feature on).

## Development

```sh
uv run pytest                      # synthetic-grid tests; no downloaded data needed
uvx ruff check src tests
typos src tests README.md docs
```

## Licences and attribution

- Code: [MIT](LICENSE).
- Fonts: Archivo and JetBrains Mono, SIL Open Font License 1.1 (texts in `src/takas/static/fonts/`).
- Map data © OpenStreetMap contributors, [ODbL](https://opendatacommons.org/licenses/odbl/);
  extract from Geofabrik. Not included in this repository.
- Elevation: FABDEM V1-2 (Hawker et al., University of Bristol),
  [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/), personal non-commercial
  use only. Downloaded by `takas fetch`, not included in this repository.
