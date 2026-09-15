"""
📜 glass.adapters.almanac — on this day, from Wikipedia's curated feed.

Source: https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/all/MM/DD (no key, needs a
User-Agent, 8 s) → fixture (glass/fixtures/almanac.json, 7 September, slimmed).
params: date (YYYY-MM-DD, default today), n_events (default 8), demo.

Selection is deliberate, not "latest first": events come from Wikipedia's `selected` list
spread evenly across the centuries so the column reads like a timeline; births and deaths
are the same even spread over the sorted lists (the far past first — an emperor of 923
beside a swimmer of 2000 is the point); holidays drop the "Christian feast day" filler.

Output: date_label, weekday, doy (day of year), events [{year, text}], births, deaths,
holidays [text], demo.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Dict, List

import requests

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "almanac.json"
UA = {"User-Agent": "sticky-glass/1.0 (https://github.com/cagataycali/sticky-the-reterminal)"}
URL = "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/all/{m:02d}/{d:02d}"


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def _spread(items: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    items = sorted({(e.get("year"), _clean(e["text"])) for e in items if e.get("year") is not None})
    if len(items) <= n:
        picked = items
    else:
        picked = [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]
    return [{"year": y, "text": t} for y, t in picked]


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    when = dt.date.fromisoformat(str(params["date"])) if params.get("date") else dt.date.today()
    n = int(params.get("n_events", 8) or 8)
    src = None
    if not params.get("demo"):
        try:
            r = requests.get(URL.format(m=when.month, d=when.day), headers=UA, timeout=8)
            r.raise_for_status()
            src = r.json()
        except Exception:
            src = None
    demo = src is None
    if demo:
        src = json.loads(FIXTURE.read_text())
        if not params.get("date"):
            when = dt.date(when.year, src["month"], src["day"])
    hol = [re.split(r":|\), ", _clean(h["text"]))[0] for h in src.get("holidays", [])]
    hol = [h + ")" if h.count("(") > h.count(")") else h for h in hol]
    hol = [h for h in hol if not h.lower().startswith(("christian feast", "feast day", "earliest day", "latest day"))]
    return {"date_label": when.strftime("%-d %B"), "weekday": when.strftime("%A"), "doy": when.timetuple().tm_yday,
            "events": _spread(src.get("selected") or src.get("events", []), n), "births": _spread(src.get("births", []), 4),
            "deaths": _spread(src.get("deaths", []), 3), "holidays": hol[:4], "demo": demo}
