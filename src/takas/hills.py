from __future__ import annotations

import math

import numpy as np
from scipy.sparse.csgraph import dijkstra

from . import config, elevation
from .graph import Graph
from .loops import payload, shortest_path


class DegradedDem(RuntimeError):
    """Hill finding needs bare-earth elevation (FABDEM); Copernicus canopy makes fake climbs."""


def target_length(minutes: float, kind: str, grade: float) -> float:
    """Metres covered uphill in `minutes` of hard effort: the steeper, the shorter the repeat."""
    pace = config.HARD_FLAT_PACE * (1.0 + config.UPHILL_SLOWDOWN[kind] * max(grade, 0.0))
    return minutes * 60.0 / pace * 1000.0


def _chain_ok(g: Graph, a: int, steps_ok: bool) -> bool:
    if g.arc_bt[a] or g.arc_crossing[a]:
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
        # a hill may ease in: the first step only has to be more than a dip; the average decides
        if not any(arc_grade[a] >= 0.5 * min_grade and _chain_ok(g, a, False) for a in out):
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
                # a repeat never crosses a street. Along one street, a split or a T-junction is fine (run on the
                # far side); a crossroads, turning into another street or a path touching a street is not.
                if arcs:
                    p = arcs[-1]
                    same = g.arc_onroad[p] and g.arc_onroad[a] and g.arc_street[p] == g.arc_street[a] >= 0
                    if g.node_street_branches[node] >= (4 if same else 1):
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
                # dips are allowed part-way, but a repeat tops out on a climbing step, not a flat side path
                if nl >= lo and min_grade <= avg <= config.MAX_GRADE[kind] and gr >= 0.5 * min_grade:
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
            # falling short is what the detour is there to fix, so a short route loses to a long one
            short = 1.0 if L < 0.85 * target_m else 0.0
            key = (abs(L - target_m) / target_m + short + 0.3 * ret / max(len(arcs), 1), arcs, L)
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


def hills_session(
    g: Graph, reps: int, minutes: float, min_grade: float, kind: str, start=None,
    warmup_max_m: float = 3000.0, cool_min: float = 10.0, profile: str = "mixed",
    pace: float | None = None, uphill_pace: float | None = None,
) -> dict:
    """Warm-up (the way to the foot, run or walked, within warmup_max_m) -> reps x (run up M minutes,
    walk down) -> cool-down of cool_min minutes back to the start."""
    if g.meta.get("degraded"):
        raise DegradedDem("hill finder refuses to run on the Copernicus (canopy) DEM; use FABDEM")
    s = g.snap(*(start or config.HOME))
    cd = cool_min * 60.0 / config.EASY_PACE * 1000.0
    climbs = find_climbs(g, start, minutes, min_grade, kind, warmup_m=(0.0, warmup_max_m),
                         ideal_foot=warmup_max_m, top=6, uphill_pace=uphill_pace)
    cost = g.cost(profile, "any")
    cands, notes = [], []
    for c in climbs:
        foot = c["foot_node"]
        warm = shortest_path(g, cost, s, foot)
        back = shortest_path(g, cost, foot, s)
        down = g.arc_rev[c["arcs"]][::-1]
        if warm is None or back is None or (down < 0).any():
            continue
        cool = back
        if float(g.arc_len[back].sum()) < 0.85 * cd:
            cool = _detour(g, cost, foot, s, cd) or back
        up_m = float(g.arc_len[c["arcs"]].sum())
        w_m = float(g.arc_len[warm].sum())
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
        cand["warmup"] = {"m": round(w_m), "run_s": round(w_m / 1000.0 * config.EASY_PACE),
                          "walk_s": round(w_m / 1000.0 * config.WALK_PACE)}
        cand["walk"] = None
        # of two equally good hills the nearer wins: less getting there, less getting back
        cand["score"] = round(c["score"] + 0.5 * w_m / max(warmup_max_m, 1.0), 3)
        cand["climb"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in c.items()
                         if k not in ("arcs", "foot_node", "top_node")}
        cand["climb"]["reps"] = reps
        cand["retrace_pct"] = 0.0  # repeats are the point; not a routing defect
        cands.append(cand)
    cands.sort(key=lambda c: c["score"])
    cands = cands[:3]
    if not cands:
        notes.append(f"no climb with ≥{min_grade * 100:.0f}% grade of ~{target_length(minutes, kind, min_grade):.0f} m "
                     f"within {warmup_max_m / 1000:.1f} km of the start")
    return {"candidates": cands, "notes": notes,
            "start": {"lat": float(g.node_lat[s]), "lon": float(g.node_lon[s]), "node": s},
            "request": {"reps": reps, "minutes": minutes, "min_grade": min_grade, "kind": kind,
                        "warmup_max_m": warmup_max_m, "cool_min": cool_min, "uphill_pace": uphill_pace}}
