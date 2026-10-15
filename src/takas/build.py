from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from . import config, elevation

CLASSES = ["foot", "res", "tert", "sec", "path", "steps"]
HIGHWAY_CLASS = {
    "footway": "foot", "pedestrian": "foot", "cycleway": "foot",
    "residential": "res", "living_street": "res", "service": "res",
    "unclassified": "tert", "tertiary": "tert", "tertiary_link": "tert",
    "secondary": "sec", "secondary_link": "sec", "primary": "sec", "primary_link": "sec",
    "path": "path", "track": "path", "bridleway": "path",
    "steps": "steps",
}
CARRIAGEWAY = {"res", "tert", "sec"}
# streets a runner has to cross; driveways and parking aisles (service) don't count
ROAD_HW = {"residential", "living_street", "unclassified", "tertiary", "tertiary_link", "secondary",
           "secondary_link", "primary", "primary_link", "trunk", "trunk_link", "motorway", "motorway_link"}
PAVED = {"asphalt", "paved", "concrete", "paving_stones", "sett", "concrete:plates", "concrete:lanes",
         "cobblestone", "metal", "wood", "bricks", "chipseal"}
UNPAVED = {"unpaved", "gravel", "fine_gravel", "dirt", "ground", "earth", "grass", "sand", "mud",
           "compacted", "pebblestone", "woodchips", "salt", "snow", "ice", "clay"}
GREEN_TAGS = [("leisure", "park"), ("leisure", "nature_reserve"), ("landuse", "forest"),
              ("landuse", "meadow"), ("natural", "wood")]
M_LAT = 111320.0


def way_rules(tags) -> dict | None:
    """Tag rules from the plan; returns edge attributes or None if not walkable."""
    hw = tags.get("highway")
    cls = HIGHWAY_CLASS.get(hw)
    if cls is None:
        return None
    foot = tags.get("foot")
    if foot == "no":
        return None
    if tags.get("access") in ("private", "no") and foot not in ("yes", "designated", "permissive"):
        return None
    if cls in CARRIAGEWAY:
        if tags.get("sidewalk") == "separate" or tags.get("sidewalk:both") == "separate":
            return None
        if tags.get("sidewalk:left") == "separate" and tags.get("sidewalk:right") == "separate":
            return None
    surface = tags.get("surface")
    if surface in PAVED:
        paved = True
    elif surface in UNPAVED:
        paved = False
    else:
        paved = cls != "path"  # paths/tracks/bridleways default to unpaved
    of = tags.get("oneway:foot")
    if of in ("yes", "true", "1"):
        fwd, rev = True, False
    elif of in ("-1", "reverse"):
        fwd, rev = False, True
    else:
        fwd = rev = True
    return {
        "cls": cls,
        "paved": paved,
        "lit": tags.get("lit") in ("yes", "24/7", "automatic", "sunset-sunrise"),
        "bridge": tags.get("bridge") not in (None, "no"),
        "tunnel": tags.get("tunnel") not in (None, "no"),
        "onroad": hw in ROAD_HW,
        "crossing": "crossing" in (tags.get("footway"), tags.get("cycleway"), tags.get("path"))
                    or tags.get("crossing") not in (None, "no"),
        "fwd": fwd,
        "rev": rev,
    }


def _read_ways(pbf, bbox):
    import osmium

    s, w, n, e = bbox
    fp = (
        osmium.FileProcessor(str(pbf))
        .with_locations()
        .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
        .with_filter(osmium.filter.KeyFilter("highway"))
    )
    ways = []
    # node ref -> street branches leaving it (walkable or not): 2 where a street runs through, 1 where one
    # ends, so a street continuing as a second way gives 2, a T-junction 3, a crossroads 4
    branches: dict[int, int] = {}
    for way in fp:
        if way.tags.get("highway") in ROAD_HW:
            refs = [nd.ref for nd in way.nodes]
            for i, ref in enumerate(refs):
                branches[ref] = branches.get(ref, 0) + (1 if i in (0, len(refs) - 1) else 2)
        attrs = way_rules(way.tags)
        if attrs is None:
            continue
        if attrs["onroad"]:
            attrs["street"] = way.tags.get("name") or f"way/{way.id}"
        run = []
        for nd in way.nodes:
            if not nd.location.valid():
                continue
            la, lo = nd.location.lat, nd.location.lon
            if s <= la <= n and w <= lo <= e:
                run.append((nd.ref, la, lo))
            else:
                if len(run) > 1:
                    ways.append((run, attrs))
                run = []
        if len(run) > 1:
            ways.append((run, attrs))
    return ways, branches


