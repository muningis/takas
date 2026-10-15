from __future__ import annotations

import json
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, gpx, hills, logs, loops, plan, walk
from .graph import Graph

STATIC = Path(__file__).parent / "static"
CACHE_SIZE = 64


class Point(BaseModel):
    lat: float
    lon: float


class LoopReq(BaseModel):
    km: float = Field(10, gt=0.5, le=100)
    profile: str = Field("mixed", pattern="^(road|mixed|trail)$")
    terrain: str = Field("any", pattern="^(any|flat|hilly)$")
    gain_target: float | None = Field(None, ge=0)
    lit: bool = False
    n: int = Field(4, ge=1, le=8)
    start: Point | None = None
    tol: float | None = Field(None, gt=0, lt=0.2)
    walk_max_min: float = Field(0, ge=0, le=60)


class ClientEvent(BaseModel):
    event: Literal["select", "gpx", "error", "view"]
    data: dict = {}


class HillsReq(BaseModel):
    reps: int = Field(6, ge=1, le=30)
    minutes: float = Field(2, gt=0.2, le=10)
    min_grade: float | None = Field(None, gt=0, lt=0.4)
    steep: bool = False
    uphill_pace: float | None = Field(None, gt=150, lt=1200)  # s/km for the hard climb
    warmup_km: float = Field(3, gt=0, le=15)  # how far the hill may be: the warm-up is getting there
    cool_min: float = Field(10, ge=0, le=60)
    start: Point | None = None


def _watched() -> list[Path]:
    here = Path(__file__).parent
    return [*here.glob("*.py"), *(here / "static").glob("*.*"), config.BUILD_DIR / "graph.npz"]


