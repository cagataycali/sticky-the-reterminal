"""
💸 glass.adapters.budget — the month's spending against its budget.

Source: params.entries (JSON) → ~/.tiny/sticky-budget.json → fixture (a September with
rent on the 1st). Contract: {currency, month_budget, entries: [{date: YYYY-MM-DD, amount,
category, note, fixed?}]}. `fixed` marks bills (rent, insurance) so the pace line is judged
on the discretionary rest — otherwise every month starts "over budget" on the 1st.
params: at (YYYY-MM-DD pin), demo.

Output: month_label, day, days_in_month, currency, budget, spent, fixed, variable,
pace (variable spend expected by today at an even burn), delta (variable − pace; + is over),
daily_left (what's left per remaining day), cumulative [{day, total}] (variable, by day),
categories [{name, total, share}] (variable, sorted), recent [{date_label, amount, category,
note}] (last 6), largest, projected (variable projected to month end + fixed), demo.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "budget.json"
USER_FILE = Path.home() / ".tiny" / "sticky-budget.json"


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    src: Optional[Dict[str, Any]] = None
    source = "demo"
    if params.get("entries"):
        raw = params["entries"]
        src = {"currency": params.get("currency", "$"), "month_budget": params.get("month_budget", 0),
               "entries": json.loads(raw) if isinstance(raw, str) else raw}
        source = "pushed"
    elif not params.get("demo") and USER_FILE.exists():
        try:
            src = json.loads(USER_FILE.read_text())
            source = "file"
        except (OSError, ValueError):
            src = None
    if src is None:
        src = json.loads(FIXTURE.read_text())
        source = "demo"
    if params.get("at"):
        today = dt.date.fromisoformat(str(params["at"]))
    elif source == "demo" and src.get("month"):
        y, m = (int(x) for x in src["month"].split("-"))
        today = dt.date(y, m, 7)
    else:
        today = dt.date.today()
    dim = calendar.monthrange(today.year, today.month)[1]
    ents = []
    for e in src.get("entries", []):
        try:
            d = dt.date.fromisoformat(str(e["date"]))
        except (KeyError, ValueError):
            continue
        if d.year == today.year and d.month == today.month and d <= today:
            ents.append({"date": d, "amount": float(e.get("amount", 0)), "category": str(e.get("category", "other")),
                         "note": str(e.get("note", "")), "fixed": bool(e.get("fixed"))})
    ents.sort(key=lambda e: e["date"])
    budget = float(src.get("month_budget") or 0)
    fixed = sum(e["amount"] for e in ents if e["fixed"])
    variable = sum(e["amount"] for e in ents if not e["fixed"])
    var_budget = max(0.0, budget - fixed)
    pace = var_budget * today.day / dim
    by_day: Dict[int, float] = defaultdict(float)
    for e in ents:
        if not e["fixed"]:
            by_day[e["date"].day] += e["amount"]
    cum, run = [], 0.0
    for day in range(1, today.day + 1):
        run += by_day.get(day, 0.0)
        cum.append({"day": day, "total": run})
    cats: Dict[str, float] = defaultdict(float)
    for e in ents:
        if not e["fixed"]:
            cats[e["category"]] += e["amount"]
    categories = sorted(({"name": k, "total": v, "share": (v / variable if variable else 0)} for k, v in cats.items()),
                        key=lambda c: -c["total"])
    days_left = dim - today.day
    return {"month_label": today.strftime("%B %Y"), "day": today.day, "days_in_month": dim, "currency": str(src.get("currency", "$")),
            "budget": budget, "spent": fixed + variable, "fixed": fixed, "variable": variable, "var_budget": var_budget,
            "pace": pace, "delta": variable - pace, "daily_left": ((var_budget - variable) / days_left) if days_left else 0.0,
            "cumulative": cum, "categories": categories,
            "recent": [{"date_label": e["date"].strftime("%-d %b"), "amount": e["amount"], "category": e["category"], "note": e["note"]}
                       for e in reversed(ents[-6:])],
            "largest": max((e for e in ents if not e["fixed"]), key=lambda e: e["amount"], default=None),
            "projected": fixed + (variable / today.day * dim if today.day else 0), "source": source, "demo": source == "demo"}
