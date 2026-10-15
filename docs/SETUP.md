# Setting up takas

This guide takes a fresh machine to a running takas, a local running-route generator for Vilnius.
It is written so a person or an AI coding agent can follow it step by step. Each step says what
to run, what success looks like, and what to do when it fails. Do the steps in order; each step
assumes the previous one passed.

## Facts at a glance

| | |
|---|---|
| Runs on | macOS or Linux, Apple Silicon or x86-64 |
| Language | Python 3.13, managed by [uv](https://docs.astral.sh/uv/) (`.python-version` pins 3.13) |
| Area | Vilnius: OSM extract of Lithuania + elevation tile `N54E025` (54–55° N, 25–26° E) |
| Disk | about 270 MB: OSM 213 MB, elevation 20 MB, graph 33 MB (all under `data/`) |
| Network | needed for installing (uv, Python, packages), `takas fetch` (Geofabrik, University of Bristol), and in the browser for the map library (unpkg.com) and background tiles (openstreetmap.org). Routing itself is offline. |
| Time | download a few minutes, graph build about 80 s |
| Web app | `http://127.0.0.1:8742` (localhost only) |
| Personal settings | `takas.toml` in the repo root, gitignored |
| Generated data and logs | `data/`, gitignored |

## 1. Get the code

Run:

```sh
git clone https://github.com/muningis/takas.git
cd takas
```

Expect: a `takas/` folder with `pyproject.toml`, `src/takas/` and `tests/`.

Every later command runs from this folder.

## 2. Install uv and the dependencies

Check for uv:

```sh
uv --version
```

If the command is not found, install uv (it brings its own Python):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then install the project:

```sh
uv sync
```

Expect: uv creates `.venv/` with Python 3.13 and installs `osmium`, `rasterio`, `scipy`, `shapely`,
`numpy`, `fastapi`, `uvicorn`, `httpx` and the test tools. All of them ship prebuilt wheels for
3.13, so nothing compiles.

If it fails:
- "No interpreter found for Python 3.13": run `uv python install 3.13`, then `uv sync` again.
- A wheel fails to build (rare, usually an old OS): make sure uv uses 3.13 (`uv run python -V`). Do
  not switch to another Python version; the pins expect 3.13.

## 3. Check the install, no data needed

Run:

```sh
uv run pytest -q
```

Expect: `N passed` and no failures. The tests build a small synthetic town in a temporary folder;
they never read `data/` or your `takas.toml`.

If it fails, stop and fix this first. Every later step depends on a working install.

## 4. Optional: set your start point

Without a config file, runs start at Cathedral Square, Vilnius (54.6859, 25.2877). To use your own
start point:

```sh
cp takas.example.toml takas.toml
```

Then edit `takas.toml`:

```toml
home = [54.6859, 25.2877]   # lat, lon of where your runs usually start
radius_km = 15              # the map graph covers home ± this; 15 km allows loops up to ~40 km
```

Rules:
- `home` must lie inside the elevation tile: 54–55° N, 25–26° E. That covers Vilnius and
  surroundings. Anywhere else needs code changes in `src/takas/config.py`: `PBF_URL` (the OSM
  extract is Lithuania only), `FABDEM_ZIP_URL` and `FABDEM_MEMBER` (the elevation tile), and
  `COPERNICUS_URL` (the fallback tile).
- Set `home` before step 6. If you change `home` or `radius_km` later, run `uv run takas build`
  again.
- `takas.toml` is gitignored on purpose: it holds where you live. Never commit it.

You can also pick the start point in the web app (click the map). That choice lives only in the
browser and needs no rebuild, as long as it is inside the graph area.

## 5. Download the map and elevation data

Run:

```sh
uv run takas fetch
```

Expect:
- `data/osm/lithuania-latest.osm.pbf` (about 213 MB) and its `.md5`. The file is checked against the
  md5 and deleted if it does not match.
- `data/dem/N54E025_FABDEM_V1-2.tif` (about 20 MB). It is read straight out of a 1.5 GB zip on the
  University of Bristol server using HTTP range requests, so only the 20 MB tile is downloaded.

If it fails:
- Network error during the OSM download: run the same command again. Files that already exist are
  skipped; `--force` downloads everything again.
- `DEM ... bounds ... do not contain <lat>,<lon>`: your `home` is outside the elevation tile. See
  the rules in step 4. The tile is deleted again, so fix `home` and rerun.
- `server ignored Range request`, `... is compressed (method N); expected stored` or
  `zip64 not supported`: the University of Bristol server or its zip changed, so the tile cannot be
  read out of the zip any more. Use the fallback below and report the error.
- The elevation server does not answer: run `uv run takas fetch --dem copernicus` for a fallback
  tile. This is a **degraded mode**: Copernicus measures treetops, so forests show fake climbs.
  The app shows a banner and turns the hill finder off. Retry FABDEM later.

## 6. Build the routing graph

Run:

```sh
uv run takas build
```

Expect: about 80 s, then a printed JSON summary. Success means it contains:
- `"n_nodes"` around 137 000 and `"n_edges"` around 176 000 (default 15 km radius)
- `"dem_source": "fabdem"` and `"degraded": false`
- `"build_seconds"`.

The result is `data/build/graph.npz` plus `data/build/meta.json`.

If it fails:
- ``run `takas fetch` first`` (the OSM file is missing) or
  ``FileNotFoundError: no DEM in data/dem; run `takas fetch` `` (the elevation tile is missing):
  step 5 did not finish. Run it again.

## 7. Start the app

Run:

```sh
uv run takas serve
```

Expect: uvicorn logs `Uvicorn running on http://127.0.0.1:8742`. Open that address in a browser.
The page shows the map with your start pin and three tabs: Loop, Hills and, only with the Plan day
feature on, Plan day.

Check from another terminal:

```sh
curl -s http://127.0.0.1:8742/api/meta
```

Expect: JSON with `"dem_source": "fabdem"`, `"degraded": false`, `"stale": false` and your `home`.

If it fails:
- "address already in use": something else is using port 8742. Use another port:
  `uv run takas serve --port 8750`. **Do not stop the other process unless you started it.** It may
  be someone's own server.
- The page is blank: open the browser console. The map library loads from unpkg.com and the
  background tiles from openstreetmap.org, so the page needs internet even though routing does not.

## 8. Use it

- **Loop**: distance, surface (road / mixed / trail), terrain (any / flat / hilly), optional gain
  target, prefer lit streets. Tick "May start further away (walk there)" to let a loop begin up to
  N minutes' walk away.
- **Hills**: repeats, minutes up, your uphill pace (min:ss per km), how far the hill may be (the
  warm-up), cool-down minutes. Vert mode looks for ≥ 12 % slopes to power-hike.
- Click a candidate card to centre the map on it. Download its GPX ("run only", or "walk + run").
- Settings such as the start point, uphill pace and warm-up distance are remembered in the browser.

## 9. Optional: Plan day

Plan day turns one day of a training-plan JSON file into a route. It is off by default because
plans are personal. To turn it on, add to `takas.toml`:

```toml
[features]
plan = true

[plan]
path = "~/training/plan.json"
```

Then restart `takas serve`. The file format and the type → route mapping are in
[plan.md](plan.md); the schema is [plan.schema.json](plan.schema.json). takas only reads the file.

## Keeping it up to date

```sh
git pull                                      # new code
uv sync                                       # new dependencies, if any
uv run takas fetch --force && uv run takas build   # fresh OSM data (Geofabrik updates daily);
                                                   # --force also re-reads the 20 MB elevation tile, which never changes
```

Then **restart `takas serve`**. A running server keeps the code and graph it started with. When
newer ones are on disk, the page shows a "Restart takas" banner and `/api/meta` reports
`"stale": true` with the changed files.

## Logs

Every request, its result, the candidate selected in the browser, GPX downloads and browser errors
go to `data/logs/takas.jsonl`. Change the folder with `TAKAS_LOGS`. Read the log with:

```sh
uv run takas logs -n 30          # readable
uv run takas logs -n 30 --json   # raw records
```

The log contains coordinates near your start point. It stays in `data/`, which is gitignored.

## Settings reference

| `takas.toml` key | Environment override | Default | Meaning |
|---|---|---|---|
| `home` | `TAKAS_HOME="lat,lon"` | Cathedral Square | default start point; graph centre |
| `radius_km` | none | 15 | graph covers home ± this (rebuild after changing) |
| `[features] plan` | `TAKAS_FEATURE_PLAN=1` | false | enable Plan day |
| `[plan] path` | `TAKAS_PLAN=/path` | none | training-plan JSON (read-only) |
| `[plan] lap_km` | none | none | lap length for fixed-lap days that do not state one |
| none | `TAKAS_ROOT=/path` | the repo folder | base for the two defaults below |
| none | `TAKAS_CONFIG=/path` | `<repo>/takas.toml` | use another config file |
| none | `TAKAS_DATA=/path` | `<repo>/data` | data folder (osm, dem, build, logs) |
| none | `TAKAS_LOGS=/path` | `<data>/logs` | log folder |

Paths default to the repo folder, not the current directory, so `uv run takas ...` behaves the
same from any directory inside the repo.

## Uninstall

Delete the `takas/` folder. Outside it, only uv keeps things: its download cache and any Python
it installed (`uv cache clean`, `uv python uninstall 3.13`), and the `uv` binary itself if step 2
installed it.

## Notes for AI agents working in this repo

- **Privacy invariants.** `takas.toml`, `data/` and `.claude/*` (except the shared check config
  `.claude/ratchet/config.json`) are gitignored and must stay out of commits: they hold the user's
  home location, logs and training plan. Before any commit or push:
  - check that `git status` shows none of them
  - check that no file contains real coordinates from the user's `takas.toml`.
- **Processes.** Never stop a `takas serve` (or any process) you did not start; the user often runs
  their own on 8742. For checks, start your own on another port (`--port 8749`), remember its PID
  and stop only that.
- **Checks before calling work done.** All must pass:
  - `uv run pytest -q`
  - `uvx ruff check src tests`
  - `uvx typos src tests README.md docs`
  - `uvx semgrep scan --config auto --error --exclude data --exclude .venv src tests` (fetches its
    rules, so it needs network)

  `uvx` runs each tool without installing it; uv is all you need.

  Tests are synthetic (see `tests/synth.py`). Add new scenarios there, never real coordinates.
- **Understanding a user's complaint.** Read the activity log first: `uv run takas logs -n 50`. It
  shows the exact request, the routes returned (thinned coordinates, climb foot and top) and which
  candidate the user selected. Then check `/api/meta` for `"stale": true`: a stale server shows old
  behaviour.
- **Map of the code** (`src/takas/`):

  | File | Contents |
  |---|---|
  | `config.py` | paths, defaults, `takas.toml` loading |
  | `fetch.py` | downloads (OSM + md5, FABDEM range read) |
  | `build.py` | OSM ways → edges; tag rules, street branches and crossings, elevation, green share |
  | `graph.py` | loaded graph, directed arcs, costs |
  | `elevation.py` | DEM sampling, gain with hysteresis, max grade |
  | `loops.py` | loop generator and scoring |
  | `hills.py` | climb finder and hill sessions |
  | `walk.py` | walking to a further loop start |
  | `plan.py` | Plan day mapping |
  | `server.py` | FastAPI app, activity log hooks |
  | `logs.py` | activity log |
  | `gpx.py` | GPX export |
  | `static/` | web UI, design tokens in `design.css` |
