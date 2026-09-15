"""
✅ glass.adapters.habits — a dot-matrix habit log, owner-fed.

Source, in order: params.habits (list of {name, days}) → ~/.tiny/sticky-habits.json
(the owner edits a file; the card follows) → glass/fixtures/habits.json (demo).
`days` is a string of 0/1, oldest first, LAST char = today; shorter strings are
left-padded with 0. `date` (YYYY-MM-DD) anchors "today" for the fixture/tests.
Output: window (default 30 days) per habit as bools, streak (consecutive days
ending today — or yesterday when today is still open), 30-day rate, done-today,
per-day totals for the column bars, and month/weekday labels.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "habits.json"
USER_FILE = Path.home() / ".tiny" / "sticky-habits.json"


def _source(params: Dict[str, Any]) -> Dict[str, Any]:
    if params.get("habits"):
        h = params["habits"]
        if isinstance(h, str):
            h = json.loads(h)
        return {"habits": h, "date": params.get("date")}
    if not params.get("demo") and USER_FILE.exists():
        return json.loads(USER_FILE.read_text())
    return json.loads(FIXTURE.read_text())


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    src = _source(params)
    n = int(params.get("days") or 30)
    today = dt.date.fromisoformat(str(params.get("date") or src.get("date") or dt.date.today().isoformat()))
    first = today - dt.timedelta(days=n - 1)
    habits: List[Dict[str, Any]] = []
    for h in src["habits"][:8]:
        s = str(h.get("days", "")).replace(" ", "")
        s = ("0" * n + s)[-n:]
        marks = [c == "1" for c in s]
        streak, i = 0, n - 1
        if not marks[-1]:
            i = n - 2                       # today still open: count from yesterday
        while i >= 0 and marks[i]:
            streak += 1
            i -= 1
        habits.append({"name": str(h.get("name", "habit")), "marks": marks, "streak": streak,
                       "rate": sum(marks) / n, "today": marks[-1], "best": _best(marks)})
    totals = [sum(h["marks"][i] for h in habits) for i in range(n)]
    dates = [first + dt.timedelta(days=i) for i in range(n)]
    months = []
    for i, d in enumerate(dates):
        months.append(d.strftime("%b") if (i == 0 or d.day == 1) else "")
    return {"habits": habits, "n": n, "date": today.isoformat(), "today_label": today.strftime("%A, %-d %B"),
            "done_today": sum(1 for h in habits if h["today"]), "totals": totals,
            "dows": [d.strftime("%a")[0] for d in dates], "doms": [d.day for d in dates], "months": months,
            "month_rate": (sum(totals) / (n * len(habits))) if habits else 0.0,
            "now": dt.datetime.now().strftime("%H:%M"),
            "source": "params" if params.get("habits") else "file" if (not params.get("demo") and USER_FILE.exists()) else "demo"}


def _best(marks: List[bool]) -> int:
    best = cur = 0
    for m in marks:
        cur = cur + 1 if m else 0
        best = max(best, cur)
    return best
