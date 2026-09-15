"""
🕰 glass.adapters.now — the lock screen's facts: clock, date, weather, next event, inbox.

Composes the other adapters; each keeps its own fixture fallback so `demo`
never touches the network. params are forwarded: place/lat/lon/units (weather),
ics_url/tz (calendar), demo. `clock` = "HH:MM" override for tests/previews.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional

from . import calendar as cal
from . import notifications as notif
from . import weather as wx


def _next_event(day: Dict[str, Any], now_min: int) -> Optional[Dict[str, Any]]:
    for e in day["events"]:
        s = e["dt_start"].hour * 60 + e["dt_start"].minute
        en = e["dt_end"].hour * 60 + e["dt_end"].minute
        if en > now_min:
            return {**e, "ongoing": s <= now_min, "in_min": max(0, s - now_min)}
    return None


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    demo = bool(params.get("demo"))
    tz = cal.local_tz() if not params.get("tz") else cal.ZoneInfo(str(params["tz"]))
    now = dt.datetime.now(tz)
    if params.get("clock"):
        hh, mm = str(params["clock"]).split(":")
        now = now.replace(hour=int(hh), minute=int(mm))
    try:
        weather = wx.fetch(params)
    except Exception:
        weather = wx.fetch({**params, "demo": 1})
    day = cal.fetch_day({**params, "date": now.date().isoformat()})
    now_min = now.hour * 60 + now.minute
    nxt = _next_event(day, now_min)
    later = [e for e in day["events"] if e["dt_start"].hour * 60 + e["dt_start"].minute > now_min]
    inbox = notif.fetch({"demo": 1} if demo else {"live": 1})
    return {
        "clock": now.strftime("%H:%M"), "date": now.strftime("%A, %-d %B"), "weekday": now.strftime("%A"),
        "weather": weather, "next": nxt,
        "then": [{"start": e["start"], "title": e["title"], "location": e["location"]}
                 for e in later if nxt is None or e["dt_start"] != nxt["dt_start"] or e["title"] != nxt["title"]][:4],
        "all_day": [e["title"] for e in day["all_day"]], "events_today": len(day["events"]),
        "unread": inbox["unread"], "tz": str(tz),
    }
