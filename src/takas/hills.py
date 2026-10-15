from __future__ import annotations

import math

import numpy as np
from scipy.sparse.csgraph import dijkstra

from . import config, elevation
from . import walk as walking
from .graph import Graph
from .loops import payload, shortest_path


class DegradedDem(RuntimeError):
    """Hill finding needs bare-earth elevation (FABDEM); Copernicus canopy makes fake climbs."""


def target_length(minutes: float, kind: str, grade: float) -> float:
    """Metres covered uphill in `minutes` of hard effort: the steeper, the shorter the repeat."""
    pace = config.HARD_FLAT_PACE * (1.0 + config.UPHILL_SLOWDOWN[kind] * max(grade, 0.0))
    return minutes * 60.0 / pace * 1000.0


def _chain_ok(g: Graph, a: int, steps_ok: bool) -> bool:
    if g.arc_bt[a]:
        return False
    return steps_ok or g.arc_cls[a] != 5


def find_climbs(
    g: Graph, start, minutes: float, min_grade: float, kind: str = "hills",
    warmup_m: tuple[float, float] = (1200.0, 3500.0), top: int = 3, tolerance: float = 0.25,
    expansion_cap: int = 600, ideal_foot: float | None = None, uphill_pace: float | None = None,
) -> list[dict]:
    s = g.snap(*(start or config.HOME))
    # with the runner's own uphill pace the hill must simply offer M minutes of climbing; ranking is then
    # by steepness, not by how exactly the length matches
    fixed = minutes * 60.0 / uphill_pace * 1000.0 if uphill_pace else None
    if fixed:
        lo, hi = 0.9 * fixed, 1.3 * fixed
    else:
        lo = target_length(minutes, kind, 0.30) * (1 - tolerance)
        hi = target_length(minutes, kind, min_grade) * (1 + tolerance)
    d = dijkstra(g.csr(g.arc_len), directed=True, indices=s, limit=warmup_m[1])
    feet = np.nonzero((d >= warmup_m[0]) & (d <= warmup_m[1]))[0]
    arc_grade = g.arc_grade
    found: list[dict] = []
    for f in feet:
        out = range(g.indptr[f], g.indptr[f + 1])
        if not any(arc_grade[a] >= 0.75 * min_grade and _chain_ok(g, a, False) for a in out):
            continue
        z0 = float(g.node_elev[f])
        best = None
        stack = [(int(f), [], 0.0, 0.0)]  # node, arcs, length, dip budget used
        expansions = 0
        while stack and expansions < expansion_cap:
            node, arcs, ln, dip = stack.pop()
            expansions += 1
            for a in range(g.indptr[node], g.indptr[node + 1]):
                if not _chain_ok(g, a, False):
                    continue
                nxt = int(g.arc_v[a])
                if nxt == f or any(g.arc_u[x] == nxt for x in arcs):
                    continue
                gr = arc_grade[a]
                nd = dip
                if gr < 0.5 * min_grade:
                    if gr < -0.02 or g.arc_len[a] > 40 or dip + g.arc_len[a] > 60:
                        continue
                    nd = dip + g.arc_len[a]
                nl = ln + g.arc_len[a]
                if nl > hi:
                    continue
                na = arcs + [a]
                z1 = float(g.node_elev[nxt])
                avg = (z1 - z0) / nl
                if nl >= lo and min_grade <= avg <= config.MAX_GRADE[kind]:
                    t = fixed or target_length(minutes, kind, avg)
                    off = abs(nl - t) / t
                    if (fixed or off <= tolerance) and (best is None or off < best[0]):
                        best = (off, na, nl, avg, z1, t)
                stack.append((nxt, na, nl, nd))
        if best:
            found.append(_describe(g, best[1], best[2], best[3], z0, best[4], float(d[f]), best[5], tolerance,
                                     ideal_foot or sum(warmup_m) / 2, (warmup_m[1] - warmup_m[0]) / 2, kind,
                                     0.3 if fixed else 1.5))
    found.sort(key=lambda c: c["score"])
    out_list: list[dict] = []
    for c in found:
        if all(math.hypot(*(g.xy[c["foot_node"]] - g.xy[o["foot_node"]])) > 300 for o in out_list):
            out_list.append(c)
            if len(out_list) == top:
                break
    return out_list


