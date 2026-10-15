import itertools

import pytest

from takas import hills
from takas.config import HOME

from . import synth

WIN = (100.0, 1500.0)  # synthetic grid is small; real default is 1.2-3.5 km


def test_target_length_shrinks_with_grade():
    # hard effort is 4:50/km on the flat and slows with grade, so steep repeats are short
    assert hills.target_length(2, "hills", 0.10) == pytest.approx(295.6, abs=2)
    assert hills.target_length(2, "hills", 0.15) < hills.target_length(2, "hills", 0.08)
    assert hills.target_length(2, "vert", 0.15) < hills.target_length(2, "hills", 0.15)


def test_finds_planted_slope_direction_and_length(graph):
    r = hills.find_climbs(graph, HOME, minutes=2, min_grade=0.04, kind="hills", warmup_m=WIN)
    assert r, "no climb found"
    c = r[0]
    T = hills.target_length(2, "hills", c["grade"])
    assert 0.75 * T <= c["length"] <= 1.25 * T
    assert c["grade"] >= 0.04
    assert c["grade"] == pytest.approx(0.08, abs=0.01)
    # directed: goes up the east ramp, ends higher than it starts
    assert c["top_elev"] - c["foot_elev"] == pytest.approx(c["grade"] * c["length"], rel=0.1)
    assert c["top_elev"] > c["foot_elev"] + 15
    assert c["top"]["lon"] > c["foot"]["lon"]
    # nothing found on the flat west side
    for cl in r:
        assert cl["foot"]["lon"] > HOME[1]


def test_steep_requires_8_percent(graph):
    flat_only = hills.find_climbs(graph, HOME, minutes=2, min_grade=0.12, kind="vert", warmup_m=WIN)
    assert flat_only == []
    r = hills.find_climbs(graph, HOME, minutes=2, min_grade=0.07, kind="vert", warmup_m=WIN)
    T = hills.target_length(2, "vert", r[0]["grade"]) if r else 0
    assert r and 0.75 * T <= r[0]["length"] <= 1.25 * T


def test_bridges_excluded(graph):
    r = hills.find_climbs(graph, HOME, minutes=2, min_grade=0.01, kind="hills", warmup_m=(0, 3000))
    for c in r:
        assert not graph.arc_bt[c["arcs"]].any()


def test_session_route_structure(graph):
    r = hills.hills_session(graph, reps=4, minutes=2, min_grade=0.04, kind="hills",
                            start=HOME, cool_min=4, warmup_max_m=800.0)
    c = r["candidates"][0]
    s = graph.snap(*HOME)
    arcs = c["_arcs"]
    assert graph.arc_u[arcs[0]] == s and graph.arc_v[arcs[-1]] == s
    names = [x["name"] for x in c["segments"]]
    assert names[0] == "warm-up" and names[-1] == "cool-down"
    assert names.count("up") == 4 and names.count("walk down") == 4
    # warm-up is just the way to the foot (within the limit); the cool-down fills its minutes
    seg = {x["name"]: x["to_m"] - x["from_m"] for x in c["segments"]}
    assert seg["warm-up"] <= 800 * 1.3
    assert seg["cool-down"] >= 0.85 * 4 * 60 / 405 * 1000
    assert c["gain"] >= 4 * c["climb"]["gain"] * 0.95
    ups = [x for x in c["segments"] if x["name"] == "up"]
    assert all(u["to_m"] - u["from_m"] == pytest.approx(ups[0]["to_m"] - ups[0]["from_m"], abs=1) for u in ups)


def test_degraded_mode_refuses(graph):
    graph.meta["degraded"] = True
    try:
        with pytest.raises(hills.DegradedDem):
            hills.hills_session(graph, reps=4, minutes=2, min_grade=0.04, kind="hills", start=HOME)
    finally:
        graph.meta["degraded"] = False


def test_near_climb_not_penalised(graph):
    # a climb inside the warm-up radius is reachable by looping the warm-up; only feet beyond it cost
    c = hills.find_climbs(graph, HOME, minutes=2, min_grade=0.04, kind="hills", warmup_m=WIN)[0]
    args = (graph, c["arcs"], c["length"], c["grade"], c["foot_elev"], c["top_elev"])
    T, tol, ideal, half = 363.6, 0.25, 2200.0, 1100.0
    near = hills._describe(*args, 1300.0, T, tol, ideal, half)["score"]
    at = hills._describe(*args, 2200.0, T, tol, ideal, half)["score"]
    far = hills._describe(*args, 3100.0, T, tol, ideal, half)["score"]
    assert near == pytest.approx(at)
    assert far > at


def test_warmup_is_the_direct_way_to_the_foot(graph):
    from takas.loops import shortest_path
    r = hills.hills_session(graph, reps=2, minutes=2, min_grade=0.04, kind="hills", start=HOME, warmup_max_m=3000.0)
    c = r["candidates"][0]
    seg = c["segments"][0]
    assert seg["name"] == "warm-up" and c["walk"] is None
    foot = graph.snap(c["climb"]["foot"]["lat"], c["climb"]["foot"]["lon"])
    direct = shortest_path(graph, graph.cost("mixed", "any"), graph.snap(*HOME), foot)
    assert seg["to_m"] - seg["from_m"] == pytest.approx(graph.arc_len[direct].sum(), abs=1)
    w = c["warmup"]
    assert w["m"] == pytest.approx(seg["to_m"] - seg["from_m"], abs=1)
    assert w["walk_s"] > w["run_s"] > 0


