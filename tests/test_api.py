import json
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from takas import server
from takas.config import HOME

from .test_plan import DAYS


@pytest.fixture(scope="module")
def client(graph, tmp_path_factory):
    p = tmp_path_factory.mktemp("plan") / "plan.json"
    p.write_text(json.dumps({"days": list(DAYS.values())}))
    with TestClient(server.create_app(graph, p, plan_enabled=True)) as c:
        yield c


def test_meta(client):
    m = client.get("/api/meta").json()
    assert m["dem_source"] == "fabdem" and m["degraded"] is False
    assert m["home"] == list(HOME) and m["features"] == {"plan": True}


def test_loop_and_gpx_roundtrip(client):
    r = client.post("/api/loop", json={"km": 4, "profile": "mixed", "terrain": "any"})
    assert r.status_code == 200
    body = r.json()
    assert body["candidates"] and all("_arcs" not in c for c in body["candidates"])
    cid = body["candidates"][0]["id"]
    g = client.get(f"/api/gpx/{cid}")
    assert g.status_code == 200 and "gpx" in g.headers["content-type"]
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    root = ET.fromstring(g.text)
    assert root.attrib["version"] == "1.1"
    pts = root.findall(".//g:trk/g:trkseg/g:trkpt", ns)
    assert len(pts) > 20
    assert pts[0].attrib == pts[-1].attrib
    assert pts[0].find("g:ele", ns) is not None


def test_gpx_unknown_404(client):
    assert client.get("/api/gpx/nope").status_code == 404


def test_loop_validation(client):
    assert client.post("/api/loop", json={"km": 4, "profile": "bogus"}).status_code == 422
    assert client.post("/api/loop", json={"km": -1}).status_code == 422


def test_loop_with_dragged_start(client):
    r = client.post("/api/loop", json={"km": 3, "start": {"lat": HOME[0] + 0.0004, "lon": HOME[1]}})
    assert r.status_code == 200 and r.json()["candidates"]


def test_loop_gain_target_note_and_fallback(client):
    r = client.post("/api/loop", json={"km": 6, "profile": "mixed", "terrain": "hilly", "gain_target": 400}).json()
    assert any("achieved" in n for n in r["notes"])
    assert r["fallback"] and "_arcs" not in r["fallback"]["candidate"]
    assert client.get(f"/api/gpx/{r['fallback']['candidate']['id']}").status_code == 200


def test_hills(client):
    r = client.post("/api/hills", json={"reps": 4, "minutes": 2, "warm_min": 3,
                                        "start": {"lat": HOME[0], "lon": HOME[1]}})
    assert r.status_code == 200
    body = r.json()
    assert body["candidates"], body["notes"]
    c = body["candidates"][0]
    assert [s["name"] for s in c["segments"]].count("up") == 4
    g = client.get(f"/api/gpx/{c['id']}")
    root = ET.fromstring(g.text)
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    pts = root.findall(".//g:trkpt", ns)
    assert pts[0].attrib == pts[-1].attrib


def test_hills_degraded_409(client, graph):
    graph.meta["degraded"] = True
    try:
        r = client.post("/api/hills", json={"reps": 4, "minutes": 2})
        assert r.status_code == 409 and "FABDEM" in r.json()["detail"]
    finally:
        graph.meta["degraded"] = False


def test_day_hills_mapping(client):
    r = client.get("/api/day/2030-03-10")
    assert r.status_code == 200
    b = r.json()
    assert b["plan_request"]["mode"] == "hills" and b["plan_request"]["reps"] == 6
    assert b["day"]["session"] == "Hills 6×2 min"


def test_day_loop_and_none(client):
    b = client.get("/api/day/2030-03-01").json()
    assert b["plan_request"]["mode"] == "loop"
    n = client.get("/api/day/2030-03-04").json()
    assert n["plan_request"]["mode"] == "none" and n["candidates"] == [] and n["notes"]


def test_day_missing(client):
    assert client.get("/api/day/1999-01-01").status_code == 404


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "leaflet" in r.text.lower()
    assert client.get("/static/app.js").status_code == 200


def test_api_walk_object_and_gpx_variants(client):
    start = {"lat": HOME[0], "lon": HOME[1] - 600 * 0 + (-600 / (111320 * 0.5887))}
    r = client.post("/api/hills", json={"reps": 3, "minutes": 2, "warm_min": 2,
                                        "walk_max_min": 10, "start": start})
    c = r.json()["candidates"][0]
    w = c["walk"]
    assert w and set(w) >= {"coords", "km", "time_s", "to"}
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    run = ET.fromstring(client.get(f"/api/gpx/{c['id']}").text).findall(".//g:trkpt", ns)
    assert [float(run[0].attrib["lat"]), float(run[0].attrib["lon"])] == pytest.approx(c["coords"][0], abs=1e-6)
    assert [float(run[0].attrib["lat"]), float(run[0].attrib["lon"])] == pytest.approx(
        [w["to"]["lat"], w["to"]["lon"]], abs=1e-6)
    full = ET.fromstring(client.get(f"/api/gpx/{c['id']}?walk=1").text).findall(".//g:trkpt", ns)
    assert [float(full[0].attrib["lat"]), float(full[0].attrib["lon"])] == pytest.approx(w["coords"][0], abs=1e-6)
    assert len(full) == len(run) + len(w["coords"]) - 1


def test_api_walk_default_off(client):
    c = client.post("/api/loop", json={"km": 3}).json()["candidates"][0]
    assert c["walk"] is None
    assert client.post("/api/loop", json={"km": 3, "walk_max_min": 999}).status_code == 422


def test_day_walk_params(client):
    off = client.get("/api/day/2030-03-10").json()["candidates"]
    on = client.get("/api/day/2030-03-10?walk_max_min=10").json()
    assert on["candidates"] or on["notes"]
    assert all("walk" in c for c in off)
    loop = client.get("/api/day/2030-03-01?walk_max_min=10&loops_walk=true")
    assert loop.status_code == 200 and all("walk" in c for c in loop.json()["candidates"])


def test_plan_feature_off_by_default(graph):
    with TestClient(server.create_app(graph)) as c:
        assert c.get("/api/meta").json()["features"] == {"plan": False}
        r = c.get("/api/day/2030-03-10")
        assert r.status_code == 404 and "disabled" in r.json()["detail"]


def test_plan_feature_on_without_path(graph):
    with TestClient(server.create_app(graph, plan_enabled=True)) as c:
        assert c.get("/api/meta").json()["features"] == {"plan": False}
        r = c.get("/api/day/2030-03-10")
        assert r.status_code == 404 and "not configured" in r.json()["detail"]


def test_hills_uphill_pace_input(client):
    r = client.post("/api/hills", json={"reps": 3, "minutes": 2, "warm_min": 3, "cool_min": 3, "uphill_pace": 480})
    assert r.status_code == 200
    b = r.json()
    assert b["request"]["uphill_pace"] == 480
    for c in b["candidates"]:
        assert 0.9 * 250 <= c["climb"]["length"] <= 1.3 * 250
    d = client.get("/api/day/2030-03-10?uphill_pace=480").json()
    assert d["request"]["uphill_pace"] == 480