def _describe(g, arcs, length, avg, z0, z1, d_foot, T, tol, ideal_foot, half_window, kind="hills",
              length_weight=1.5):
    ln = g.arc_len[arcs]
    _, _, z, s = g.route_profile(arcs)
    std = elevation.grade_variability(z, s)
    busy = float(ln[np.isin(g.arc_cls[arcs], (2, 3))].sum() / ln.sum())
    score = (
        3.0 * std / max(avg, 0.02)
        + length_weight * abs(length - T) / (T * tol)
        + 1.0 * max(0.0, d_foot - ideal_foot) / max(half_window, 1.0)
        + 2.0 * busy
        # a runnable steep hill (~10 %) beats a gentle drag and a hike-steep bank alike
        - 1.5 * max(0.0, 1.0 - abs(avg - config.STEEP_IDEAL[kind]) / 0.05)
    )
    foot, top = int(g.arc_u[arcs[0]]), int(g.arc_v[arcs[-1]])
    return {
        "arcs": arcs, "length": float(length), "grade": float(avg), "gain": float(z1 - z0),
        "foot_elev": z0, "top_elev": z1, "score": float(score), "foot_node": foot, "top_node": top,
        "foot": {"lat": float(g.node_lat[foot]), "lon": float(g.node_lon[foot])},
        "top": {"lat": float(g.node_lat[top]), "lon": float(g.node_lon[top])},
        "dist_from_start": float(d_foot), "max_grade": elevation.max_grade(*g.route_profile(arcs)[2:]) / 100,
        "steadiness_std": std, "paved_pct": 100 * float(ln[g.arc_paved[arcs]].sum() / ln.sum()),
    }


def _detour(g: Graph, cost, a: int, b: int, target_m: float):
    """Route a -> waypoint -> b near target_m long, picking the least retracing bearing."""
    from .loops import route_via

    ax, ay = g.xy[a]
    best = None
    for k in range(8):
        br = math.radians(k * 45)
        r = target_m / 2.6
        tried = []
        for _ in range(5):
            wp = int(g.tree.query([ax + r * math.sin(br), ay + r * math.cos(br)])[1])
            arcs = route_via(g, cost, [a, wp, b])
            if not arcs:
                break
            L = float(g.arc_len[arcs].sum())
            ret = len(arcs) - len(set(g.arc_edge[arcs].tolist()))
            key = (abs(L - target_m) / target_m + 0.3 * ret / max(len(arcs), 1), arcs, L)
            if best is None or key[0] < best[0]:
                best = key
            if abs(L - target_m) / target_m < 0.03:
                break
            tried.append((r, L))
            r = r * target_m / L
    return best[1] if best else None


def _add_segment(g: Graph, arcs: list, segs: list, name: str, part) -> None:
    part = list(part)
    pos = float(g.arc_len[arcs].sum()) if arcs else 0.0
    ln = float(g.arc_len[part].sum()) if part else 0.0
    segs.append({"name": name, "from_m": round(pos, 1), "to_m": round(pos + ln, 1)})
    arcs.extend(part)


def _walk_origin(g: Graph, s: int, foot: int, w: float, walk_max_m: float):
    """Run start W on the walking path home -> foot, about one warm-up before the foot (walk capped)."""
    path = walking.walk_path(g, s, foot)
    if not path:
        return s, []
    cum = np.cumsum(g.arc_len[path])
    allowed = min(float(cum[-1]) - w, walk_max_m)
    k = int(np.searchsorted(cum, allowed, side="right"))
    return (int(g.arc_v[path[k - 1]]), path[:k]) if k else (s, [])


