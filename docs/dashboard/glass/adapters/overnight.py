"""
🌙 glass.adapters.overnight — the frame the glass HOLDS while everything sleeps.

E-ink keeps its last image at zero draw, so the sleep frame is the one card that will
be looked at for hours without a refresh. It must not carry a clock or anything that
goes stale by 06:00. What it carries: tomorrow — its first event, how many events,
sunrise, the forecast high/low, the to-do items due tomorrow, the moon tonight, and
the fleet's battery at lights-out (a fact about the past, so it stays true).
Sources: calendar.fetch_day(date+1), weather.days[1], sun.tomorrow, moon, todo,
fleet — every one with a fixture; a failure in one leaves the others (marked).
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List

from . import calendar as cal, moon as md, sun as sd, todo as td, weather as wx

try:  # fleet is optional — battery at lights-out is a nicety
    from . import fleet as fl
except Exception:  # noqa: BLE001
    fl = None


def _safe(fn, params, name, missing: List[str]):
    try:
        return fn(params)
    except Exception:  # noqa: BLE001
        if params.get("demo"):
            raise
        try:
            return fn({**params, "demo": 1})
        finally:
            missing.append(name)


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    missing: List[str] = []
    today = dt.date.today()
    if params.get("date"):
        today = dt.date.fromisoformat(str(params["date"]))
    tomorrow = today + dt.timedelta(days=1)
    day = _safe(cal.fetch_day, {**params, "date": tomorrow.isoformat()}, "calendar", missing)
    w = _safe(wx.fetch, params, "weather", missing)
    s = _safe(sd.fetch, params, "sun", missing)
    m = _safe(md.fetch, params, "moon", missing)
    t = _safe(td.fetch, params, "todo", missing)
    events = day["events"]
    first = events[0] if events else None
    tw = next((x for x in w.get("days", []) if x.get("date") == tomorrow.isoformat()), (w.get("days") or [None, None])[1] if len(w.get("days", [])) > 1 else None)
    cond, glyph = wx.describe(tw["code"]) if tw else ("—", "cloud")
    due = [x for x in t["open"] if x["due"] == "tomorrow"]
    fleet = None
    if fl is not None:
        try:
            f = fl.fetch(params)
            fleet = {"online": f.get("online"), "total": f.get("total")}
        except Exception:  # noqa: BLE001
            fleet = None
    return {
        "tonight": today.strftime("%A, %-d %B"), "tomorrow": tomorrow.strftime("%A"), "tomorrow_label": day["day_label"],
        "first": first, "n_events": len(events), "all_day": [e["title"] for e in day["all_day"]],
        "events": events[:6],
        "hi": tw["hi"] if tw else None, "lo": tw["lo"] if tw else None, "precip": tw.get("precip") if tw else None,
        "cond": cond, "glyph": glyph, "units": w.get("units", "f"), "place": w.get("place", ""),
        "sunrise": s["tomorrow"]["sunrise"], "sunset": s["tomorrow"]["sunset"],
        "moon_name": m["name"], "moon_illum": m["illumination"], "moon_f": m["fraction"],
        "due": due, "n_open": t["n_open"],
        "fleet": fleet, "missing": missing,
        "demo": bool(params.get("demo")) or day["calendar"].lower().startswith("demo"),
    }
