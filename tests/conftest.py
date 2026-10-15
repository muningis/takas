import os
import tempfile

# Tests must see the public defaults, never a developer's personal takas.toml or TAKAS_* variables,
# and must not write into the real data/ (logs live there).
os.environ["TAKAS_CONFIG"] = os.path.join(os.path.dirname(__file__), "no-such-takas.toml")
os.environ["TAKAS_DATA"] = tempfile.mkdtemp(prefix="takas-test-data-")
for _k in ("TAKAS_HOME", "TAKAS_PLAN", "TAKAS_FEATURE_PLAN"):
    os.environ.pop(_k, None)

import pytest

from . import synth


@pytest.fixture(scope="session")
def synth_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("synth")
    info = synth.write_osm(d / "g.osm")
    synth.write_dem(d / "dem.tif")
    return d, info


@pytest.fixture(scope="session")
def graph(synth_dir):
    from takas import build
    from takas import graph as G

    d, info = synth_dir
    out = d / "build"
    build.build_graph(d / "g.osm", d / "dem.tif", out, bbox=synth.bbox(), dem_source="fabdem")
    g = G.Graph.load(out)
    g.test_ids = info["ids"]
    g.test_marks = info["marks"]
    return g