def hills_session(
    g: Graph, reps: int, minutes: float, min_grade: float, kind: str, start=None,
    warm_min: float = 15.0, cool_min: float = 10.0, profile: str = "mixed",
    warmup_m: tuple[float, float] | None = None, pace: float | None = None, walk_max_m: float = 0.0,
    uphill_pace: float | None = None,
) -> dict:
    if g.meta.get("degraded"):
        raise DegradedDem("hill finder refuses to run on the Copernicus (canopy) DEM; use FABDEM")
    s = g.snap(*(start or config.HOME))
    w = warm_min * 60.0 / config.EASY_PACE * 1000.0
    cd = cool_min * 60.0 / config.EASY_PACE * 1000.0
    # a foot closer than the warm-up is fine: the warm-up loops before reaching it
    window = warmup_m or (0.25 * w, 1.6 * w + walk_max_m)
    # the walk makes every foot inside the window equally reachable: no distance penalty, more candidates to re-rank
    climbs = find_climbs(g, start, minutes, min_grade, kind, warmup_m=window,
                         ideal_foot=window[1] if walk_max_m else w, top=6 if walk_max_m else 3,
                         uphill_pace=uphill_pace)
    cost = g.cost(profile, "any")
    cands, notes = [], []
    for c in climbs:
        foot = c["foot_node"]
        origin, walk_arcs = s, []
        if walk_max_m > 0 and c["dist_from_start"] > w:
            origin, walk_arcs = _walk_origin(g, s, foot, w, walk_max_m)
        warm = shortest_path(g, cost, origin, foot)
        back = shortest_path(g, cost, foot, origin)
        down = g.arc_rev[c["arcs"]][::-1]
        if warm is None or back is None or (down < 0).any():
            continue
        if float(g.arc_len[warm].sum()) < 0.85 * w:
            warm = _detour(g, cost, origin, foot, w) or warm
        # the session is set by time, not distance: warm-up and cool-down fill their minutes at easy pace
        cool = back
        if float(g.arc_len[back].sum()) < 0.85 * cd:
            cool = _detour(g, cost, foot, origin, cd) or back
        up_m = float(g.arc_len[c["arcs"]].sum())
        w_m, c_m = float(g.arc_len[warm].sum()), float(g.arc_len[cool].sum())
        overrun = max(0.0, w_m - 1.15 * w) / max(w, 1.0) + max(0.0, c_m - 1.15 * cd) / max(cd, 1.0)
        arcs, segs = [], []
        _add_segment(g, arcs, segs, "warm-up", warm)
        for _ in range(reps):
            _add_segment(g, arcs, segs, "up", c["arcs"])
            _add_segment(g, arcs, segs, "walk down", down.tolist())
        _add_segment(g, arcs, segs, "cool-down", cool)
        cand = payload(g, arcs, pace)
        cand["segments"] = segs
        down_s = float(g.arc_len[down].sum()) / 1000.0 * config.WALK_PACE
        easy_m = sum(x["to_m"] - x["from_m"] for x in segs if x["name"] in ("warm-up", "cool-down"))
        cand["repeat"] = {"up_m": round(up_m), "up_s": round(minutes * 60), "down_s": round(down_s),
                          "pace": uphill_pace or round(minutes * 60 / up_m * 1000)}
        cand["time_s"] = round(easy_m / 1000.0 * (pace or config.EASY_PACE) + reps * (minutes * 60 + down_s))
        cand["walk"] = walking.walk_object(g, walk_arcs)
        wk_km = cand["walk"]["km"] if cand["walk"] else 0.0
        cand["score"] = round(c["score"] + overrun + walking.PENALTY_PER_KM * wk_km, 3)
        cand["climb"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in c.items()
                         if k not in ("arcs", "foot_node", "top_node")}
        cand["climb"]["reps"] = reps
        cand["retrace_pct"] = 0.0  # repeats are the point; not a routing defect
        cands.append(cand)
    cands.sort(key=lambda c: c["score"])
    cands = cands[:3]
    if not cands:
        notes.append(f"no climb with ≥{min_grade * 100:.0f}% grade of ~{target_length(minutes, kind, min_grade):.0f} m "
                     f"found {window[0] / 1000:.1f}-{window[1] / 1000:.1f} km from the start")
    return {"candidates": cands, "notes": notes,
            "start": {"lat": float(g.node_lat[s]), "lon": float(g.node_lon[s]), "node": s},
            "request": {"reps": reps, "minutes": minutes, "min_grade": min_grade, "kind": kind,
                        "warm_min": warm_min, "cool_min": cool_min, "uphill_pace": uphill_pace}}
