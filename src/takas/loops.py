from __future__ import annotations

import itertools
import math
import uuid

import numpy as np
from scipy.sparse.csgraph import dijkstra

from . import config, walk
from .graph import Graph

REUSE_PENALTY = 4.0
WALK_BEARINGS = 6  # per walk-away start, to keep the walk-on request fast
PENALTY_PER_KM = walk.PENALTY_PER_KM
DETOUR = 1.3  # street network vs straight-line perimeter


def est_time(km: float, gain: float, pace: float | None = None) -> float:
    """Seconds: flat pace per km plus a climbing allowance."""
    return km * (pace or config.EASY_PACE) + 4.0 * gain


def shortest_path(g: Graph, cost: np.ndarray, src: int, dst: int, straight: float | None = None):
    m = g.csr(cost)
    limit = np.inf if straight is None else 4.0 * straight + 2000.0
    for lim in (limit, np.inf):
        d, p = dijkstra(m, directed=True, indices=src, return_predecessors=True, limit=lim)
        if np.isfinite(d[dst]):
            nodes = [dst]
            while nodes[-1] != src:
                nodes.append(int(p[nodes[-1]]))
            nodes.reverse()
            return g.arcs_from_nodes(nodes) if len(nodes) > 1 else []
        if lim == np.inf:
            break
    return None


def route_via(g: Graph, base_cost: np.ndarray, seq: list[int], penalty: float = REUSE_PENALTY):
    """Chain legs through seq; arcs (both directions) used by earlier legs are penalised."""
    cost = base_cost.copy()
    out: list[int] = []
    for a, b in itertools.pairwise(seq):
        if a == b:
            continue
        straight = float(np.hypot(*(g.xy[a] - g.xy[b])))
        arcs = shortest_path(g, cost, a, b, straight)
        if arcs is None:
            return None
        arr = np.array(arcs, dtype=int)
        cost[arr] *= penalty
        rv = g.arc_rev[arr]
        cost[rv[rv >= 0]] *= penalty
        out.extend(arcs)
    return out


def _waypoint_offsets(shape: str, bearing: float, perimeter: float):
    """Offsets (dx, dy) in metres of the waypoints on a polygon through the start."""
    def pt(b, d):
        a = math.radians(b)
        return (d * math.sin(a), d * math.cos(a))

    if shape == "tri":
        r = perimeter / 3
        return [pt(bearing - 30, r), pt(bearing + 30, r)]
    s = perimeter / 4
    return [pt(bearing - 45, s), pt(bearing, s * math.sqrt(2)), pt(bearing + 45, s)]


def _snap_wp(g: Graph, x: float, y: float, radius: float, hilly: bool) -> int:
    if hilly and radius > 0:
        idx = g.tree.query_ball_point([x, y], radius)
        if idx:
            idx = np.asarray(idx)
            return int(idx[np.argmax(g.node_elev[idx])])
    return int(g.tree.query([x, y])[1])


def _fit(g, cost, s, shape, bearing, target_m, tol, hilly, f0):
    f, tried, best = f0, [], None
    for _ in range(8 if tol < 0.02 else 6):
        per = target_m / DETOUR * f
        sx, sy = g.xy[s]
        wps = [
            _snap_wp(g, sx + dx, sy + dy, per * 0.04 if hilly else 0, hilly)
            for dx, dy in _waypoint_offsets(shape, bearing, per)
        ]
        arcs = route_via(g, cost, [s, *wps, s])
        if not arcs:
            return best, f
        L = float(g.arc_len[arcs].sum())
        err = abs(L - target_m) / target_m
        if best is None or err < best[2]:
            best = (arcs, L, err, f)
        if err <= tol:
            break
        tried.append((f, L))
        if len(tried) >= 2 and tried[-1][1] != tried[-2][1]:
            (fa, la), (fb, lb) = tried[-2], tried[-1]
            nf = fb + (target_m - lb) * (fb - fa) / (lb - la)
        else:
            nf = f * target_m / L
        if abs(nf - f) < 1e-3 * f:
            nf = f * (1 + 0.02 * np.sign(target_m - L))
        f = float(np.clip(nf, f * 0.5, f * 2.0))
    return best, f


