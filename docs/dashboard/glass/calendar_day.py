"""
📅 calendar_day — today's agenda as a timeline.

Header: weekday + date, calendar name, event count. All-day events as chips.
Then a time column with events as blocks (title, location, start–end),
overlapping events share the width, a "now" hairline with the clock if the
day is today. Hours 06–22 by default, stretched when events fall outside.
Landscape splits the day into two side-by-side columns (morning | afternoon)
so a 30-minute block is still 20+ px tall; portrait is one tall column.

Data: glass.adapters.calendar (ICS URL or demo fixture).
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Tuple

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import calendar as cal
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _minutes(e: Dict[str, Any], key: str) -> int:
    t: dt.datetime = e[key]
    return t.hour * 60 + t.minute


def _lanes(events: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], int, int]]:
    """Greedy interval colouring → (event, lane, lanes_in_group)."""
    evs = sorted(events, key=lambda e: (_minutes(e, "dt_start"), -_minutes(e, "dt_end")))
    out, group, group_end, lane_ends = [], [], -1, []
    for e in evs:
        s, en = _minutes(e, "dt_start"), _minutes(e, "dt_end")
        if s >= group_end and group:
            n = len(lane_ends)
            out += [(ge, gl, n) for ge, gl in group]
            group, lane_ends = [], []
        lane = next((i for i, le in enumerate(lane_ends) if le <= s), None)
        if lane is None:
            lane_ends.append(en)
            lane = len(lane_ends) - 1
        else:
            lane_ends[lane] = en
        group.append((e, lane))
        group_end = max(group_end, en)
    n = len(lane_ends)
    out += [(ge, gl, n) for ge, gl in group]
    return out


def _timeline(g: Glass, d: Dict[str, Any], box, h0: int, h1: int, title_px: int = 18) -> None:
    x0, y0, x1, y1 = box
    gutter = 46
    px_per_min = (y1 - y0) / ((h1 - h0) * 60)

    def ymin(m: int) -> int:
        return round(y0 + (m - h0 * 60) * px_per_min)

    hf = mono(15, 500)
    for h in range(h0, h1 + 1):
        y = ymin(h * 60)
        g.text((x0 + gutter - 8, y), f"{h:02d}", hf, DARK, anchor="rm")
        g.hairline(x0 + gutter, y, x1, LIGHT)
        if h < h1:
            g.dots(x0 + gutter, ymin(h * 60 + 30), x1, 6, LIGHT)
    lo, hi = h0 * 60, h1 * 60
    inside = [e for e in d["events"] if _minutes(e, "dt_end") > lo and _minutes(e, "dt_start") < hi]
    bx0, bx1 = x0 + gutter + 4, x1
    tf, lf, sf = sans(title_px, 600), sans(15, 400), mono(13, 500)
    for e, lane, n in _lanes(inside):
        s, en = max(_minutes(e, "dt_start"), lo), min(_minutes(e, "dt_end"), hi)
        top, bot = ymin(s) + 1, ymin(en) - 2
        if bot - top < 16:
            bot = top + 16
        lw = (bx1 - bx0 - 4 * (n - 1)) / n
        lx0 = round(bx0 + lane * (lw + 4))
        lx1 = round(lx0 + lw)
        past = d.get("now_minutes") is not None and _minutes(e, "dt_end") <= d["now_minutes"]
        fill = WHITE if not past else WHITE
        g.rect((lx0, top, lx1, bot), fill=fill, outline=DARK if past else BLACK, width=1)
        g.rect((lx0, top, lx0 + 4, bot), fill=LIGHT if past else BLACK)
        ink = DARK if past else BLACK
        h = bot - top
        tx = lx0 + 12
        avail = lx1 - tx - 6
        if h >= 40:
            g.text((tx, top + 4), g.fit_text(e["title"], avail, tf), tf, ink)
            line2 = f"{e['start']}–{e['end']}" + (f"  {e['location']}" if e["location"] else "")
            g.text((tx, top + 6 + title_px + 3), g.fit_text(line2, avail, lf), lf, DARK)
        else:
            f = tf if h >= 22 else sans(14, 600)
            stamp = e["start"]
            sw = g.text_size(stamp, sf)[0]
            g.text((tx, top + (h - g.text_size("Xg", f)[1]) // 2 - 1), g.fit_text(e["title"], avail - sw - 8, f), f, ink)
            g.text((lx1 - 6, top + h // 2), stamp, sf, DARK, anchor="rm")
    nm = d.get("now_minutes")
    if nm is not None and lo <= nm <= hi:
        y = ymin(nm)
        g.d.line((x0 + gutter - 2, y, x1, y), fill=BLACK, width=2)
        g.circle(x0 + gutter - 2, y, 4, fill=BLACK)
        g.rect((x0 - 2, y - 12, x0 + gutter - 6, y + 12), fill=WHITE)   # clears a colliding hour label
        g.rect((x0, y - 10, x0 + gutter - 8, y + 10), fill=BLACK, radius=3)
        g.text((x0 + (gutter - 8) // 2, y), d["now"], mono(13, 700), WHITE, anchor="mm")


def _header(g: Glass, d: Dict[str, Any], y: int, wide: bool) -> int:
    day = dt.date.fromisoformat(d["date"])
    big = sans(40, 700)
    n = len(d["events"]) + len(d["all_day"])
    right = f"{n} event{'s' if n != 1 else ''}"
    g.text((MARGIN, y), day.strftime("%A"), big, BLACK)
    if wide:
        g.text((MARGIN + g.text_size(day.strftime("%A"), big)[0] + 14, y + 12), day.strftime("%-d %B"), sans(24, 400), DARK)
        g.text((g.w - MARGIN, y + 6), right, mono(18, 500), DARK, anchor="ra")
        g.label((g.w - MARGIN, y + 30), d["calendar"], 14, DARK, anchor="ra")
        return y + 52
    # portrait: the 480 px row cannot hold weekday + date + count — two rows
    g.text((MARGIN, y + 48), day.strftime("%-d %B"), sans(22, 400), DARK)
    g.text((g.w - MARGIN, y + 50), right, mono(16, 500), DARK, anchor="ra")
    g.label((g.w - MARGIN, y + 8), d["calendar"], 13, DARK, anchor="ra")
    return y + 78


def _allday(g: Glass, d: Dict[str, Any], y: int) -> int:
    if not d["all_day"]:
        return y
    x = MARGIN
    f = sans(16, 600)
    g.label((x, y + 6), "all day", 13, DARK)
    x += 78
    for e in d["all_day"]:
        t = g.fit_text(e["title"], g.w - MARGIN - x - 20, f)
        w = g.text_size(t, f)[0] + 24
        if x + w > g.w - MARGIN:
            break
        g.rect((x, y, x + w, y + 28), fill=LIGHT, radius=6)
        g.text((x + 12, y + 5), t, f, BLACK)
        x += w + 8
    return y + 38


def _range(d: Dict[str, Any]) -> Tuple[int, int]:
    h0, h1 = 6, 22
    for e in d["events"]:
        h0 = min(h0, e["dt_start"].hour)
        h1 = max(h1, min(24, e["dt_end"].hour + (1 if e["dt_end"].minute else 0)))
    return h0, h1


@component(
    "calendar_day", "Agenda", "Today's timeline: all-day chips, hour column with event blocks "
    "(title, time, location), overlaps side by side, a 'now' line.",
    params={"ics_url": "ICS feed URL or path (default: demo fixture)", "date": "YYYY-MM-DD (default today)",
            "tz": "IANA zone (default: machine's)", "demo": "1 → fixture"},
    fetch=cal.fetch_day,
    native_hint="type:'agenda' {date, now, all_day:[..], events:[{s,e,title,loc}] ≤12} ≈ 800 B; partial refresh of the now-line every minute",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    h0, h1 = _range(d)
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        y = _header(g, d, MARGIN, True)
        y = _allday(g, d, y + 2)
        g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
        top, bottom = y + 14, g.h - MARGIN
        mid = (h0 + h1) // 2
        colw = (g.w - 2 * MARGIN - 24) // 2
        _timeline(g, d, (MARGIN, top, MARGIN + colw, bottom), h0, mid)
        g.vline(MARGIN + colw + 12, top, bottom, LIGHT)
        _timeline(g, d, (MARGIN + colw + 24, top, g.w - MARGIN, bottom), mid, h1)
    else:
        g = Glass(PORTRAIT)
        y = _header(g, d, MARGIN, False)
        y = _allday(g, d, y + 2)
        g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
        _timeline(g, d, (MARGIN, y + 16, g.w - MARGIN, g.h - MARGIN), h0, h1)
    return g.snap()
