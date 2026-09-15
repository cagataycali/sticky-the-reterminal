"""
▦ glass.adapters.agenda — the shape of the next seven days.

Uses calendar.resolve + events_between over [today, today+7). Output: for each day a
busy-minutes-per-hour vector (06–22), total busy minutes, the count, the first and last
event, the longest free window inside working hours; plus the week's totals, the busiest
day, and the freest working day. Freeness is the point — a week card that shows where
the time is, not just where the meetings are. Fixture via calendar's fixture (demo).
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List

from . import calendar as cal

H0, H1 = 6, 22          # the visible day
W0, W1 = 9 * 60, 18 * 60  # working hours for the free-window search


def _busy_vec(evs: List[Dict[str, Any]], day: dt.date, tz) -> List[int]:
    vec = [0] * (H1 - H0)
    for e in evs:
        if e["all_day"]:
            continue
        s = e["dt_start"].astimezone(tz)
        en = e["dt_end"].astimezone(tz)
        d0 = dt.datetime.combine(day, dt.time(H0), tzinfo=tz)
        for h in range(H0, H1):
            a = d0 + dt.timedelta(hours=h - H0)
            b = a + dt.timedelta(hours=1)
            ov = (min(en, b) - max(s, a)).total_seconds() / 60
            if ov > 0:
                vec[h - H0] = min(60, vec[h - H0] + int(ov))
    return vec


def _free(evs: List[Dict[str, Any]], day: dt.date, tz) -> Dict[str, Any]:
    spans = sorted((e["dt_start"].astimezone(tz).hour * 60 + e["dt_start"].astimezone(tz).minute,
                    e["dt_end"].astimezone(tz).hour * 60 + e["dt_end"].astimezone(tz).minute)
                   for e in evs if not e["all_day"])
    cur, best = W0, (0, W0, W0)
    for s, en in spans + [(W1, W1)]:
        s = min(max(s, W0), W1)
        if s - cur > best[0]:
            best = (s - cur, cur, s)
        cur = max(cur, en)
    n, a, b = best
    return {"min": n, "start": f"{a // 60:02d}:{a % 60:02d}", "end": f"{b // 60:02d}:{b % 60:02d}"} if n > 0 else {"min": 0, "start": "", "end": ""}


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    c, tz, day0, name = cal.resolve(params)
    days = []
    for i in range(7):
        day = day0 + dt.timedelta(days=i)
        start = dt.datetime.combine(day, dt.time.min, tzinfo=tz)
        evs = cal.events_between(c, start, start + dt.timedelta(days=1), tz)
        timed = [e for e in evs if not e["all_day"]]
        vec = _busy_vec(evs, day, tz)
        days.append({
            "date": day.isoformat(), "dow": day.strftime("%a"), "dom": day.day, "today": i == 0,
            "weekend": day.weekday() >= 5, "busy": vec, "busy_min": sum(vec), "n": len(timed),
            "all_day": [e["title"] for e in evs if e["all_day"]],
            "first": timed[0]["start"] if timed else "", "last": timed[-1]["end"] if timed else "",
            "titles": [f"{e['start']} {e['title']}" for e in timed[:4]],
            "free": _free(evs, day, tz),
        })
    work = [d for d in days if not d["weekend"]] or days
    busiest = max(days, key=lambda d: d["busy_min"])
    freest = max(work, key=lambda d: d["free"]["min"])
    return {"days": days, "h0": H0, "h1": H1, "calendar": name, "tz": str(tz),
            "week_min": sum(d["busy_min"] for d in days), "week_n": sum(d["n"] for d in days),
            "busiest": busiest["date"], "freest": freest["date"],
            "range_label": f"{day0.strftime('%-d %b')} – {(day0 + dt.timedelta(days=6)).strftime('%-d %b')}",
            "demo": bool(params.get("demo")) or not params.get("ics_url")}
