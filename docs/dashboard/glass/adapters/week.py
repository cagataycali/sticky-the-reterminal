"""
📆 glass.adapters.week — seven days of weather + the calendar's load per day.

weather.fetch(days=7) (Open-Meteo / fixture) joined with calendar.events_between
over the same 7 days (ICS / fixture). Per day: date, dow, hi/lo, precip %,
glyph, events (count, all-day count, first titles). Week min/max for the bars.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List

from . import calendar as cal
from . import weather as wx
from .weather import describe


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    try:
        w = wx.fetch({**params, "days": 7})
    except Exception:
        w = wx.fetch({**params, "demo": 1})
    days = w["days"][:7]
    tz = cal.ZoneInfo(str(params["tz"])) if params.get("tz") else cal.local_tz()
    first = dt.date.fromisoformat(days[0]["date"])
    start = dt.datetime.combine(first, dt.time.min, tzinfo=tz)
    try:
        c, _, _, cname = cal.resolve({**params, "date": first.isoformat()})
        evs = cal.events_between(c, start, start + dt.timedelta(days=len(days)), tz)
    except Exception:
        evs, cname = [], ""
    out: List[Dict[str, Any]] = []
    for i, d in enumerate(days):
        day = dt.date.fromisoformat(d["date"])
        mine = [e for e in evs if e["dt_start"].date() <= day <= (e["dt_end"] - dt.timedelta(seconds=1)).date()]
        timed = [e for e in mine if not e["all_day"]]
        label, glyph = describe(int(d["code"]), True)
        out.append({
            "date": d["date"], "dom": day.day, "dow": "Today" if i == 0 else day.strftime("%a"),
            "weekday": day.strftime("%A"), "hi": d["hi"], "lo": d["lo"], "precip": d.get("precip") or 0,
            "code": d["code"], "glyph": glyph, "condition": label,
            "n_events": len(timed), "n_all_day": len(mine) - len(timed),
            "titles": [e["title"] for e in sorted(timed, key=lambda e: e["dt_start"])][:3],
            "first": (min(timed, key=lambda e: e["dt_start"])["start"] if timed else None),
            "busy_min": sum(int((e["dt_end"] - e["dt_start"]).total_seconds() // 60) for e in timed),
        })
    last = dt.date.fromisoformat(days[-1]["date"])
    rng = f"{first.day} – {last.day} {last.strftime('%b')}" if first.month == last.month \
        else f"{first.day} {first.strftime('%b')} – {last.day} {last.strftime('%b')}"
    return {"place": w["place"], "units": w["units"], "calendar": cname, "days": out, "range": rng,
            "week_hi": max(d["hi"] for d in out), "week_lo": min(d["lo"] for d in out),
            "total_events": sum(d["n_events"] for d in out), "now": w.get("time", "")[11:16] or dt.datetime.now(tz).strftime("%H:%M")}
