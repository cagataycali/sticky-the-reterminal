"""
▪ glass.adapters.year — the whole year as dots, today ringed.

Calendar source as the other calendar cards (ics_url → fixture). Output: year, today's
ordinal, days in the year, days left, pct; a list of 12 months each with its days as
{d, past, today, weekend, events, busy} (busy = above the median AND in the top quarter of this calendar's days); event totals
behind/ahead; the next season boundary (equinox/solstice, northern hemisphere unless
params.south) and days to it; the next event ahead. params.date pins today.
"""
from __future__ import annotations

import calendar as cal_mod
import datetime as dt
from typing import Any, Dict, List

from . import calendar as cal

SEASONS_N = [((3, 20), "spring"), ((6, 21), "summer"), ((9, 22), "autumn"), ((12, 21), "winter")]
SEASONS_S = [((3, 20), "autumn"), ((6, 21), "winter"), ((9, 22), "spring"), ((12, 21), "summer")]


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    c, tz, today, cname = cal.resolve(params)
    y = today.year
    start = dt.datetime(y, 1, 1, tzinfo=tz)
    end = dt.datetime(y + 1, 1, 1, tzinfo=tz)
    counts: Dict[dt.date, int] = {}
    titles: Dict[dt.date, str] = {}
    for e in cal.events_between(c, start, end, tz):
        d0 = dt.date.fromisoformat(e["date"])
        counts[d0] = counts.get(d0, 0) + 1
        titles.setdefault(d0, e["title"])
    # "busy" is relative to this calendar: strictly above the median day AND in the top quarter,
    # so a calendar of daily recurrences (7–9 a day) still shows only its genuinely heavier days.
    vals = sorted(counts.values())
    if vals:
        med = vals[len(vals) // 2]
        q3 = vals[(3 * len(vals)) // 4]
        busy_at = max(q3, med + 1)
    else:
        busy_at = 10 ** 9
    months: List[Dict[str, Any]] = []
    for m in range(1, 13):
        n = cal_mod.monthrange(y, m)[1]
        days = []
        for d in range(1, n + 1):
            date = dt.date(y, m, d)
            days.append({"d": d, "past": date < today, "today": date == today, "weekend": date.weekday() >= 5,
                         "events": counts.get(date, 0), "busy": counts.get(date, 0) >= busy_at})
        months.append({"m": m, "name": cal_mod.month_abbr[m], "days": days})
    n_year = 366 if cal_mod.isleap(y) else 365
    ordinal = today.timetuple().tm_yday
    behind = sum(v for d, v in counts.items() if d < today)
    ahead = sum(v for d, v in counts.items() if d >= today)
    seasons = SEASONS_S if params.get("south") else SEASONS_N
    nxt = None
    for (mm, dd), name in seasons:
        sd = dt.date(y, mm, dd)
        if sd >= today:
            nxt = {"name": name, "days": (sd - today).days, "date_label": sd.strftime("%-d %b")}
            break
    if nxt is None:
        sd = dt.date(y + 1, *seasons[0][0])
        nxt = {"name": seasons[0][1], "days": (sd - today).days, "date_label": sd.strftime("%-d %b")}
    future = sorted(d for d in counts if d >= today)
    next_event = None
    if future:
        d0 = future[0]
        next_event = {"title": titles[d0], "days": (d0 - today).days, "date_label": d0.strftime("%a %-d %b")}
    weeks_left = (n_year - ordinal) // 7
    return {"year": y, "ordinal": ordinal, "n": n_year, "left": n_year - ordinal, "pct": ordinal / n_year,
            "weeks_left": weeks_left, "months": months, "behind": behind, "ahead": ahead, "season": nxt,
            "next_event": next_event, "busy_at": busy_at, "busy_days": sum(1 for v in counts.values() if v >= busy_at), "date_label": today.strftime("%A, %-d %B"), "calendar": cname,
            "demo": bool(params.get("demo")) or not params.get("ics_url")}