def _split_edges(ways, branches=None):
    """Cut way runs at junctions into edges; guarantee at most one edge per node pair. An edge is a
    street crossing when its way is tagged as one, when a path touches a street part-way along, or
    when a street passes a crossroads part-way along (e.g. with a carriageway dropped for having
    separate sidewalks)."""
    branches = branches or {}
    count: dict[int, int] = {}
    for run, _ in ways:
        for ref, _, _ in run:
            count[ref] = count.get(ref, 0) + 1
    edges = []  # (u, v, lat[], lon[], attrs)
    seen: set[tuple[int, int]] = set()
    synth = [-1]

    def add(u, v, la, lo, attrs, depth=0):
        if len(la) < 2:
            return
        key = (u, v) if u < v else (v, u)
        if u != v and key not in seen:
            seen.add(key)
            edges.append((u, v, la, lo, attrs))
            return
        # self-loop or parallel edge: break it with a midpoint node
        need = 4 if u == v else 3
        if len(la) < need or depth > 4:
            return
        m = len(la) // 2
        mid = synth[0]
        synth[0] -= 1
        add(u, mid, la[: m + 1], lo[: m + 1], attrs, depth + 1)
        add(mid, v, la[m:], lo[m:], attrs, depth + 1)

    for run, attrs in ways:
        start = 0
        for i in range(1, len(run)):
            if count[run[i][0]] >= 2 or i == len(run) - 1:
                seg = run[start : i + 1]
                limit = 4 if attrs["onroad"] else 1
                crosses = attrs["crossing"] or any(branches.get(p[0], 0) >= limit for p in seg[1:-1])
                add(seg[0][0], seg[-1][0], [p[1] for p in seg], [p[2] for p in seg], {**attrs, "crosses": crosses})
                start = i
    return edges


def _green_polygons(pbf, bbox):
    import osmium
    import shapely

    s, w, n, e = bbox
    clip = shapely.box(w, s, e, n)
    fac = osmium.geom.WKBFactory()
    fp = osmium.FileProcessor(str(pbf)).with_areas(osmium.filter.TagFilter(*GREEN_TAGS))
    polys = []
    for o in fp:
        if not o.is_area():
            continue
        t = o.tags
        if not any(t.get(k) == v for k, v in GREEN_TAGS):
            continue
        try:
            g = shapely.from_wkb(fac.create_multipolygon(o))
        except (RuntimeError, ValueError, shapely.errors.ShapelyError):
            g = None  # degenerate OSM multipolygon: skip it
        if g is None or g.is_empty or not g.intersects(clip):
            continue
        polys.append(shapely.make_valid(g) if not g.is_valid else g)
    return polys


def _green_fractions(edges, polys, lat0):
    import shapely
    from shapely import STRtree

    out = np.zeros(len(edges), dtype="float32")
    if not polys or not edges:
        return out
    k = np.cos(np.radians(lat0))

    def proj(g):
        return shapely.transform(g, lambda c: np.column_stack([c[:, 0] * M_LAT * k, c[:, 1] * M_LAT]))

    pl = [proj(p) for p in polys]
    lines = [shapely.linestrings(np.column_stack([np.asarray(e[3]) * M_LAT * k, np.asarray(e[2]) * M_LAT]))
             for e in edges]
    tree = STRtree(pl)
    li, pi = tree.query(lines, predicate="intersects")
    order = np.argsort(li, kind="stable")
    li, pi = li[order], pi[order]
    bounds = np.searchsorted(li, np.arange(len(lines) + 1))
    for e in np.unique(li):
        sel = pi[bounds[e] : bounds[e + 1]]
        u = shapely.union_all([pl[i] for i in sel])
        ln = lines[e].length
        if ln > 0:
            out[e] = min(1.0, lines[e].intersection(u).length / ln)
    return out


