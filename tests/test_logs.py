import json

from takas import logs


def test_activity_log_writes_json_lines(tmp_path):
    log = logs.ActivityLog(tmp_path / "logs")
    log.event("hills", params={"reps": 6}, ms=12)
    log.event("select", data={"id": "abc", "rank": 1})
    lines = (tmp_path / "logs" / "takas.jsonl").read_text().splitlines()
    recs = [json.loads(x) for x in lines]
    assert [r["kind"] for r in recs] == ["hills", "select"]
    assert recs[0]["params"] == {"reps": 6} and "ts" in recs[0]
    assert [r["kind"] for r in log.tail(1)] == ["select"]


def test_summary_keeps_what_is_needed_to_see_the_route():
    c = {"id": "x", "km": 7.3, "gain": 150, "loss": 150, "time_s": 3600, "coords": [[54.0 + i / 1000, 25.0] for i in range(500)],
         "segments": [{"name": "warm-up", "from_m": 0, "to_m": 2200}, {"name": "up", "from_m": 2200, "to_m": 2438}],
         "climb": {"length": 238, "grade": 0.091, "foot": {"lat": 54.1, "lon": 25.1}, "top": {"lat": 54.2, "lon": 25.2}},
         "dist": [i * 10.0 for i in range(500)], "walk": None}
    s = logs.summarize({"candidates": [c], "notes": ["n"]})
    k = s["candidates"][0]
    assert k["id"] == "x" and k["segments"] == {"warm-up": 2200, "up": 238}
    assert k["climb"]["foot"] == {"lat": 54.1, "lon": 25.1}
    assert 2 <= len(k["route"]) <= 80 and k["route"][0] == [54.0, 25.0]
    assert k["up_route"] and len(k["up_route"]) <= 30
    assert s["notes"] == ["n"]


def test_format_record_reads_like_what_the_runner_saw():
    rec = {"ts": "2030-03-10T14:02:11", "kind": "hills", "ms": 612,
           "params": {"reps": 6, "minutes": 2, "uphill_pace": 330, "warm_min": 15, "cool_min": 10, "walk_max_min": 20},
           "result": {"notes": [], "candidates": [{"id": "a1", "km": 7.35, "gain": 150, "walk": {"km": 0.5},
                      "climb": {"length": 238, "grade": 0.091, "foot": {"lat": 54.1, "lon": 25.1},
                                "top": {"lat": 54.2, "lon": 25.2}}}]}}
    text = logs.format_record(rec)
    assert "14:02:11 hills" in text and "reps=6" in text and "612 ms" in text
    assert "#1 a1" in text and "climb 238 m 9.1%" in text and "walk 0.5 km" in text
    sel = logs.format_record({"ts": "2030-03-10T14:02:15", "kind": "select", "source": "ui", "data": {"id": "a1", "rank": 0}})
    assert "select" in sel and "a1" in sel
