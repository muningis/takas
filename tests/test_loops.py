import pytest

from takas import config, loops
from takas.config import HOME


def edge_sets(g, c):
    return {int(g.arc_edge[a]) for a in c["_arcs"]}


@pytest.fixture(scope="module")
def mixed(graph):
    return loops.generate_loops(graph, km=4.0, profile="mixed", terrain="any", start=HOME)


def test_loop_closed_and_on_target(graph, mixed):
    assert mixed["candidates"], mixed["notes"]
    s = graph.snap(*HOME)
    for c in mixed["candidates"]:
        arcs = c["_arcs"]
        assert graph.arc_u[arcs[0]] == s and graph.arc_v[arcs[-1]] == s
        assert abs(c["km"] - 4.0) / 4.0 <= 0.03
        assert c["coords"][0] == c["coords"][-1]


def test_retrace_low(mixed):
    best = mixed["candidates"][0]
    assert best["retrace_pct"] < 10


def test_diversity(graph, mixed):
    cs = mixed["candidates"]
    assert len(cs) >= 2
    for i in range(len(cs)):
        for j in range(i + 1, len(cs)):
            a, b = edge_sets(graph, cs[i]), edge_sets(graph, cs[j])
            assert len(a & b) / len(a | b) <= 0.6


def test_flat_prefers_flat_side(graph):
    flat = loops.generate_loops(graph, km=4.0, profile="mixed", terrain="flat", start=HOME)
    hilly = loops.generate_loops(graph, km=4.0, profile="mixed", terrain="hilly", start=HOME)
    assert flat["candidates"] and hilly["candidates"]
    assert flat["candidates"][0]["gain"] < hilly["candidates"][0]["gain"]
    assert flat["candidates"][0]["gain"] < 12


def test_fixed_lap_tight_tolerance(graph):
    r = loops.generate_loops(graph, km=5.0, profile="road", terrain="flat", start=HOME, tol=0.01)
    assert r["candidates"], r["notes"]
    for c in r["candidates"]:
        assert abs(c["km"] - 5.0) / 5.0 <= 0.01


def test_gain_target_honesty(graph):
    r = loops.generate_loops(graph, km=4.0, profile="trail", terrain="hilly", gain_target=500, start=HOME)
    assert r["gain_target"] == 500
    assert r["gain_best"] < 500
    assert any("achieved" in n and "500" in n for n in r["notes"])


def test_candidate_payload(mixed):
    c = mixed["candidates"][0]
    for k in ("id", "km", "gain", "loss", "paved_pct", "green_pct", "lit_pct", "retrace_pct", "time_s",
              "coords", "elev", "dist", "score"):
        assert k in c
    assert len(c["coords"]) == len(c["elev"]) == len(c["dist"])


def test_start_snaps_from_dragged_marker(graph):
    r = loops.generate_loops(graph, km=3.0, profile="mixed", start=(HOME[0] + 0.0003, HOME[1] + 0.0004))
    assert r["candidates"]


def test_fallback_offered_when_target_missed(graph):
    r = loops.generate_loops(graph, km=6.0, profile="mixed", terrain="hilly", gain_target=400, start=HOME)
    fb = r["fallback"]
    assert fb is not None
    s = graph.snap(*HOME)
    arcs = fb["candidate"]["_arcs"]
    assert graph.arc_u[arcs[0]] == s and graph.arc_v[arcs[-1]] == s
    assert abs(fb["candidate"]["km"] - 6.0) / 6.0 <= 0.12
    assert fb["repeats"] >= 1
    assert fb["candidate"]["gain"] > r["gain_best"]
    assert "repeat" in fb["description"]


def test_no_fallback_when_target_met(graph):
    r = loops.generate_loops(graph, km=4.0, profile="mixed", terrain="hilly", gain_target=20, start=HOME)
    assert r["fallback"] is None


def test_walk_off_has_no_walk(graph, mixed):
    assert all(c["walk"] is None for c in mixed["candidates"])
    r = loops.generate_loops(graph, km=4.0, profile="mixed", terrain="any", start=HOME, walk_max_m=0)
    assert [c["coords"] for c in r["candidates"]] == [c["coords"] for c in mixed["candidates"]]


def test_walk_on_hilly_loop_starts_away_with_more_gain(graph):
    wmax = 833.0
    base = loops.generate_loops(graph, km=6.0, profile="mixed", terrain="hilly", start=HOME)
    r = loops.generate_loops(graph, km=6.0, profile="mixed", terrain="hilly", start=HOME, walk_max_m=wmax)
    best = r["candidates"][0]
    assert best["walk"] is not None
    w = best["walk"]
    assert 0 < w["km"] * 1000 <= wmax + 1
    assert w["coords"][-1] == best["coords"][0] == best["coords"][-1]
    assert w["to"] == {"lat": best["coords"][0][0], "lon": best["coords"][0][1]}
    assert w["time_s"] == pytest.approx(w["km"] * config.WALK_PACE, abs=1)
    assert best["gain"] > base["candidates"][0]["gain"]
    assert abs(best["km"] - 6.0) / 6.0 <= 0.03
    s = graph.snap(*HOME)
    assert graph.arc_u[best["_arcs"][0]] != s
