"""
⏳ glass.adapters.countdown — days to (and since) the things that matter.

Source order: params.events (JSON list) → ~/.tiny/sticky-countdowns.json → fixture.
Event: {label, date: YYYY-MM-DD, from?: YYYY-MM-DD (progress start), every?: "year"
(recurs; rolled to the next occurrence), since?: true (count up from the date)}.
params.to + params.label make a one-off without a file. date=YYYY-MM-DD anchors
today. Output: hero (the nearest upcoming, or the first `since` if nothing is
ahead), upcoming list, since list; every entry has days, weeks+days, weekday,
label, date_label, progress 0..1 when `from` is known.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "countdown.json"
USER_FILE = Path.home() / ".tiny" / "sticky-countdowns.json"


def _source(params: Dict[str, Any]) -> Dict[str, Any]:
    if params.get("to"):
        return {"events": [{"label": params.get("label") or "the day", "date": params["to"], "from": params.get("from")}]}
    if params.get("events"):
        ev = params["events"]
        return {"events": json.loads(ev) if isinstance(ev, str) else ev}
    if not params.get("demo") and USER_FILE.exists():
        return json.loads(USER_FILE.read_text())
    return json.loads(FIXTURE.read_text())


def _roll(d: dt.date, today: dt.date, every: str) -> dt.date:
    if every == "year":
        while d < today:
            try:
                d = d.replace(year=d.year + 1)
            except ValueError:            # 29 Feb
                d = d.replace(year=d.year + 1, day=28)
    elif every == "month":
        while d < today:
            m = d.month % 12 + 1
            d = d.replace(year=d.year + (m == 1), month=m)
    return d


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    src = _source(params)
    today = dt.date.fromisoformat(str(params.get("date") or src.get("date") or dt.date.today().isoformat()))
    upcoming: List[Dict[str, Any]] = []
    since: List[Dict[str, Any]] = []
    for e in src.get("events", [])[:12]:
        d = dt.date.fromisoformat(str(e["date"]))
        if e.get("every"):
            d = _roll(d, today, str(e["every"]))
        delta = (d - today).days
        item = {"label": str(e.get("label", "")), "date": d.isoformat(), "date_label": d.strftime("%a %-d %b"),
                "long_label": d.strftime("%A, %-d %B %Y"), "days": abs(delta), "weeks": abs(delta) // 7,
                "rem_days": abs(delta) % 7, "recurs": bool(e.get("every"))}
        if e.get("since") or (delta < 0 and not e.get("every")):
            item["kind"] = "since"
            since.append(item)
        else:
            item["kind"] = "to"
            if e.get("from"):
                f = dt.date.fromisoformat(str(e["from"]))
                span = max(1, (d - f).days)
                item["progress"] = min(1.0, max(0.0, (today - f).days / span))
                item["from_label"] = f.strftime("%-d %b")
            upcoming.append(item)
    upcoming.sort(key=lambda x: x["days"])
    since.sort(key=lambda x: x["days"])
    hero = upcoming[0] if upcoming else (since[0] if since else None)
    return {"today": today.isoformat(), "today_label": today.strftime("%A, %-d %B"),
            "now": dt.datetime.now().strftime("%H:%M"), "hero": hero,
            "upcoming": [u for u in upcoming if u is not hero], "since": [s for s in since if s is not hero],
            "source": "params" if (params.get("to") or params.get("events")) else "file" if (not params.get("demo") and USER_FILE.exists()) else "demo"}
