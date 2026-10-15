import json

import pytest

from takas import plan

DAYS = {  # invented days: each one carries the text pattern its mapping parses
    "easy": {"date": "2030-03-01", "type": "Easy", "session": "Easy 7 km", "structure": "7 km easy", "km": 7, "minutes": 48, "pace": "6:30–7:05/km"},
    "easy_min": {"date": "2030-03-02", "type": "Easy", "session": "Easy 40 min", "structure": "40 min very easy", "km": None, "minutes": 40, "pace": ""},
    "shake": {"date": "2030-03-03", "type": "Easy", "session": "Shakeout 20 min", "structure": "20 min very easy + 4 strides", "km": 3, "minutes": 20, "pace": ""},
    "rest": {"date": "2030-03-04", "type": "Rest", "session": "Rest", "structure": "Rest", "km": 0, "minutes": 0, "pace": ""},
    "walk": {"date": "2030-03-05", "type": "Walk", "session": "Walk 45 min", "structure": "Walk 45 min, no running", "km": 0, "minutes": 45, "pace": ""},
    "long": {"date": "2030-03-06", "type": "Long", "session": "Long 18 km", "structure": "18 km steady", "km": 18, "minutes": 125, "pace": "6:20–6:50/km"},
    "long_trail": {"date": "2030-03-07", "type": "Long", "session": "Long trail 30 km", "structure": "30 km trail, hike the steep climbs", "km": 30, "minutes": 240, "pace": "by effort"},
    "ml": {"date": "2030-03-08", "type": "Medium-long", "session": "Medium-long 12 km", "structure": "12 km easy", "km": 12, "minutes": 80, "pace": ""},
    "ml_trail": {"date": "2030-03-09", "type": "Medium-long", "session": "Medium-long trail 14 km", "structure": "14 km easy", "km": 14, "minutes": 95, "pace": ""},
    "hills": {"date": "2030-03-10", "type": "Hills", "session": "Hills 6×2 min", "structure": "15 min easy → 6 × 2 min uphill hard, walk down → 10 min easy", "km": 9, "minutes": 60, "pace": "by effort"},
    "threshold": {"date": "2030-03-11", "type": "Threshold", "session": "Threshold 4×8 min", "structure": "15 min easy → 4 × 8 min at threshold (2 min jog) → 10 min easy", "km": 12, "minutes": 70, "pace": "5:00–5:10/km"},
    "vert_rep": {"date": "2030-03-12", "type": "Vert", "session": "Vert: hill repeats 10×2 min", "structure": "15 min easy → 10 × 2 min steep uphill → 10 min easy", "km": 10, "minutes": 70, "pace": ""},
    "vert_cont": {"date": "2030-03-13", "type": "Vert", "session": "Vert: hilly continuous 75 min", "structure": "75 min on the hilliest ground nearby. Aim 400–500 m+", "km": 12, "minutes": 75, "pace": ""},
    "lap": {"date": "2030-03-14", "type": "Lap-pace", "session": "Lap-pace 4×5 km", "structure": "4 × 5 km at goal lap pace, 10 min gap", "km": 20, "minutes": 150, "pace": ""},
    "sim": {"date": "2030-03-15", "type": "SIMULATION", "session": "Lap simulation 6 h (6 laps)", "structure": "6 × 5 km on the hour. Flat loop.", "km": 30, "minutes": 360, "pace": ""},
    "laps": {"date": "2030-03-16", "type": "Long", "session": "Long 4 laps (20 km)", "structure": "One lap every hour", "km": 20, "minutes": 240, "pace": ""},
    "sim_nolap": {"date": "2030-03-17", "type": "SIMULATION", "session": "Simulation 3 h", "structure": "Flat loop", "km": 15, "minutes": 180, "pace": ""},
    "sim_big": {"date": "2030-03-18", "type": "SIMULATION", "session": "Simulation 10 h", "structure": "Flat loop", "km": 60, "minutes": 600, "pace": ""},
    "test": {"date": "2030-03-19", "type": "Test", "session": "Fitness test", "structure": "Treadmill ramp test", "km": 0, "minutes": 60, "pace": ""},
    "race": {"date": "2030-03-20", "type": "RACE", "session": "RACE — trail race", "structure": "Race", "km": 50, "minutes": 420, "pace": ""},
}


