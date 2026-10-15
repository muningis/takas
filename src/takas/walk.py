"""Warm-up walk to a run start W: reach, path, candidate starts and area scoring."""
from __future__ import annotations

import math

import numpy as np
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from . import config
from .graph import Graph

BIN_DEG = 15
RELIEF_WEIGHT = 10.0
PENALTY_PER_KM = 0.3


def max_m(minutes: float) -> float:
    return minutes * 60.0 / config.WALK_PACE * 1000.0


def _cost(g: Graph) -> np.ndarray:
    return g.cost("road", "any")


def walk_reach(g: Graph, s: int, max_len: float):
    """Route length (m) from s along the road-cost tree, inf beyond max_len; plus predecessors."""
    # a cheap road arc costs about its length, so this bounds the search without cutting off real walks
    d, pred = dijkstra(g.csr(_cost(g)), directed=True, indices=s, return_predecessors=True, limit=1.5 * max_len)
    ln = np.full(g.n_nodes, np.inf)
    ln[s] = 0.0
    nodes = np.nonzero(np.isfinite(d))[0]
    for n in nodes[np.argsort(d[nodes])]:
        if n == s:
            continue
        p = int(pred[n])
        ln[n] = ln[p] + g.arc_len[g.arc_between(p, int(n))]
    ln[ln > max_len] = np.inf
    return ln, pred


def walk_path(g: Graph, s: int, t: int) -> list[int]:
    from .loops import shortest_path  # loops imports this module

    return shortest_path(g, _cost(g), s, t) or []


def candidate_starts(g: Graph, s: int, ln: np.ndarray, max_len: float, bin_deg: int = BIN_DEG) -> list[int]:
    """The farthest reachable node per bearing bin, at least half the maximum walk away."""
    sx, sy = g.xy[s]
    near = np.nonzero((ln >= 0.5 * max_len) & (ln <= max_len))[0]
    best: dict[int, int] = {}
    for n in near:
        b = int((math.degrees(math.atan2(g.xy[n][0] - sx, g.xy[n][1] - sy)) % 360) // bin_deg)
        if b not in best or ln[n] > ln[best[b]]:
            best[b] = int(n)
    return [best[b] for b in sorted(best)]


def _mid_tree(g: Graph):
    t = getattr(g, "_arc_mid_tree", None)
    if t is None:
        t = g._arc_mid_tree = cKDTree((g.xy[g.arc_u] + g.xy[g.arc_v]) / 2)
    return t


def area_score(g: Graph, node: int, radius: float, profile: str, terrain: str, lit: bool = False) -> float:
    """Lower is better: profile cost per metre around node, relief as a bonus (hilly) or penalty (flat)."""
    idx = np.asarray(_mid_tree(g).query_ball_point(g.xy[node], radius), dtype=int)
    if len(idx) < 20:
        return math.inf
    ln = g.arc_len[idx]
    total = float(ln.sum())
    per_m = float(g.cost(profile, "any", lit)[idx].sum()) / total
    relief = float((g.arc_climb[idx] + g.arc_desc[idx]).sum()) / total
    return per_m + {"hilly": -RELIEF_WEIGHT, "flat": RELIEF_WEIGHT}.get(terrain, 0.0) * relief


def pick_starts(g: Graph, cands: list[int], radius: float, profile: str, terrain: str, lit: bool, top: int = 3):
    """Best-scoring candidate starts, at least radius/2 apart."""
    scored = sorted((area_score(g, c, radius, profile, terrain, lit), c) for c in cands)
    out: list[int] = []
    for sc, c in scored:
        if math.isinf(sc):
            break
        if all(float(np.hypot(*(g.xy[c] - g.xy[o]))) >= max(300.0, 0.5 * radius) for o in out):
            out.append(c)
            if len(out) == top:
                break
    return out


def walk_object(g: Graph, arcs: list[int]) -> dict | None:
    """The `walk` field of a candidate: the route from home to the run start."""
    if not arcs:
        return None
    la, lo, z, _ = g.route_profile(arcs)
    km = float(g.arc_len[arcs].sum()) / 1000.0
    return {
        "coords": [[round(float(a), 6), round(float(b), 6)] for a, b in zip(la, lo, strict=True)],
        "elev": [round(float(v), 1) for v in z],
        "km": round(km, 3),
        "time_s": round(km * config.WALK_PACE),
        "to": {"lat": round(float(la[-1]), 6), "lon": round(float(lo[-1]), 6)},
    }
