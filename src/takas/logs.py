"""Activity log: one JSON object per line in data/logs/takas.jsonl.

It records what was asked, what came back and what was looked at in the browser, so a complaint
like "this climb crosses the road" can be traced to the exact route. It lives under data/
(gitignored) because requests carry the start point. Read it with `takas logs`.
"""
from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROUTE_POINTS = 80  # enough to redraw a route on a map, small enough to keep lines readable
UP_POINTS = 30


class ActivityLog:
    def __init__(self, log_dir: str | Path):
        self.path = Path(log_dir) / "takas.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._log = logging.getLogger(f"takas.activity.{self.path}")
        self._log.setLevel(logging.INFO)
        self._log.propagate = False
        if not self._log.handlers:
            h = RotatingFileHandler(self.path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(message)s"))
            self._log.addHandler(h)

    def event(self, kind: str, **fields) -> None:
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "kind": kind, **fields}
        self._log.info(json.dumps(rec, ensure_ascii=False, default=str))

    def tail(self, n: int = 20) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(line) for line in deque(f, maxlen=n)]


def _thin(coords: list, n: int) -> list:
    if len(coords) <= n:
        return [list(p) for p in coords]
    step = (len(coords) - 1) / (n - 1)
    return [list(coords[round(i * step)]) for i in range(n)]


def summarize(res: dict) -> dict:
    """The parts of a generator result needed to see each candidate again: numbers, segments,
    the climb, and a thinned copy of the route."""
    items = list(res.get("candidates", []))
    if res.get("fallback"):
        items.append({**res["fallback"]["candidate"], "fallback": True})
    out = []
    for c in items:
        s = {k: c[k] for k in ("id", "km", "gain", "loss", "time_s", "paved_pct", "green_pct", "retrace_pct",
                               "max_grade", "score") if k in c}
        if c.get("fallback"):
            s["fallback"] = True
        if c.get("walk"):
            s["walk"] = {k: c["walk"][k] for k in ("km", "time_s", "to") if k in c["walk"]}
        if c.get("segments"):
            segs: dict[str, float] = {}
            for x in c["segments"]:
                segs[x["name"]] = round(segs.get(x["name"], 0) + x["to_m"] - x["from_m"])
            s["segments"] = segs
            up = next((x for x in c["segments"] if x["name"] == "up"), None)
            if up and c.get("dist"):
                pts = [p for p, d in zip(c["coords"], c["dist"]) if up["from_m"] - 0.5 <= d <= up["to_m"] + 0.5]
                s["up_route"] = _thin(pts, UP_POINTS)
        if c.get("climb"):
            s["climb"] = c["climb"]
        if c.get("repeat"):
            s["repeat"] = c["repeat"]
        s["route"] = _thin(c.get("coords", []), ROUTE_POINTS)
        out.append(s)
    return {"notes": res.get("notes", []), "candidates": out}


def _ll(p: dict) -> str:
    return f"{p['lat']:.5f},{p['lon']:.5f}"


def format_record(r: dict) -> str:
    """One readable block per record, for `takas logs`."""
    head = f"{r['ts'][11:19]} {r['kind']}"
    if r.get("source") == "ui":
        return f"{head} (ui) " + " ".join(f"{k}={v}" for k, v in r.get("data", {}).items())
    if r["kind"] == "start":
        return f"{head} graph built {r.get('graph_built_at')} · {r.get('n_edges')} edges · plan day {r.get('plan_feature')}"
    params = " ".join(f"{k}={v}" for k, v in (r.get("params") or {}).items() if v not in (None, False, 0, ""))
    if r["kind"] == "error":
        return f"{head} {r.get('request')} {params} -> {r.get('status')} {r.get('detail')}"
    lines = [f"{head} {params}" + (f" ({r['ms']} ms)" if "ms" in r else "")]
    res = r.get("result") or {}
    for note in res.get("notes", []):
        lines.append(f"   note: {note}")
    for i, c in enumerate(res.get("candidates", []), 1):
        bits = [f"#{i} {c.get('id')}", f"{c.get('km', 0):.2f} km", f"+{c.get('gain', 0)} m"]
        if cl := c.get("climb"):
            bits.append(f"climb {cl['length']:.0f} m {cl['grade'] * 100:.1f}% foot {_ll(cl['foot'])} -> top {_ll(cl['top'])}")
        if w := c.get("walk"):
            bits.append(f"walk {w['km']:.1f} km")
        if c.get("fallback"):
            bits.append("fallback")
        lines.append("   " + "  ".join(bits))
    return "\n".join(lines)