def build_graph(osm_path, dem_path, out_dir, bbox=config.BBOX, dem_source="fabdem", pbf_timestamp=None):
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components

    t0 = time.time()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ways, branches = _read_ways(osm_path, bbox)
    edges = _split_edges(ways, branches)

    # compact node ids, coordinates from edge endpoints
    ids: dict[int, int] = {}
    ncoord: list[tuple[float, float]] = []
    eu = np.empty(len(edges), dtype="int64")
    ev = np.empty(len(edges), dtype="int64")
    for k, (u, v, la, lo, _) in enumerate(edges):
        for ref, idx, arr in ((u, 0, eu), (v, -1, ev)):
            if ref not in ids:
                ids[ref] = len(ncoord)
                ncoord.append((la[idx], lo[idx]))
            arr[k] = ids[ref]
    n_all = len(ncoord)
    # SCC with oneway:foot arcs honoured
    fw = np.array([e[4]["fwd"] for e in edges])
    rv = np.array([e[4]["rev"] for e in edges])
    rows = np.concatenate([eu[fw], ev[rv]])
    cols = np.concatenate([ev[fw], eu[rv]])
    g0 = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n_all, n_all))
    _, lab = connected_components(g0, directed=True, connection="strong")
    big = np.bincount(lab).argmax()
    keep_node = lab == big
    keep_edge = keep_node[eu] & keep_node[ev]
    remap = -np.ones(n_all, dtype="int64")
    remap[keep_node] = np.arange(keep_node.sum())
    edges = [e for e, k in zip(edges, keep_edge) if k]
    eu, ev = remap[eu[keep_edge]], remap[ev[keep_edge]]
    nodes = np.array(ncoord)[keep_node]
    ref_of = {v: k for k, v in ids.items()}
    node_refs = np.array([ref_of[i] for i in np.nonzero(keep_node)[0]], dtype="int64")

    polys = _green_polygons(osm_path, bbox)
    green = _green_fractions(edges, polys, nodes[:, 0].mean())

    dem = elevation.Dem.open(dem_path)
    E = len(edges)
    glat, glon, gz, gs = [], [], [], []
    off = np.zeros(E + 1, dtype="int64")
    length = np.zeros(E, dtype="float32")
    climb_f = np.zeros(E, dtype="float32")
    desc_f = np.zeros(E, dtype="float32")
    maxup_f = np.zeros(E, dtype="float32")
    maxdown_f = np.zeros(E, dtype="float32")
    for k, (u, v, la, lo, a) in enumerate(edges):
        dl, dlo, z = elevation.edge_profile(dem, la, lo, interpolate=a["bridge"] or a["tunnel"])
        _, _, s = elevation.densify(la, lo)
        glat.append(dl)
        glon.append(dlo)
        gz.append(z)
        gs.append(s)
        off[k + 1] = off[k] + len(dl)
        length[k] = s[-1]
        dz = np.diff(z)
        ds = np.maximum(np.diff(s), 1e-3)
        climb_f[k] = dz[dz > 0].sum()
        desc_f[k] = -dz[dz < 0].sum()
        gr = dz / ds
        maxup_f[k] = max(0.0, gr.max()) if len(gr) else 0.0
        maxdown_f[k] = max(0.0, -gr.min()) if len(gr) else 0.0
    nz = dem.sample(nodes[:, 0], nodes[:, 1])
    cls = np.array([CLASSES.index(e[4]["cls"]) for e in edges], dtype="int8")
    street_ids: dict[str, int] = {}
    flag = lambda key: np.array([e[4][key] for e in edges], dtype=bool)

    np.savez(
        out_dir / "graph.npz",
        node_lat=nodes[:, 0], node_lon=nodes[:, 1], node_elev=nz.astype("float32"), node_ref=node_refs,
        eu=eu.astype("int32"), ev=ev.astype("int32"), edge_len=length, edge_cls=cls,
        edge_paved=flag("paved"), edge_lit=flag("lit"), edge_bridge=flag("bridge"),
        edge_tunnel=flag("tunnel"), edge_fwd=fw[keep_edge], edge_rev=rv[keep_edge],
        edge_green=green, edge_crossing=flag("crosses"), edge_onroad=flag("onroad"),
        node_street_branches=np.array([min(branches.get(int(r), 0), 9) for r in node_refs], dtype="int8"),
        edge_street=np.array([street_ids.setdefault(e[4]["street"], len(street_ids)) if e[4]["onroad"] else -1
                              for e in edges], dtype="int32"),
        climb_f=climb_f, desc_f=desc_f, maxup_f=maxup_f, maxdown_f=maxdown_f,
        geom_off=off, geom_lat=np.concatenate(glat), geom_lon=np.concatenate(glon),
        geom_z=np.concatenate(gz).astype("float32"), geom_s=np.concatenate(gs).astype("float32"),
    )
    meta = {
        "n_nodes": len(nodes), "n_edges": int(E), "bbox": list(bbox), "home": list(config.HOME),
        "dem_source": dem_source, "degraded": dem_source != "fabdem", "pbf_timestamp": pbf_timestamp,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "build_seconds": round(time.time() - t0, 1), "n_green_polygons": len(polys),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def pbf_timestamp(pbf) -> str | None:
    import osmium

    h = osmium.io.Reader(str(pbf)).header()
    return h.get("osmosis_replication_timestamp") or h.get("timestamp") or None


def build_all():
    from . import fetch

    pbf = config.OSM_DIR / "lithuania-latest.osm.pbf"
    if not pbf.exists():
        raise SystemExit("run `takas fetch` first")
    dem, src = fetch.find_dem()
    meta = build_graph(pbf, dem, config.BUILD_DIR, dem_source=src, pbf_timestamp=pbf_timestamp(pbf))
    print(json.dumps(meta, indent=2))
    return meta
