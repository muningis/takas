from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="takas")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download OSM extract and DEM tile")
    f.add_argument("--dem", choices=["fabdem", "copernicus"], default="fabdem")
    f.add_argument("--force", action="store_true")
    sub.add_parser("build", help="build routing graph from downloaded data")
    s = sub.add_parser("serve", help="run the web app on 127.0.0.1:8742")
    s.add_argument("--port", type=int, default=None)
    lp = sub.add_parser("loop", help="quick CLI loop check")
    lp.add_argument("--km", type=float, default=10)
    lp.add_argument("--profile", default="mixed")
    lp.add_argument("--terrain", default="any")
    lp.add_argument("--gain", type=float, default=None)
    a = p.parse_args(argv)

    if a.cmd == "fetch":
        from . import fetch

        fetch.fetch_osm(a.force)
        fetch.fetch_dem(a.dem, a.force)
    elif a.cmd == "build":
        from . import build

        build.build_all()
    elif a.cmd == "serve":
        import uvicorn

        from . import config

        uvicorn.run("takas.server:app", host=config.HOST, port=a.port or config.PORT)
    elif a.cmd == "loop":
        from . import graph, loops

        g = graph.Graph.load()
        res = loops.generate_loops(g, km=a.km, profile=a.profile, terrain=a.terrain, gain_target=a.gain)
        for c in res["candidates"]:
            print(f"{c['km']:.2f} km  +{c['gain']:.0f}/-{c['loss']:.0f} m  score {c['score']:.2f}")