def create_app(graph: Graph | None = None, plan_path: str | Path | None = None,
               plan_enabled: bool | None = None, log_dir: str | Path | None = None,
               watch: list[Path] | None = None) -> FastAPI:
    plan_path = plan_path or config.PLAN_PATH
    plan_enabled = config.FEATURE_PLAN if plan_enabled is None else plan_enabled
    cache: OrderedDict[str, tuple[str, dict]] = OrderedDict()
    lock = Lock()
    log = logs.ActivityLog(log_dir or config.LOG_DIR)
    started = time.time()
    watch = _watched() if watch is None else watch

    def stale_files() -> list[str]:
        # a running server keeps the code and graph it started with; say so when either changed on disk
        return sorted(p.name for p in watch if p.exists() and p.stat().st_mtime > started)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.graph = graph or Graph.load()
        m = app.state.graph.meta
        log.event("start", graph_built_at=m.get("built_at"), pbf_timestamp=m.get("pbf_timestamp"),
                  n_edges=m.get("n_edges"), dem=m.get("dem_source"), home=list(config.HOME),
                  plan_feature=bool(plan_enabled and plan_path))
        yield

    app = FastAPI(title="takas", lifespan=lifespan)
    app.state.log = log
    if graph is not None:
        app.state.graph = graph

    def logged(kind: str, params: dict, run):
        t = time.perf_counter()
        try:
            res = run()
        except HTTPException as e:
            log.event("error", request=kind, params=params, status=e.status_code, detail=e.detail,
                      ms=round((time.perf_counter() - t) * 1000))
            raise
        log.event(kind, params=params, ms=round((time.perf_counter() - t) * 1000), result=logs.summarize(res))
        return res

    def G() -> Graph:
        return app.state.graph

    def start_of(p: Point | None):
        return (p.lat, p.lon) if p else config.HOME

    def publish(res: dict, label: str) -> dict:
        """Strip internals, remember candidates for the GPX endpoint."""
        res = dict(res)
        cands = []
        fb = res.get("fallback")
        items = list(res.get("candidates", []))
        if fb:
            items.append(fb["candidate"])
        for c in items:
            with lock:
                cache[c["id"]] = (f"takas {label}", c)
                while len(cache) > CACHE_SIZE:
                    cache.popitem(last=False)
        for c in res.get("candidates", []):
            cands.append({k: v for k, v in c.items() if not k.startswith("_")})
        res["candidates"] = cands
        if fb:
            res["fallback"] = {**fb, "candidate": {k: v for k, v in fb["candidate"].items() if not k.startswith("_")}}
        return res

    @app.get("/api/meta")
    def meta():
        m = dict(G().meta)
        m["features"] = {"plan": bool(plan_enabled and plan_path)}
        m["home"] = list(config.HOME)
        m["server_started_at"] = datetime.fromtimestamp(started).isoformat(timespec="seconds")
        m["stale_files"] = stale_files()
        m["stale"] = bool(m["stale_files"])
        return m

    @app.post("/api/log", status_code=204)
    def client_event(ev: ClientEvent):
        if len(json.dumps(ev.data)) > 4000:
            raise HTTPException(413, "event too large")
        log.event(ev.event, source="ui", data=ev.data)
        return Response(status_code=204)

    @app.post("/api/loop")
    def loop(req: LoopReq):
        def run():
            res = loops.generate_loops(
                G(), km=req.km, profile=req.profile, terrain=req.terrain, gain_target=req.gain_target,
                lit=req.lit, n=req.n, start=start_of(req.start), tol=req.tol,
                walk_max_m=walk.max_m(req.walk_max_min),
            )
            return publish(res, f"{req.km:g} km {req.profile} {req.terrain}")
        return logged("loop", req.model_dump(), run)

    @app.post("/api/hills")
    def hills_ep(req: HillsReq):
        def run():
            try:
                res = hills.hills_session(
                    G(), reps=req.reps, minutes=req.minutes, kind="vert" if req.steep else "hills",
                    min_grade=req.min_grade or config.MIN_GRADE["vert" if req.steep else "hills"],
                    start=start_of(req.start), warmup_max_m=req.warmup_km * 1000, cool_min=req.cool_min,
                    uphill_pace=req.uphill_pace,
                )
            except hills.DegradedDem as e:
                raise HTTPException(409, str(e)) from None
            return publish(res, f"hills {req.reps}x{req.minutes:g}min")
        return logged("hills", req.model_dump(), run)

    @app.get("/api/day/{date}")
    def day(date: str, lat: float | None = Query(None), lon: float | None = Query(None),
            walk_max_min: float = Query(0, ge=0, le=60), loops_walk: bool = False,
            uphill_pace: float | None = Query(None, gt=150, lt=1200),
            warmup_km: float | None = Query(None, gt=0, le=15)):
        params = {"date": date, "lat": lat, "lon": lon, "walk_max_min": walk_max_min, "loops_walk": loops_walk,
                  "uphill_pace": uphill_pace, "warmup_km": warmup_km}
        return logged("day", params, lambda: _day(date, lat, lon, walk_max_min, loops_walk, uphill_pace, warmup_km))

    def _day(date, lat, lon, walk_max_min, loops_walk, uphill_pace, warmup_km):
        if not plan_enabled:
            raise HTTPException(404, "plan day feature is disabled (set [features] plan = true in takas.toml)")
        try:
            days = plan.load_plan(plan_path)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from None
        d = plan.find_day(days, date)
        if d is None:
            raise HTTPException(404, f"no plan entry for {date}")
        req = plan.day_request(d)
        start = (lat, lon) if lat is not None and lon is not None else config.HOME
        try:
            res = plan.run_request(G(), req, start, walk_max_m=walk.max_m(walk_max_min), loops_walk=loops_walk,
                                   uphill_pace=uphill_pace, warmup_max_m=warmup_km * 1000 if warmup_km else None)
        except hills.DegradedDem as e:
            raise HTTPException(409, str(e)) from None
        out = publish(res, f"{date} {d['session']}")
        return {**out, "day": d, "plan_request": req}

    @app.get("/api/gpx/{cid}")
    def gpx_ep(cid: str, walk: bool = False):
        with lock:
            hit = cache.get(cid)
        if hit is None:
            log.event("error", request="gpx", params={"id": cid, "walk": walk}, status=404, detail="expired")
            raise HTTPException(404, "route expired from cache; regenerate it")
        label, c = hit
        log.event("gpx", params={"id": cid, "walk": walk}, km=c["km"])
        body = gpx.write_gpx(f"{label} {c['km']:.1f} km", c["coords"], c["elev"],
                             desc=f"+{c['gain']} m / -{c['loss']} m", walk=c.get("walk") if walk else None)
        return Response(body, media_type="application/gpx+xml",
                        headers={"Content-Disposition": f'attachment; filename="takas-{cid}.gpx"'})

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _lazy_app() -> FastAPI:
    return create_app()


app = _lazy_app()