def test_feet_beyond_the_warmup_limit_are_excluded(graph):
    kw = {"reps": 3, "minutes": 2, "min_grade": 0.04, "kind": "hills", "start": synth.ll(400, 1000)}
    far = hills.hills_session(graph, warmup_max_m=500.0, **kw)  # the ramp starts 800 m east
    assert far["candidates"] == [] and "0.5 km" in far["notes"][0]
    near = hills.hills_session(graph, warmup_max_m=3000.0, **kw)
    assert near["candidates"] and all(c["climb"]["dist_from_start"] <= 3000 for c in near["candidates"])


def test_repeats_run_up_walk_down_time(graph):
    from takas import config
    r = hills.hills_session(graph, reps=4, minutes=2, min_grade=0.04, kind="hills",
                            start=HOME, warmup_max_m=800.0)
    c = r["candidates"][0]
    seg = {x["name"]: x["to_m"] - x["from_m"] for x in c["segments"]}
    down_m = seg["walk down"]
    easy_m = seg["warm-up"] + seg["cool-down"]
    want = easy_m / 1000 * config.EASY_PACE + 4 * (2 * 60 + down_m / 1000 * config.WALK_PACE)
    assert c["time_s"] == pytest.approx(want, rel=0.01)
    assert c["repeat"]["up_s"] == 120 and c["repeat"]["down_s"] == pytest.approx(down_m / 1000 * config.WALK_PACE, abs=1)


def test_runnable_steepness_preferred(graph):
    # ~10 % is a runnable steep hill; gentler is a drag, much steeper turns into hiking
    c = hills.find_climbs(graph, HOME, minutes=2, min_grade=0.04, kind="hills", warmup_m=WIN)[0]

    def score(avg):
        T = hills.target_length(2, "hills", avg)
        return hills._describe(graph, c["arcs"], T, avg, 0.0, avg * T, 1500.0, T, 0.25, 2200.0, 1100.0,
                               "hills")["score"]

    assert score(0.10) < score(0.07) and score(0.10) < score(0.15)


def test_hills_cap_excludes_hiking_grades(graph, monkeypatch):
    from takas import config
    monkeypatch.setitem(config.MAX_GRADE, "hills", 0.07)
    r = hills.find_climbs(graph, HOME, minutes=2, min_grade=0.04, kind="hills", warmup_m=WIN, top=50)
    assert all(c["grade"] <= 0.07 for c in r)


@pytest.mark.parametrize("pace", [330.0, 480.0])
def test_climb_length_follows_users_uphill_pace(graph, pace):
    # the rep is M minutes at the runner's own uphill pace; the hill must offer that much climb
    want = 2 * 60 / pace * 1000
    r = hills.find_climbs(graph, HOME, minutes=2, min_grade=0.04, kind="hills", warmup_m=WIN, uphill_pace=pace)
    assert r
    for c in r:
        assert 0.9 * want <= c["length"] <= 1.3 * want


def test_session_echoes_pace_and_card_numbers(graph):
    r = hills.hills_session(graph, reps=3, minutes=2, min_grade=0.04, kind="hills", start=HOME,
                            cool_min=3, warmup_max_m=800.0, uphill_pace=480.0)
    assert r["request"]["uphill_pace"] == 480.0
    c = r["candidates"][0]
    assert c["repeat"]["up_s"] == 120 and c["repeat"]["pace"] == 480.0


def _crosses_street(g, p, q):
    same = g.arc_onroad[p] and g.arc_onroad[q] and g.arc_street[p] == g.arc_street[q] >= 0
    return g.node_street_branches[g.arc_v[p]] >= (4 if same else 1)


def test_climbs_never_cross_a_street(graph):
    r = hills.find_climbs(graph, HOME, minutes=2, min_grade=0.04, kind="hills", warmup_m=(0, 3000), top=50)
    assert r
    for c in r:
        arcs = c["arcs"]
        assert not graph.arc_crossing[arcs].any()
        assert not any(_crosses_street(graph, p, q) for p, q in itertools.pairwise(arcs))


@pytest.mark.parametrize(("foot", "pace", "mark"), [((1200, 1200), 240.0, "d_split"),
                                                    ((1500, 1250), 290.0, "d_tee")])
def test_climb_may_follow_a_street_past_a_split_and_a_t_junction(graph, foot, pace, mark):
    # starting at street D: 2 min at 4:00/km (~500 m) runs through its split point, at 4:50/km (~414 m)
    # through its T-junction. Both stay on the same street, so neither is a crossing.
    from . import synth
    r = hills.find_climbs(graph, synth.ll(*foot), minutes=2, min_grade=0.04, kind="hills", warmup_m=(0, 20),
                          uphill_pace=pace)
    inner = {int(graph.arc_v[a]) for c in r for a in c["arcs"][:-1]}
    assert graph.node_index[graph.test_marks[mark]] in inner


def test_climb_ends_climbing(graph):
    # a dip is allowed part-way, but the top of a repeat is not a flat side path
    for pace in (240.0, 290.0, 330.0):
        for c in hills.find_climbs(graph, HOME, minutes=2, min_grade=0.04, kind="hills", warmup_m=(0, 3000),
                                   top=50, uphill_pace=pace):
            assert graph.arc_grade[c["arcs"][-1]] >= 0.02


def test_climb_may_start_gently(graph):
    # a real hill often eases in: the first step only needs to be more than a dip (half the minimum
    # grade); the whole climb must still average the minimum. Street F starts at 5 % against a 7 % minimum.
    from . import synth
    r = hills.find_climbs(graph, synth.ll(1170, 1350), minutes=2, min_grade=0.07, kind="hills",
                          warmup_m=(0, 20), uphill_pace=343.0)
    assert r and all(c["grade"] >= 0.07 for c in r)