def _score(c, a):
    tol, profile, terrain, gain_target, lit = a
    s = c["_err"] / tol
    s += 2.0 * c["retrace_pct"] / 10.0
    paved, green = c["paved_pct"] / 100, c["green_pct"] / 100
    if profile == "road":
        s += 1.5 * (1 - paved)
    elif profile == "trail":
        s += 1.5 * (1 - min(1.0, 0.6 * (1 - paved) + 0.4 * green * 2))
    else:
        s += 0.5 * (1 - green)
    gpk = c["gain"] / max(c["km"], 0.1)
    if terrain == "flat":
        s += 2.0 * gpk / 15.0
    elif terrain == "hilly":
        if gain_target:
            s += 3.0 * abs(c["gain"] - gain_target) / gain_target
        else:
            s -= 2.0 * gpk / 40.0
    if lit:
        s += 1.0 - c["lit_pct"] / 100
    return float(s)


def payload(g: Graph, arcs: list[int], pace: float | None = None) -> dict:
    st = g.route_stats(arcs)
    la, lo, z, s = g.route_profile(arcs)
    return {
        "id": uuid.uuid4().hex[:10],
        **{k: round(v, 3) for k, v in st.items()},
        "gain": round(st["gain"]),
        "loss": round(st["loss"]),
        "time_s": round(est_time(st["km"], st["gain"], pace)),
        "coords": [[round(float(a), 6), round(float(b), 6)] for a, b in zip(la, lo)],
        "elev": [round(float(v), 1) for v in z],
        "dist": [round(float(v), 1) for v in s],
        "walk": None,
        "_arcs": arcs,
    }


def _diverse(g, cands, n, max_overlap=0.6):
    out, sets = [], []
    for c in cands:
        e = set(g.arc_edge[c["_arcs"]].tolist())
        if all(len(e & o) / len(e | o) <= max_overlap for o in sets):
            out.append(c)
            sets.append(e)
            if len(out) == n:
                break
    return out


def _loops_from(g, s, cost, nb, target, tol, hilly, pace):
    shapes = [("tri", i * 360 / nb) for i in range(nb)] + [("sq", (i + 0.5) * 360 / nb) for i in range(nb)]
    cands, hint = [], 1.0
    for shape, bearing in shapes:
        best, f = _fit(g, cost, s, shape, bearing, target, tol, hilly, hint)
        if best is None:
            continue
        if best[2] <= tol:
            hint = f
            c = payload(g, best[0], pace)
            c["_err"] = best[2]
            c["shape"] = f"{shape}@{int(bearing)}"
            cands.append(c)
    return cands


def _walk_starts(g, s, walk_max_m, target, profile, terrain, lit) -> dict[int, dict]:
    """Best-scoring run starts within walking range -> the walk that gets there."""
    ln, _pred = walk.walk_reach(g, s, walk_max_m)
    radius = target / (2 * math.pi)
    nodes = walk.pick_starts(g, walk.candidate_starts(g, s, ln, walk_max_m), radius, profile, terrain, lit)
    out = {}
    for n in nodes:
        arcs = walk.walk_path(g, s, n)
        wk = walk.walk_object(g, arcs)
        if wk and wk["km"] * 1000.0 <= walk_max_m + 1.0:
            out[n] = wk
    return out