def req(k):
    return plan.day_request(DAYS[k])


def test_all_twelve_types_covered():
    types = {d["type"] for d in DAYS.values()}
    assert types == {"Easy", "Long", "Medium-long", "Threshold", "Hills", "Vert", "Lap-pace", "Test",
                     "SIMULATION", "RACE", "Rest", "Walk"}


def test_easy_and_medium_long_mixed():
    r = req("easy")
    assert (r["mode"], r["km"], r["profile"], r["terrain"]) == ("loop", 7, "mixed", "any")
    assert req("ml")["profile"] == "mixed" and req("ml")["km"] == 12


def test_long_is_road_and_trail_variants_trail():
    assert req("long")["profile"] == "road" and req("long")["km"] == 18
    assert req("long_trail")["profile"] == "trail" and req("long_trail")["km"] == 30
    assert req("ml_trail")["profile"] == "trail"


def test_threshold_flat_road_lit():
    r = req("threshold")
    assert (r["profile"], r["terrain"], r["lit"], r["km"]) == ("road", "flat", True, 12)


def test_hills_parsing():
    r = req("hills")
    assert r["mode"] == "hills" and r["reps"] == 6 and r["minutes"] == 2
    assert r["min_grade"] == 0.08 and r["kind"] == "hills"
    assert r["warm_min"] == 15 and r["cool_min"] == 10 and "total_km" not in r


def test_vert_repeats_steep():
    r = req("vert_rep")
    assert r["mode"] == "hills" and r["reps"] == 10 and r["kind"] == "vert" and r["min_grade"] == 0.12


def test_vert_continuous_gain_target():
    r = req("vert_cont")
    assert (r["mode"], r["profile"], r["terrain"]) == ("loop", "trail", "hilly")
    assert r["gain_target"] == 400 and r["gain_range"] == [400, 500] and r["km"] == 12


def test_fixed_lap_length_comes_from_the_plan():
    for k in ("lap", "sim", "laps"):
        r = req(k)
        assert r["mode"] == "loop" and r["km"] == 5.0 and r["terrain"] == "flat" and r["tol"] == 0.01, k


def test_fixed_lap_fallbacks(monkeypatch):
    from takas import config
    monkeypatch.setattr(config, "LAP_KM", None)
    r = req("sim_nolap")
    assert r["mode"] == "loop" and r["km"] == 15 and r["terrain"] == "flat"
    big = req("sim_big")
    assert big["mode"] == "none" and "lap_km" in big["reason"]
    monkeypatch.setattr(config, "LAP_KM", 4.2)
    assert req("sim_nolap")["km"] == 4.2 and req("sim_big")["km"] == 4.2


def test_minutes_to_km():
    assert req("easy_min")["km"] == pytest.approx(40 * 60 / 405 , abs=0.1)
    assert req("shake")["km"] == pytest.approx(20 * 60 / 405, abs=0.1)


def test_no_route_types_explain():
    for k in ("rest", "walk", "test", "race"):
        r = req(k)
        assert r["mode"] == "none" and r["reason"], k


def test_pace_parsing():
    assert plan.parse_pace("6:30–7:05/km") == pytest.approx((390 + 425) / 2)
    assert plan.parse_pace("6:45/km") == 405
    assert plan.parse_pace("by effort") is None
    assert req("easy")["pace"] == pytest.approx(407.5)


def test_load_and_lookup(tmp_path, monkeypatch):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"days": list(DAYS.values())}))
    days = plan.load_plan(p)
    assert plan.find_day(days, "2030-03-10")["session"] == "Hills 6×2 min"
    assert plan.find_day(days, "1999-01-01") is None


def test_load_plan_without_path_is_a_clear_error(monkeypatch):
    from takas import config
    monkeypatch.setattr(config, "PLAN_PATH", None)
    with pytest.raises(FileNotFoundError, match="not configured"):
        plan.load_plan()
