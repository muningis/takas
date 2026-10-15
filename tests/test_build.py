import numpy as np
import pytest

from takas import build


def R(**tags):
    return build.way_rules(tags)


def test_motorway_trunk_dropped():
    assert R(highway="motorway") is None
    assert R(highway="trunk_link") is None
    assert R(highway="construction") is None
    assert R(highway="proposed") is None


def test_class_mapping():
    assert R(highway="footway")["cls"] == "foot"
    assert R(highway="residential")["cls"] == "res"
    assert R(highway="tertiary")["cls"] == "tert"
    assert R(highway="primary")["cls"] == "sec"
    assert R(highway="track")["cls"] == "path"
    assert R(highway="steps")["cls"] == "steps"


def test_sidewalk_separate_drops_carriageway():
    assert R(highway="residential", **{"sidewalk:both": "separate"}) is None
    assert R(highway="tertiary", sidewalk="separate") is None
    assert R(highway="tertiary", **{"sidewalk:left": "separate", "sidewalk:right": "separate"}) is None
    assert R(highway="tertiary", **{"sidewalk:left": "separate"}) is not None
    assert R(highway="footway", sidewalk="separate") is not None  # only carriageways


def test_access_rules():
    assert R(highway="residential", access="private") is None
    assert R(highway="residential", access="private", foot="yes") is not None
    assert R(highway="residential", access="no", foot="designated") is not None
    assert R(highway="cycleway", foot="no") is None
    assert R(highway="cycleway") is not None
    assert R(highway="footway", foot="no") is None


def test_oneway_ignored_oneway_foot_counts():
    a = R(highway="residential", oneway="yes")
    assert a["fwd"] and a["rev"]
    b = R(highway="footway", **{"oneway:foot": "yes"})
    assert b["fwd"] and not b["rev"]
    c = R(highway="footway", **{"oneway:foot": "-1"})
    assert not c["fwd"] and c["rev"]


def test_surface_defaults():
    assert R(highway="residential")["paved"] is True
    assert R(highway="path")["paved"] is False
    assert R(highway="path", surface="asphalt")["paved"] is True
    assert R(highway="footway", surface="gravel")["paved"] is False
    assert R(highway="track", surface="paving_stones")["paved"] is True


def test_lit_bridge_tunnel():
    a = R(highway="footway", lit="yes", bridge="yes")
    assert a["lit"] and a["bridge"] and not a["tunnel"]
    assert R(highway="footway", tunnel="yes")["tunnel"]
    assert not R(highway="footway", lit="no")["lit"]


def nid(g, i, j):
    return g.test_ids[(i, j)]


def edge_between(g, a, b):
    if a not in g.node_index or b not in g.node_index:
        return np.zeros(0, dtype=int)
    n1, n2 = g.node_index[a], g.node_index[b]
    m = ((g.eu == n1) & (g.ev == n2)) | ((g.eu == n2) & (g.ev == n1))
    return np.nonzero(m)[0]


def test_sidewalk_separate_edge_absent(graph):
    g = graph
    assert len(edge_between(g, nid(g, 4, 15), nid(g, 5, 15))) == 0
    # the column through (4,15) survives as one edge past the dropped carriageway junction
    assert len(edge_between(g, nid(g, 4, 14), nid(g, 4, 16))) == 1
    assert nid(g, 4, 15) not in g.node_index
    # the sidewalk footway is routable between (2,15) and (8,15)
    assert len(edge_between(g, nid(g, 2, 15), nid(g, 8, 15))) == 1


def test_dropped_rows(graph):
    g = graph
    assert len(edge_between(g, nid(g, 3, 18), nid(g, 4, 18))) == 0
    assert len(edge_between(g, nid(g, 3, 19), nid(g, 4, 19))) == 0


def test_oneway_ignored_in_graph(graph):
    g = graph
    e = edge_between(g, nid(g, 3, 8), nid(g, 4, 8))
    assert len(e) == 1
    assert g.edge_fwd[e[0]] and g.edge_rev[e[0]]


def test_largest_component_only(graph):
    g = graph
    assert g.n_nodes >= 20 * 20 - 40 - 40  # grid minus dropped rows' own-only nodes
    # isolated footway nodes are gone: all nodes mutually reachable
    from scipy.sparse.csgraph import connected_components

    n, _lab = connected_components(g.csr_structure(), directed=True, connection="strong")
    assert n == 1


def test_no_duplicate_uv_pairs(graph):
    g = graph
    keys = g.arc_u.astype(np.int64) * g.n_nodes + g.arc_v
    assert len(np.unique(keys)) == len(keys)
    # both geometries of the duplicate pair survive
    a, b = nid(g, 12, 12), nid(g, 13, 12)
    n1, n2 = g.node_index[a], g.node_index[b]
    assert g.has_arc(n1, n2)
    path_edges = [e for e in range(g.n_edges) if g.edge_cls_name(e) == "path"]
    assert len(path_edges) >= 1  # path edge kept (possibly split by a midpoint node)


def test_green_fraction(graph):
    g = graph
    e = edge_between(g, nid(g, 16, 2), nid(g, 17, 2))[0]
    assert g.edge_green[e] == pytest.approx(1.0, abs=0.02)
    e2 = edge_between(g, nid(g, 3, 3), nid(g, 4, 3))[0]
    assert g.edge_green[e2] == pytest.approx(0.0, abs=0.01)
    assert g.edge_green.max() <= 1.0 + 1e-6


def test_edge_lengths(graph):
    g = graph
    e = edge_between(g, nid(g, 3, 3), nid(g, 4, 3))[0]
    assert g.edge_len[e] == pytest.approx(100, rel=0.01)


def test_meta(synth_dir):
    import json

    d, _ = synth_dir
    m = json.loads((d / "build" / "meta.json").read_text())
    assert m["dem_source"] == "fabdem" and m["degraded"] is False
    assert m["n_nodes"] > 300 and m["n_edges"] > 500


def test_bridge_dip_removed(graph):
    g = graph
    e = edge_between(g, nid(g, 4, 5), nid(g, 5, 5))[0]
    s, t = g.geom_off[e], g.geom_off[e + 1]
    z = g.geom_z[s:t]
    assert z.min() >= min(z[0], z[-1]) - 0.01
    assert g.climb_f[e] < 0.5 and g.desc_f[e] < 0.5
    # a neighbouring non-bridge edge crossing the same dip region would show it
    e2 = edge_between(g, nid(g, 4, 5), nid(g, 4, 6))[0]
    assert e2 != e


def test_hill_grades(graph):
    g = graph
    e = edge_between(g, nid(g, 14, 10), nid(g, 15, 10))[0]
    uphill = [a for a in range(g.n_arcs) if g.arc_edge[a] == e and g.arc_climb[a] > 7]
    assert len(uphill) == 1
    assert g.arc_climb[uphill[0]] == pytest.approx(8, abs=0.5)
    assert g.arc_maxup[uphill[0]] == pytest.approx(0.08, abs=0.005)
    flat = edge_between(g, nid(g, 3, 10), nid(g, 4, 10))[0]
    assert g.climb_f[flat] < 0.2 and g.desc_f[flat] < 0.2


def test_route_gain_uses_hysteresis(graph):
    g = graph
    n = [g.node_index[nid(g, i, 10)] for i in range(10, 17)]
    arcs = g.arcs_from_nodes(n)
    st = g.route_stats(arcs)
    assert st["gain"] == pytest.approx(0.08 * 400, abs=2.5)
    assert st["km"] == pytest.approx(0.6, rel=0.02)