def generate_loops(
    g: Graph,
    km: float,
    profile: str = "mixed",
    terrain: str = "any",
    gain_target: float | None = None,
    lit: bool = False,
    n: int = 4,
    start=None,
    tol: float | None = None,
    pace: float | None = None,
    n_bearings: int | None = None,
    walk_max_m: float = 0.0,
) -> dict:
    tol = tol or 0.03
    start = start or config.HOME
    s = g.snap(*start)
    cost = g.cost(profile, terrain, lit)
    target = km * 1000.0
    hilly = terrain == "hilly"
    nb = n_bearings or (8 if km > 30 else 12)
    cands = _loops_from(g, s, cost, nb, target, tol, hilly, pace)
    walks: dict[int, dict | None] = {}
    if walk_max_m > 0:
        walks = _walk_starts(g, s, walk_max_m, target, profile, terrain, lit)
        for node, wk in walks.items():
            for c in _loops_from(g, node, cost, min(nb, WALK_BEARINGS), target, tol, hilly, pace):
                c["walk"] = wk
                cands.append(c)
    notes: list[str] = []
    if not cands:
        notes.append(f"no loop within ±{tol * 100:.0f}% of {km:g} km found from this start")
    args = (tol, profile, terrain, gain_target, lit)
    for c in cands:
        c["score"] = round(_score(c, args) + (PENALTY_PER_KM * c["walk"]["km"] if c["walk"] else 0.0), 3)
    cands.sort(key=lambda c: c["score"])
    top = _diverse(g, cands, n)
    for c in top:
        c.pop("_err", None)
    gain_best = max((c["gain"] for c in top), default=0)
    res = {
        "candidates": top,
        "start": {"lat": float(g.node_lat[s]), "lon": float(g.node_lon[s]), "node": s},
        "request": {"km": km, "profile": profile, "terrain": terrain, "gain_target": gain_target, "lit": lit},
        "notes": notes,
        "gain_target": gain_target,
        "gain_best": gain_best,
    }
    res["fallback"] = None
    if gain_target and top and gain_best < gain_target * 0.9:
        notes.append(f"achieved {gain_best:.0f} m of {gain_target:.0f} m gain as a loop of {km:g} km")
        res["fallback"] = hilly_fallback(g, km, profile, gain_target, start, pace)
        if res["fallback"]:
            fb = res["fallback"]["candidate"]
            notes.append(f"fallback: {res['fallback']['description']} -> {fb['km']:.1f} km, +{fb['gain']:.0f} m")
    return res


def _hilly_centre(g: Graph, s: int, reach_m: float, cell: float = 500.0) -> int | None:
    """Node in the cell with the most climbing per unit of distance from the start."""
    sx, sy = g.xy[s]
    up = g.arc_climb > 0
    x, y = g.xy[g.arc_u[up], 0], g.xy[g.arc_u[up], 1]
    ix, jy = ((x - sx) // cell).astype(int), ((y - sy) // cell).astype(int)
    tot: dict = {}
    for i, j, c in zip(ix, jy, g.arc_climb[up]):
        tot[(i, j)] = tot.get((i, j), 0.0) + c
    best, best_v = None, 0.0
    for (i, j), v in tot.items():
        cx, cy = sx + (i + 0.5) * cell, sy + (j + 0.5) * cell
        d = math.hypot(cx - sx, cy - sy)
        if d > reach_m or d < cell:
            continue
        v = v / (1.0 + d / 3000.0)
        if v > best_v:
            best, best_v = (cx, cy), v
    return None if best is None else int(g.tree.query(best)[1])


def hilly_fallback(g: Graph, km: float, profile: str, gain_target: float, start, pace=None) -> dict | None:
    """Run to a hilly area, repeat a hilly circuit there, run home."""
    s = g.snap(*(start or config.HOME))
    target = km * 1000.0
    centre = _hilly_centre(g, s, 0.4 * target)
    if centre is None:
        return None
    cost = g.cost(profile, "any")
    out = shortest_path(g, cost, s, centre)
    back = shortest_path(g, cost, centre, s)
    if not out or not back:
        return None
    approach = float(g.arc_len[out].sum() + g.arc_len[back].sum())
    remaining = target - approach
    if remaining < 1200:
        return None
    reps = max(1, round(remaining / 2500.0))
    circuit_m = remaining / reps
    sub = generate_loops(
        g, circuit_m / 1000.0, profile, "hilly", None, False, 4,
        (float(g.node_lat[centre]), float(g.node_lon[centre])), tol=0.08, n_bearings=8,
    )
    if not sub["candidates"]:
        return None
    circ = max(sub["candidates"], key=lambda c: c["gain"])
    arcs = list(out) + list(circ["_arcs"]) * reps + list(back)
    cand = payload(g, arcs, pace)
    cand["score"] = 0.0
    desc = (f"run {approach / 2000:.1f} km to the hilly area, repeat a {circ['km']:.1f} km hilly circuit "
            f"{reps}x (+{circ['gain']:.0f} m each), run home")
    return {"candidate": cand, "repeats": reps, "circuit_km": circ["km"], "description": desc}
