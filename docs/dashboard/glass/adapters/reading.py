"""
📖 glass.adapters.reading — the book on the desk.

Source: params.book (JSON) → ~/.tiny/sticky-reading.json → fixture. Shape:
{current:{title, author, pages, started, log:[{date, page}], note?}, next:[…], finished:[{title, author, date, pages}]}.
Derived: page/percent, pace (pages per day over the last 14 calendar days, counting the
days without an entry as zero — the honest pace), pages left, finish date at that pace,
days reading, a 14-day pages-per-day vector for the strip, this year's finished count
and pages. A card that goes stale gracefully: nothing on it is a clock.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "reading.json"
USER_FILE = Path.home() / ".tiny" / "sticky-reading.json"
WINDOW = 14


def _load(params: Dict[str, Any]) -> tuple[Dict[str, Any], str]:
    if params.get("book"):
        return json.loads(str(params["book"])), "params"
    if not params.get("demo") and USER_FILE.exists():
        return json.loads(USER_FILE.read_text()), str(USER_FILE)
    return json.loads(FIXTURE.read_text()), "demo"


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    raw, source = _load(params)
    cur = raw.get("current") or {}
    today = dt.date.fromisoformat(str(params["date"])) if params.get("date") else dt.date.today()
    log = sorted(({"date": dt.date.fromisoformat(e["date"]), "page": int(e["page"])} for e in cur.get("log", [])), key=lambda e: e["date"])
    pages = int(cur.get("pages") or 0)
    page = log[-1]["page"] if log else int(cur.get("page") or 0)
    started = dt.date.fromisoformat(cur["started"]) if cur.get("started") else (log[0]["date"] if log else today)
    # pages read per calendar day over the last WINDOW days (delta of the running page)
    per_day: List[int] = []
    prev = 0
    by_day = {}
    for e in log:
        by_day[e["date"]] = e["page"]
    running = 0
    days_axis = [today - dt.timedelta(days=i) for i in range(WINDOW - 1, -1, -1)]
    # running page before the window
    before = [e["page"] for e in log if e["date"] < days_axis[0]]
    running = before[-1] if before else 0
    for d in days_axis:
        if d in by_day:
            per_day.append(max(0, by_day[d] - running))
            running = by_day[d]
        else:
            per_day.append(0)
    active = [d for d in days_axis if d in by_day]
    pace = sum(per_day) / WINDOW if log else 0.0
    left = max(0, pages - page)
    finish = today + dt.timedelta(days=round(left / pace)) if pace > 0 and left > 0 else None
    last = log[-1]["date"] if log else None
    fin = raw.get("finished") or []
    this_year = [b for b in fin if str(b.get("date", "")).startswith(str(today.year))]
    return {
        "title": cur.get("title", "—"), "author": cur.get("author", ""), "pages": pages, "page": page,
        "pct": (page / pages) if pages else 0.0, "left": left,
        "started": started.strftime("%-d %b"), "days": (today - started).days + 1,
        "pace": pace, "per_day": per_day, "axis": [d.strftime("%a")[0] for d in days_axis],
        "reading_days": len(active), "window": WINDOW,
        "finish": finish.strftime("%a %-d %b") if finish else None, "finish_in": (finish - today).days if finish else None,
        "last": last.strftime("%a %-d %b") if last else None, "last_gap": (today - last).days if last else None,
        "note": cur.get("note", ""), "next": list(raw.get("next") or [])[:3],
        "finished_year": len(this_year), "finished_pages": sum(int(b.get("pages") or 0) for b in this_year),
        "finished": [{"title": b["title"], "author": b.get("author", ""), "date": dt.date.fromisoformat(b["date"]).strftime("%b") if b.get("date") else ""} for b in fin[:4]],
        "source": source, "demo": source == "demo", "date_label": today.strftime("%A, %-d %B"),
    }
