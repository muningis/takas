from __future__ import annotations

import json
import re
from pathlib import Path

from . import config

NO_ROUTE = {
    "Rest": "Rest day, no route needed.",
    "Walk": "Walk day, no route generated.",
    "Test": "Test session, no route needed.",
    "RACE": "Race day: the course is fixed, no route generated.",
}
MAX_SINGLE_LOOP_KM = 42.0


def load_plan(path: str | Path | None = None) -> list[dict]:
    """Read-only; never writes to the plan file."""
    path = path or config.PLAN_PATH
    if path is None:
        raise FileNotFoundError("plan path not configured (set [plan] path in takas.toml or TAKAS_PLAN)")
    with open(path, encoding="utf-8") as f:
        return json.load(f)["days"]


def lap_km(session: str, structure: str) -> float | None:
    """Lap length of a fixed-lap day, read from the plan text: '4 × 5 km' or '4 laps (20 km)'."""
    text = f"{session} {structure}"
    if m := re.search(r"\d+\s*[×x]\s*(\d+(?:\.\d+)?)\s*km", text):
        return float(m.group(1))
    if m := re.search(r"(\d+)\s*(?:laps?|loops?|yards?)\s*\((\d+(?:\.\d+)?)\s*km\)", text, re.IGNORECASE):
        return round(float(m.group(2)) / int(m.group(1)), 3)
    return None


def find_day(days: list[dict], date: str) -> dict | None:
    return next((d for d in days if d["date"] == date), None)


def parse_pace(s) -> float | None:
    """'6:30–7:05/km' -> mean seconds per km; None for 'by effort' etc."""
    if not s:
        return None
    vals = [int(m) * 60 + int(sec) for m, sec in re.findall(r"(\d+):(\d{2})", s)]
    return sum(vals) / len(vals) if vals else None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def day_request(day: dict) -> dict:
    """Map a plan day to a generator request; the mapping table is in docs/plan.md."""
    typ, session, structure = day["type"], day.get("session", ""), day.get("structure", "")
    base = {"type": typ, "session": session, "pace": parse_pace(day.get("pace"))}
    if typ in NO_ROUTE:
        return {**base, "mode": "none", "reason": NO_ROUTE[typ]}
    km = _num(day.get("km"))
    minutes = _num(day.get("minutes"))
    low = f"{session} {structure}".lower()
    trail = "trail" in session.lower()

    if typ in ("SIMULATION", "Lap-pace") or re.search(r"\b\d+\s*(?:laps?|loops?|yards?)\b", session, re.IGNORECASE) and typ == "Long":
        lap = lap_km(session, structure) or config.LAP_KM
        if lap:
            return {**base, "mode": "loop", "km": lap, "profile": "road", "terrain": "flat",
                    "lit": False, "tol": 0.01, "note": f"flat fixed-lap loop of {lap:g} km"}
        if km and km <= MAX_SINGLE_LOOP_KM:
            return {**base, "mode": "loop", "km": km, "profile": "road", "terrain": "flat", "lit": False}
        return {**base, "mode": "none",
                "reason": "No lap length in this day's text; set [plan] lap_km in takas.toml."}

    if typ == "Hills" or (typ == "Vert" and "repeats" in low):
        m = re.search(r"(\d+)\s*[×x]\s*(\d+(?:\.\d+)?)\s*min", session)
        reps, mins = (int(m.group(1)), float(m.group(2))) if m else (6, 2.0)
        warm = re.match(r"\s*(\d+)\s*min", structure)
        cool = re.search(r"→\s*(\d+)\s*min easy\s*$", structure.strip())
        steep = typ == "Vert"
        return {**base, "mode": "hills", "reps": reps, "minutes": mins, "kind": "vert" if steep else "hills",
                "min_grade": config.MIN_GRADE["vert" if steep else "hills"],
                "warm_min": float(warm.group(1)) if warm else 15.0,
                "cool_min": float(cool.group(1)) if cool else 10.0}

    if typ == "Vert":  # hilly continuous
        g = re.search(r"(\d+)\s*[–-]\s*(\d+)\s*m\+?", structure)
        lo_hi = [int(g.group(1)), int(g.group(2))] if g else [400, 500]
        return {**base, "mode": "loop", "km": km or round((minutes or 75) * 60 / 480, 1), "profile": "trail",
                "terrain": "hilly", "lit": False, "gain_target": lo_hi[0], "gain_range": lo_hi}

    if re.match(r"(easy|shakeout)\s+\d+\s*min", session, re.IGNORECASE) or km is None or km == 0:
        mm = re.search(r"(\d+)\s*min", session)
        mins = float(mm.group(1)) if mm else (minutes or 30.0)
        km = round(mins * 60 / config.EASY_PACE, 1)

    if typ == "Threshold":
        return {**base, "mode": "loop", "km": km, "profile": "road", "terrain": "flat", "lit": True}
    profile = "trail" if trail else ("road" if typ == "Long" else "mixed")
    return {**base, "mode": "loop", "km": km, "profile": profile, "terrain": "any", "lit": False}


def run_request(g, req: dict, start=None, n: int = 4, walk_max_m: float = 0.0, loops_walk: bool = False,
                uphill_pace: float | None = None) -> dict:
    """Execute a day request against the loaded graph; loop days walk only when loops_walk is set."""
    from . import hills, loops

    if req["mode"] == "none":
        return {"candidates": [], "notes": [req["reason"]]}
    if req["mode"] == "hills":
        return hills.hills_session(
            g, reps=req["reps"], minutes=req["minutes"], min_grade=req["min_grade"], kind=req["kind"],
            start=start, warm_min=req["warm_min"], cool_min=req["cool_min"],
            pace=req.get("pace"), walk_max_m=walk_max_m, uphill_pace=uphill_pace,
        )
    return loops.generate_loops(
        g, km=req["km"], profile=req["profile"], terrain=req["terrain"], gain_target=req.get("gain_target"),
        lit=req.get("lit", False), n=n, start=start, tol=req.get("tol"), pace=req.get("pace"),
        walk_max_m=walk_max_m if loops_walk else 0.0,
    )
