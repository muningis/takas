import itertools
import math

import numpy as np
import pytest

from takas import config, walk
from takas.config import HOME

from . import synth

WALK_M = 833.0  # 10 min at 12:00/km


def test_pace_constant():
    assert config.WALK_PACE == 720
    assert walk.max_m(20) == pytest.approx(1666.7, abs=1)


def test_reach_respects_max_and_paths_are_contiguous(graph):
    s = graph.snap(*HOME)
    ln, _pred = walk.walk_reach(graph, s, WALK_M)
    assert ln[s] == 0
    reached = np.nonzero(np.isfinite(ln))[0]
    assert len(reached) > 20 and ln[reached].max() <= WALK_M
    t = int(reached[np.argmax(ln[reached])])
    arcs = walk.walk_path(graph, s, t)
    assert graph.arc_u[arcs[0]] == s and graph.arc_v[arcs[-1]] == t
    assert all(graph.arc_v[a] == graph.arc_u[b] for a, b in itertools.pairwise(arcs))


def test_candidate_starts_far_and_spread(graph):
    s = graph.snap(*HOME)
    ln, _ = walk.walk_reach(graph, s, WALK_M)
    cs = walk.candidate_starts(graph, s, ln, WALK_M)
    assert 4 <= len(cs) <= 24
    assert all(0.5 * WALK_M <= ln[c] <= WALK_M for c in cs)
    sx, sy = graph.xy[s]
    bins = {int((math.degrees(math.atan2(graph.xy[c][0] - sx, graph.xy[c][1] - sy)) % 360) // 15) for c in cs}
    assert len(bins) == len(cs)


def test_area_score_prefers_hills_or_flat_by_terrain(graph):
    ids = graph.test_ids
    east, west = ids[(16, 10)], ids[(4, 10)]
    h_e = walk.area_score(graph, east, 600, "mixed", "hilly")
    h_w = walk.area_score(graph, west, 600, "mixed", "hilly")
    f_e = walk.area_score(graph, east, 600, "mixed", "flat")
    f_w = walk.area_score(graph, west, 600, "mixed", "flat")
    assert h_e < h_w
    assert f_w < f_e


def test_synth_ids_exist():
    assert synth.HOME == HOME
