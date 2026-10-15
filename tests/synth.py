"""Synthetic 20x20 grid (100 m edges) written as .osm XML plus a GeoTIFF DEM.

Node (i, j) sits at x=100*i east, y=100*j north of the SW corner; node (10, 10) is HOME.
Planted features:
  * east of x=1200 m the terrain ramps +8 % up to a plateau (hill); west is flat 100 m
  * row j=5 has a bridge way between i=4 and i=5 with a 15 m DEM dip under it
  * row j=15 (i=2..8) is a carriageway with sidewalk:both=separate and a parallel footway
  * row j=2 between x=1500..1900 lies inside a leisure=park polygon
  * row 18 is access=private, row 19 is a motorway (both dropped)
  * a parallel path with a bend between (12,12) and (13,12) duplicates a grid edge
  * an isolated footway far away (dropped as non-largest component)
"""
from __future__ import annotations

import math

import numpy as np

HOME = (54.6859, 25.2877)  # the public default start (Cathedral Square)
DLAT = 1 / 111320.0
DLON = 1 / (111320.0 * math.cos(math.radians(HOME[0])))
BASE = 100.0


def ll(x: float, y: float) -> tuple[float, float]:
    return HOME[0] + (y - 1000) * DLAT, HOME[1] + (x - 1000) * DLON


def elevation_xy(x: float, y: float) -> float:
    z = BASE + np.clip(0.08 * (x - 1200), 0, 56)
    # bridge dip at the middle of the (4,5)-(5,5) edge
    z = z - 15.0 * np.exp(-(((x - 450) / 25.0) ** 2 + ((y - 500) / 40.0) ** 2))
    return z


def write_osm(path) -> dict:
    nodes: dict[int, tuple[float, float]] = {}
    ways: list[tuple[int, list[int], dict]] = []
    ids = {}
    nid = [1]

    def node(i, j):
        k = (i, j)
        if k not in ids:
            ids[k] = nid[0]
            nodes[nid[0]] = ll(100 * i, 100 * j)
            nid[0] += 1
        return ids[k]

    def extra(x, y):
        v = nid[0]
        nid[0] += 1
        nodes[v] = ll(x, y)
        return v

    wid = [1000]

    def way(nds, **tags):
        wid[0] += 1
        ways.append((wid[0], nds, tags))
        return wid[0]

    for j in range(20):
        row = [node(i, j) for i in range(20)]
        if j == 5:
            way(row[:5], highway="residential")
            way(row[4:6], highway="residential", bridge="yes")
            way(row[5:], highway="residential")
        elif j == 15:
            way(row[:3], highway="residential")
            way(row[2:9], highway="residential", **{"sidewalk:both": "separate"})
            way(row[8:], highway="residential")
        elif j == 18:
            way(row, highway="residential", access="private")
        elif j == 19:
            way(row, highway="motorway")
        elif j == 8:
            way(row, highway="residential", oneway="yes")
        elif j == 2:
            way(row, highway="footway")
        else:
            way(row, highway="residential")
    for i in range(20):
        way([node(i, j) for j in range(20)], highway="residential")

    # separate sidewalk footway north of the carriageway, 5 m offset
    sw = [node(2, 15), extra(300, 1505), extra(500, 1505), extra(700, 1505), node(8, 15)]
    way(sw, highway="footway", footway="sidewalk")
    # duplicate path between (12,12) and (13,12), bending 30 m north
    dup = [node(12, 12), extra(1250, 1230), node(13, 12)]
    way(dup, highway="path", surface="dirt")
    # isolated footway island (not connected)
    iso = [extra(-500, 500), extra(-450, 500)]
    way(iso, highway="footway")
    # park polygon
    corners = [(1500, -50), (1900, -50), (1900, 450), (1500, 450)]
    park_nodes = [extra(*c) for c in corners]
    way(park_nodes + [park_nodes[0]], leisure="park")

    with open(path, "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="synth">\n')
        f.writelines(f'<node id="{k}" version="1" lat="{la:.7f}" lon="{lo:.7f}"/>\n' for k, (la, lo) in nodes.items())
        for w, nds, tags in ways:
            f.write(f'<way id="{w}" version="1">')
            f.writelines(f'<nd ref="{n}"/>' for n in nds)
            f.writelines(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
            f.write("</way>\n")
        f.write("</osm>\n")
    return {"ids": ids}


def write_dem(path, half=1500.0, res=10.0) -> None:
    import rasterio
    from rasterio.transform import from_origin

    xs = np.arange(-half + 1000, half + 1000 + res, res)
    ys = np.arange(half + 1000, -half + 1000 - res, -res)
    X, Y = np.meshgrid(xs, ys)
    Z = elevation_xy(X, Y).astype("float32")
    lat_top, lon_left = ll(xs[0], ys[0])
    tr = from_origin(lon_left - DLON * res / 2, lat_top + DLAT * res / 2, DLON * res, DLAT * res)
    with rasterio.open(
        path, "w", driver="GTiff", height=Z.shape[0], width=Z.shape[1], count=1,
        dtype="float32", crs="EPSG:4326", transform=tr,
    ) as ds:
        ds.write(Z, 1)


def bbox():
    s, w = ll(-1400, -1400)
    n, e = ll(3400, 3400)
    return (s, w, n, e)
