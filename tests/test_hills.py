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
                            start=HOME, warm_min=3, cool_min=4, warmup_m=(100.0, 800.0))
    c = r["candidates"][0]
    s = graph.snap(*HOME)
    arcs = c["_arcs"]
    assert graph.arc_u[arcs[0]] == s and graph.arc_v[arcs[-1]] == s
    names = [x["name"] for x in c["segments"]]
    assert names[0] == "warm-up" and names[-1] == "cool-down"
    assert names.count("up") == 4 and names.count("walk down") == 4
    # the session is defined by time: warm-up and cool-down fill their minutes at easy pace
    seg = {x["name"]: x["to_m"] - x["from_m"] for x in c["segments"]}
    assert seg["warm-up"] >= 0.85 * 3 * 60 / 405 * 1000
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


def test_warmup_extended_when_climb_is_close(graph):
    warm_min = 16  # ~2.4 km, while the planted slope's foot is 1.5 km away
    w = warm_min * 60 / 405 * 1000  # easy pace 6:45/km
    r = hills.hills_session(graph, reps=2, minutes=2, min_grade=0.04, kind="hills",
                            start=HOME, warm_min=warm_min)
    seg = r["candidates"][0]["segments"][0]
    assert seg["name"] == "warm-up"
    assert seg["to_m"] - seg["from_m"] >= 0.85 * w


def test_walk_reaches_slope_that_is_otherwise_out_of_range(graph):
    start = synth.ll(400, 1000)  # 800 m west of the ramp foot
    kw = {"reps": 3, "minutes": 2, "min_grade": 0.04, "kind": "hills", "start": start, "warm_min": 2}
    assert hills.hills_session(graph, **kw)["candidates"] == []
    wmax = 833.0
    r = hills.hills_session(graph, walk_max_m=wmax, **kw)
    c = r["candidates"][0]
    w = c["walk"]
    assert w and 0 < w["km"] * 1000 <= wmax + 1
    s, arcs = graph.snap(*start), c["_arcs"]
    wnode = int(graph.arc_u[arcs[0]])
    assert wnode != s and graph.arc_v[arcs[-1]] == wnode
    assert c["coords"][0] == c["coords"][-1] == w["coords"][-1] == [w["to"]["lat"], w["to"]["lon"]]
    assert w["coords"][0] == [round(float(graph.node_lat[s]), 6), round(float(graph.node_lon[s]), 6)]
    # W lies on the west -> foot path, about one warm-up (296 m) before the foot
    foot_lon = c["climb"]["foot"]["lon"]
    assert start[1] < w["to"]["lon"] < foot_lon
    assert foot_lon - w["to"]["lon"] == pytest.approx(296 / (111320 * 0.5887), abs=0.004)
    assert [x["name"] for x in c["segments"]].count("up") == 3
    assert abs(c["km"] - 4.0) / 4.0 <= 0.15


def test_walk_not_used_when_foot_is_within_warmup(graph):
    r = hills.hills_session(graph, reps=2, minutes=2, min_grade=0.04, kind="hills",
                            start=HOME, warm_min=16, walk_max_m=833.0)
    assert r["candidates"][0]["walk"] is None
    assert graph.arc_u[r["candidates"][0]["_arcs"][0]] == graph.snap(*HOME)


def test_repeats_run_up_walk_down_time(graph):
    from takas import config
    r = hills.hills_session(graph, reps=4, minutes=2, min_grade=0.04, kind="hills",
                            start=HOME, warm_min=3, warmup_m=(100.0, 800.0))
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
    assert hills.find_climbs(graph, HOME, minutes=2, min_grade=0.04, kind="hills", warmup_m=WIN) == []


@pytest.mark.parametrize("pace", [330.0, 480.0])
def test_climb_length_follows_users_uphill_pace(graph, pace):
    # the rep is M minutes at the runner's own uphill pace; the hill must offer that much climb
    want = 2 * 60 / pace * 1000
    r = hills.find_climbs(graph, HOME, minutes=2, min_grade=0.04, kind="hills", warmup_m=WIN, uphill_pace=pace)
    assert r
    for c in r:
        assert 0.9 * want <= c["length"] <= 1.3 * want


def test_session_echoes_pace_and_card_numbers(graph):
    r = hills.hills_session(graph, reps=3, minutes=2, min_grade=0.04, kind="hills", start=HOME, warm_min=3,
                            cool_min=3, warmup_m=(100.0, 800.0), uphill_pace=480.0)
    assert r["request"]["uphill_pace"] == 480.0
    c = r["candidates"][0]
    assert c["repeat"]["up_s"] == 120 and c["repeat"]["pace"] == 480.0
