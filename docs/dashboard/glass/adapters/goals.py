"""
◎ glass.adapters.goals — daily quantities against a target. No network.

Source: params.goals (JSON list or already-parsed) → ~/.tiny/sticky-goals.json →
fixture (steps, water, deep work, reading). Each goal: name, value, target,
unit, optional history (last 7 days incl. today) — anything with a number and a
target: steps from a phone, litres from a bottle, hours from a timer. Output
adds fraction, remaining, pct, streak-ish "days hit" over the history, and a
day label. Values are the owner's to wire; this adapter never invents them.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "goals.json"
USER_FILE = Path.home() / ".tiny" / "sticky-goals.json"


def _fmt(v: float, unit: str) -> str:
    s = f"{v:,.0f}" if float(v).is_integer() or v >= 100 else f"{v:.1f}"
    return f"{s} {unit}".strip()


def _load(params: Dict[str, Any]) -> tuple[List[Dict[str, Any]], str]:
    g = params.get("goals")
    if g:
        if isinstance(g, str):
            g = json.loads(g)
        return list(g), "params"
    if not params.get("demo") and USER_FILE.exists():
        return list(json.loads(USER_FILE.read_text())["goals"]), "file"
    return list(json.loads(FIXTURE.read_text())["goals"]), "demo"


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    raw, source = _load(params)
    goals = []
    for r in raw[:4]:
        v, t = float(r.get("value") or 0), float(r.get("target") or 1)
        hist = [float(x) for x in (r.get("history") or [])][-7:]
        frac = v / t if t > 0 else 0
        goals.append({"name": str(r.get("name") or "goal"), "value": v, "target": t, "unit": str(r.get("unit") or ""),
                      "fraction": frac, "pct": int(round(frac * 100)), "remaining": max(0.0, t - v),
                      "display": _fmt(v, r.get("unit") or ""), "target_display": _fmt(t, r.get("unit") or ""),
                      "remaining_display": _fmt(max(0.0, t - v), r.get("unit") or ""), "done": v >= t,
                      "history": hist, "hits": sum(1 for x in hist if x >= t)})
    now = dt.datetime.now()
    return {"goals": goals, "done": sum(1 for g in goals if g["done"]), "source": source,
            "date_label": now.strftime("%A, %-d %B"), "now": now.strftime("%H:%M"),
            "day_fraction": (now.hour * 60 + now.minute) / 1440}
