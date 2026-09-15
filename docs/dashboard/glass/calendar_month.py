"""
🗓 calendar_month — the month at a glance + what's next.

Left/top: a 7-column grid (Monday first), day numbers with event dots (≤4, then
"+n"), today as an inverted disc, other-month days in DARK and the lighter face, all-day events as a
thin bar under the number. Right/bottom: "Next" — the upcoming events from today
forward (date · time · title), dense mono, so the card answers both "what does
the month look like" and "what's coming" without a tap.

Data: glass.adapters.calendar.fetch_month (ICS URL or demo fixture).
"""
from __future__ import annotations

import calendar as pycal
import datetime as dt
from typing import Any, Dict, List

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import calendar as cal
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans

WEEKDAYS = ["M", "T", "W", "T", "F", "S", "S"]


def _grid(g: Glass, d: Dict[str, Any], box, num_px: int = 18) -> None:
    x0, y0, x1, y1 = box
    year, month = d["year"], d["month"]
    today = dt.date.fromisoformat(d["today"])
    weeks = pycal.Calendar(firstweekday=0).monthdatescalendar(year, month)
    cols = 7
    cw = (x1 - x0) / cols
    head_h = 22
    rh = (y1 - y0 - head_h) / len(weeks)
    wf = sans(13, 600)
    for c, wd in enumerate(WEEKDAYS):
        g.text((round(x0 + c * cw + cw / 2), y0), wd, wf, DARK, anchor="ma")
    g.hairline(x0, y0 + head_h - 4, x1, DARK)
    nf, of = sans(num_px, 600), sans(num_px, 400)
    for r, week in enumerate(weeks):
        cy0 = round(y0 + head_h + r * rh)
        if r:
            g.hairline(x0, cy0, x1, LIGHT)
        for c, day in enumerate(week):
            cx0 = round(x0 + c * cw)
            in_month = day.month == month
            evs: List[Dict[str, Any]] = d["by_day"].get(day.isoformat(), [])
            nx, ny = cx0 + 6, cy0 + 5
            if day == today:
                rad = num_px // 2 + 5
                g.circle(nx + rad - 2, ny + rad - 2, rad, fill=BLACK)
                g.text((nx + rad - 2, ny + rad - 2), str(day.day), nf, WHITE, anchor="mm")
            else:
                # other-month days: DARK in the lighter face (LIGHT text on white reads as nothing on e-ink)
                ink = DARK if (not in_month or c >= 5) else BLACK
                g.text((nx, ny), str(day.day), nf if in_month else of, ink)
            if not evs:
                continue
            ink = BLACK if in_month else DARK
            allday = [e for e in evs if e["all_day"]]
            timed = [e for e in evs if not e["all_day"]]
            by = cy0 + round(rh) - 12
            if allday:
                g.rect((cx0 + 6, by - 8, cx0 + round(cw) - 6, by - 6), fill=DARK if in_month else LIGHT)
            dx = cx0 + 8
            cap = 4 if cw >= 90 else 3
            shown = min(len(timed), cap)
            for i in range(shown):
                past = day < today or (day == today and timed[i]["dt_end"] < dt.datetime.now(timed[i]["dt_end"].tzinfo))
                g.circle(dx + i * 11, by, 3, fill=ink if not past else None, outline=ink if past else None)
            if len(timed) > cap:
                g.text((dx + cap * 11 - 1, by), f"+{len(timed) - cap}", mono(11, 500), DARK, anchor="lm")


def _upcoming(d: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    today = dt.date.fromisoformat(d["today"])
    now = dt.datetime.now(dt.timezone.utc)
    out, seen = [], set()
    for day in sorted(d["by_day"]):
        if dt.date.fromisoformat(day) < today:
            continue
        for e in d["by_day"][day]:
            key = (e["title"], e["dt_start"])
            if key in seen:
                continue
            if day == d["today"] and not e["all_day"] and e["dt_end"] < now:
                continue
            seen.add(key)
            out.append({**e, "day": dt.date.fromisoformat(day)})
            if len(out) >= limit:
                return out
    return out


def _next(g: Glass, d: Dict[str, Any], box, rows: int) -> None:
    x0, y0, x1, y1 = box
    today = dt.date.fromisoformat(d["today"])
    g.label((x0, y0), "next", 13, DARK)
    g.hairline(x0, y0 + 20, x1, DARK)
    y = y0 + 30
    rh = max(24, (y1 - y) // max(rows, 1))
    df, tf, ef = mono(13, 500), mono(14, 700), sans(16, 500)
    last_day = None
    for e in _upcoming(d, rows):
        day = e["day"]
        if day != last_day:
            lab = "Today" if day == today else ("Tomorrow" if day == today + dt.timedelta(days=1) else day.strftime("%a %-d"))
            g.text((x0, y + 3), lab.upper(), df, DARK)
            last_day = day
        tx = x0 + 68
        stamp = "all day" if e["all_day"] else e["start"]
        g.text((tx, y + 2), stamp, tf, BLACK if not e["all_day"] else DARK)
        tx += 62
        g.text((tx, y), g.fit_text(e["title"], x1 - tx, ef), ef, BLACK)
        y += rh
        if y + rh > y1 + 4:
            break
    if last_day is None:
        g.text((x0, y), "Nothing scheduled ahead.", sans(16, 400), DARK)


def _header(g: Glass, d: Dict[str, Any], y: int) -> int:
    big = sans(34, 700)
    first = dt.date(d["year"], d["month"], 1)
    g.text((MARGIN, y), first.strftime("%B"), big, BLACK)
    g.text((MARGIN + g.text_size(first.strftime("%B"), big)[0] + 12, y + 10), str(d["year"]), sans(22, 400), DARK)
    n = sum(1 for k, v in d["by_day"].items() if dt.date.fromisoformat(k).month == d["month"] for _ in v)
    g.text((g.w - MARGIN, y + 6), f"{n} events", mono(16, 500), DARK, anchor="ra")
    g.label((g.w - MARGIN, y + 28), d["calendar"], 13, DARK, anchor="ra")
    return y + 46


@component(
    "calendar_month", "Month",
    "Month grid (Mon-first) with event dots, today inverted, all-day bars, plus a dense "
    "'Next' list of what's coming from today onward.",
    params={"ics_url": "ICS feed URL or path (default: demo fixture)", "date": "YYYY-MM-DD in the month (default today)",
            "tz": "IANA zone (default: machine's)", "demo": "1 → fixture"},
    fetch=cal.fetch_month,
    native_hint="type:'month' {y,m,today,dots:[42 x u8 count|0x80 allday], next:[{d,t,title}] ≤8} ≈ 300 B; one full refresh per day",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        y = _header(g, d, MARGIN)
        gw = 440
        _grid(g, d, (MARGIN, y + 6, MARGIN + gw, g.h - MARGIN))
        g.vline(MARGIN + gw + 16, y + 6, g.h - MARGIN, LIGHT)
        _next(g, d, (MARGIN + gw + 32, y + 6, g.w - MARGIN, g.h - MARGIN), rows=10)
    else:
        g = Glass(PORTRAIT)
        y = _header(g, d, MARGIN)
        gh = 372
        _grid(g, d, (MARGIN, y + 6, g.w - MARGIN, y + 6 + gh), num_px=17)
        g.hairline(MARGIN, y + gh + 20, g.w - MARGIN, LIGHT)
        _next(g, d, (MARGIN, y + gh + 34, g.w - MARGIN, g.h - MARGIN), rows=10)
    return g.snap()
