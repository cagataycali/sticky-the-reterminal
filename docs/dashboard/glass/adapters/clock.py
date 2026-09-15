"""
🕰 glass.adapters.clock — time, in the local zone and a few others. No network.

params: face (analog|digits|world|minimal), clock=HH:MM and date=YYYY-MM-DD
(tests), tz, zones="Europe/Istanbul,Europe/London,Asia/Tokyo" for the world face.
Output: h, m, clock, date labels, ISO week, day-of-year, and for each zone:
city, time, weekday if different, offset delta in hours vs local, is_day
(07–19 by local solar-free convention — honest about being a convention).
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List

from .calendar import ZoneInfo, local_tz

DEFAULT_ZONES = "Europe/Istanbul,Europe/London,Asia/Tokyo"


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    tz = local_tz() if not params.get("tz") else ZoneInfo(str(params["tz"]))
    now = dt.datetime.now(tz)
    if params.get("date"):
        d = dt.date.fromisoformat(str(params["date"]))
        now = now.replace(year=d.year, month=d.month, day=d.day)
    if params.get("clock"):
        hh, mm = str(params["clock"]).split(":")
        now = now.replace(hour=int(hh), minute=int(mm), second=0)
    zones: List[Dict[str, Any]] = []
    for z in str(params.get("zones") or DEFAULT_ZONES).split(","):
        z = z.strip()
        if not z:
            continue
        try:
            t = now.astimezone(ZoneInfo(z))
        except Exception:
            continue
        delta = (t.utcoffset() - now.utcoffset()).total_seconds() / 3600
        zones.append({"zone": z, "city": z.split("/")[-1].replace("_", " "), "time": t.strftime("%H:%M"),
                      "day": t.strftime("%a") if t.date() != now.date() else "",
                      "delta": delta, "delta_label": ("same time" if delta == 0 else f"{delta:+g} h"),
                      "is_day": 7 <= t.hour < 19, "h": t.hour, "m": t.minute})
    return {"face": str(params.get("face") or "analog"), "h": now.hour, "m": now.minute,
            "clock": now.strftime("%H:%M"), "date": now.strftime("%A, %-d %B"), "short": now.strftime("%a %-d"),
            "weekday": now.strftime("%A"), "day": now.day, "month": now.strftime("%B"), "year": now.year,
            "week": now.isocalendar()[1], "doy": now.timetuple().tm_yday,
            "days_in_year": 366 if now.year % 4 == 0 and (now.year % 100 or not now.year % 400) else 365,
            "tz": str(tz), "tz_short": now.strftime("%Z") or str(tz).split("/")[-1], "zones": zones}
