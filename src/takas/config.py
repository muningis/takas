from __future__ import annotations

import math
import os
import tomllib
from pathlib import Path

ROOT = Path(os.environ.get("TAKAS_ROOT", Path(__file__).resolve().parents[2]))
# Personal values (your start point, a private training plan) live in takas.toml, which is gitignored.
CONFIG_PATH = Path(os.environ.get("TAKAS_CONFIG", ROOT / "takas.toml"))
_cfg = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.is_file() else {}


def _home() -> tuple[float, float]:
    if env := os.environ.get("TAKAS_HOME"):
        lat, lon = (float(v) for v in env.split(","))
        return lat, lon
    lat, lon = _cfg.get("home", (54.6859, 25.2877))  # Cathedral Square, Vilnius
    return float(lat), float(lon)


HOME = _home()  # lat, lon
RADIUS_KM = float(_cfg.get("radius_km", 15))
_dlat = RADIUS_KM * 1000 / 111320.0
_dlon = _dlat / math.cos(math.radians(HOME[0]))
BBOX = (HOME[0] - _dlat, HOME[1] - _dlon, HOME[0] + _dlat, HOME[1] + _dlon)  # south, west, north, east
DATA = Path(os.environ.get("TAKAS_DATA", ROOT / "data"))
OSM_DIR = DATA / "osm"
DEM_DIR = DATA / "dem"
BUILD_DIR = DATA / "build"
LOG_DIR = Path(os.environ.get("TAKAS_LOGS", DATA / "logs"))
PBF_URL = "https://download.geofabrik.de/europe/lithuania-latest.osm.pbf"
FABDEM_ZIP_URL = "https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn/N50E020-N60E030_FABDEM_V1-2.zip"
FABDEM_MEMBER = "N54E025_FABDEM_V1-2.tif"
COPERNICUS_URL = (
    "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N54_00_E025_00_DEM/"
    "Copernicus_DSM_COG_10_N54_00_E025_00_DEM.tif"
)
_plan = _cfg.get("plan", {})
FEATURE_PLAN = os.environ.get("TAKAS_FEATURE_PLAN", "") in ("1", "true") or bool(_cfg.get("features", {}).get("plan"))
_plan_path = os.environ.get("TAKAS_PLAN") or _plan.get("path")
PLAN_PATH = Path(_plan_path).expanduser() if _plan_path else None
LAP_KM = float(_plan["lap_km"]) if "lap_km" in _plan else None  # fallback for fixed-lap days
HOST, PORT = "127.0.0.1", 8742

EASY_PACE = 6.75 * 60  # s/km
HARD_FLAT_PACE = 4 * 60 + 50  # s/km, roughly 5k race effort
UPHILL_SLOWDOWN = {"hills": 4.0, "vert": 6.0}  # pace multiplier per unit grade: 1 + k*grade
MIN_GRADE = {"hills": 0.08, "vert": 0.12}
STEEP_IDEAL = {"hills": 0.10, "vert": 0.18}  # runnable steep vs power-hike steep
MAX_GRADE = {"hills": 0.16, "vert": 0.40}  # average grade above this is not a running repeat
WALK_PACE = 720  # s/km, 5 km/h
