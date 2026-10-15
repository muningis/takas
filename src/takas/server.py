from __future__ import annotations

from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, gpx, hills, loops, plan, walk
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


class HillsReq(BaseModel):
    reps: int = Field(6, ge=1, le=30)
    minutes: float = Field(2, gt=0.2, le=10)
    min_grade: float | None = Field(None, gt=0, lt=0.4)
    steep: bool = False
    uphill_pace: float | None = Field(None, gt=150, lt=1200)  # s/km for the hard climb
    warm_min: float = Field(15, ge=0, le=60)
    cool_min: float = Field(10, ge=0, le=60)
    start: Point | None = None
    walk_max_min: float = Field(0, ge=0, le=60)


def create_app(graph: Graph | None = None, plan_path: str | Path | None = None,
               plan_enabled: bool | None = None) -> FastAPI:
    plan_path = plan_path or config.PLAN_PATH
    plan_enabled = config.FEATURE_PLAN if plan_enabled is None else plan_enabled
    cache: OrderedDict[str, tuple[str, dict]] = OrderedDict()
    lock = Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.graph = graph or Graph.load()
        yield

    app = FastAPI(title="takas", lifespan=lifespan)
    if graph is not None:
        app.state.graph = graph

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
        return m

    @app.post("/api/loop")
    def loop(req: LoopReq):
        res = loops.generate_loops(
            G(), km=req.km, profile=req.profile, terrain=req.terrain, gain_target=req.gain_target,
            lit=req.lit, n=req.n, start=start_of(req.start), tol=req.tol, walk_max_m=walk.max_m(req.walk_max_min),
        )
        return publish(res, f"{req.km:g} km {req.profile} {req.terrain}")

    @app.post("/api/hills")
    def hills_ep(req: HillsReq):
        try:
            res = hills.hills_session(
                G(), reps=req.reps, minutes=req.minutes, kind="vert" if req.steep else "hills",
                min_grade=req.min_grade or config.MIN_GRADE["vert" if req.steep else "hills"],
                start=start_of(req.start), warm_min=req.warm_min, cool_min=req.cool_min,
                walk_max_m=walk.max_m(req.walk_max_min), uphill_pace=req.uphill_pace,
            )
        except hills.DegradedDem as e:
            raise HTTPException(409, str(e))
        return publish(res, f"hills {req.reps}x{req.minutes:g}min")

    @app.get("/api/day/{date}")
    def day(date: str, lat: float | None = Query(None), lon: float | None = Query(None),
            walk_max_min: float = Query(0, ge=0, le=60), loops_walk: bool = False,
            uphill_pace: float | None = Query(None, gt=150, lt=1200)):
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
                                   uphill_pace=uphill_pace)
        except hills.DegradedDem as e:
            raise HTTPException(409, str(e))
        out = publish(res, f"{date} {d['session']}")
        return {**out, "day": d, "plan_request": req}

    @app.get("/api/gpx/{cid}")
    def gpx_ep(cid: str, walk: bool = False):
        with lock:
            hit = cache.get(cid)
        if hit is None:
            raise HTTPException(404, "route expired from cache; regenerate it")
        label, c = hit
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
