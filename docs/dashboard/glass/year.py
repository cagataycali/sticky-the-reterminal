"""
▪ year — the whole year as dots, today ringed.

A page a wall calendar cannot print: 365 dots in twelve rows, the past filled,
the future hollow, today a heavy ring you can find from across the room. A day
with something on the calendar carries a point inside its ring; a busy one (above
the calendar's own median, top quarter) is black, so the shape of the year — the busy weeks, the quiet ones, the holiday — reads without
a single label. Weekends are smaller dots, which makes the week rhythm visible
as texture. Under it, the numbers: day N of 365, days and weeks left, percent,
events behind/ahead, the next season and the next thing on the calendar.
Data: glass.adapters.year (ICS via the calendar adapter, fixture fallback).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import year as yr
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _dot(g: Glass, cx: int, cy: int, day: Dict[str, Any], r: int) -> None:
    rr = r - 1 if day["weekend"] else r
    if day["today"]:
        g.circle(cx, cy, r + 4, fill=WHITE, outline=BLACK, width=3)
        return
    ev = day["events"]
    if day["past"]:
        g.circle(cx, cy, rr, fill=BLACK if day["busy"] else DARK)
    elif day["busy"]:
        g.circle(cx, cy, rr, fill=BLACK)
    else:
        # the future stays hollow; a mark on the calendar is a point inside the ring
        g.circle(cx, cy, rr, outline=DARK, width=1)
        if ev:
            g.circle(cx, cy, 1, fill=BLACK)


def _grid_rows(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, h: int) -> None:
    """12 rows (months) × 31 columns."""
    lab_w = 40
    cw = (w - lab_w) / 31
    rh = h / 12
    r = 4
    for i, m in enumerate(d["months"]):
        cy = round(y + i * rh + rh / 2)
        has_today = any(dd["today"] for dd in m["days"])
        g.text((x, cy), m["name"].upper(), mono(11, 700 if has_today else 500), BLACK if has_today else DARK, anchor="lm")
        for dd in m["days"]:
            cx = round(x + lab_w + (dd["d"] - 1) * cw + cw / 2)
            _dot(g, cx, cy, dd, r)
    # column ticks 1 · 10 · 20 · 31
    for dnum in (1, 10, 20, 31):
        cx = round(x + lab_w + (dnum - 1) * cw + cw / 2)
        g.text((cx, y - 4), str(dnum), mono(11, 500), DARK, anchor="md")


def _grid_cols(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, h: int) -> None:
    """12 columns (months) × 31 rows — portrait."""
    lab_h = 18
    cw = w / 12
    rh = (h - lab_h) / 31
    r = 4
    for i, m in enumerate(d["months"]):
        cx = round(x + i * cw + cw / 2)
        has_today = any(dd["today"] for dd in m["days"])
        g.text((cx, y), m["name"][0], mono(11, 700 if has_today else 500), BLACK if has_today else DARK, anchor="ma")
        for dd in m["days"]:
            cy = round(y + lab_h + (dd["d"] - 1) * rh + rh / 2)
            _dot(g, cx, cy, dd, r)


def _facts(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, cols: int) -> None:
    items = [
        (f"{d['ordinal']}", f"of {d['n']} days"),
        (f"{d['left']}", f"left · {d['weeks_left']} wk"),
        (f"{d['pct'] * 100:.0f} %", "of the year"),
        (f"{d['ahead']}", f"ahead · {d['busy_days']} busy"),
    ]
    if d["season"]:
        items.append((f"{d['season']['days']} d", f"to {d['season']['name']} · {d['season']['date_label']}"))
    if d["next_event"]:
        ne = d["next_event"]
        when = "today" if ne["days"] == 0 else ("tomorrow" if ne["days"] == 1 else f"in {ne['days']} d")
        items.append((when, g.fit_text(ne["title"], w // cols - 8, sans(11, 500))))
    cw = w / cols
    for i, (big, small) in enumerate(items[: cols * 2]):
        cx = round(x + (i % cols) * cw)
        cy = y + (i // cols) * 46
        g.text((cx, cy), big, mono(20, 700), BLACK)
        g.text((cx, cy + 26), g.fit_text(small, int(cw) - 10, sans(11, 500)), sans(11, 500), DARK)


@component(
    "year", "Year",
    "365 dots in twelve rows: past filled, future hollow, today a heavy ring; busy days black (relative to the calendar's own median), "
    "weekends smaller so the week rhythm reads as texture. Below: day N, days/weeks left, %, events ahead/behind, "
    "next season, next thing on the calendar.",
    params={"ics_url": "calendar", "date": "YYYY-MM-DD pin", "south": "1 for southern-hemisphere seasons"},
    fetch=yr.fetch,
    native_hint="type:'year' {ordinal, bits:'46 B past/event mask'} — the ESP32 could draw 365 dots itself",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        g.text((MARGIN, MARGIN - 6), str(d["year"]), sans(24, 700), BLACK)
        g.text((MARGIN + 72, MARGIN + 4), d["date_label"], sans(13, 500), DARK)
        g.text((g.w - MARGIN, MARGIN - 2), ("demo calendar" if d["demo"] else d["calendar"]), mono(12, 500), DARK, anchor="ra")
        top = MARGIN + 44
        _grid_rows(g, d, MARGIN, top, g.w - 2 * MARGIN, 300)
        y = top + 300 + 14
        g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
        _facts(g, d, MARGIN, y + 12, g.w - 2 * MARGIN, 6)
    else:
        g = Glass(PORTRAIT)
        g.text((MARGIN, MARGIN - 6), str(d["year"]), sans(24, 700), BLACK)
        g.text((g.w - MARGIN, MARGIN + 2), d["date_label"], sans(13, 500), DARK, anchor="ra")
        top = MARGIN + 40
        _grid_cols(g, d, MARGIN, top, g.w - 2 * MARGIN, 600)
        y = top + 600 + 10
        g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
        _facts(g, d, MARGIN, y + 10, g.w - 2 * MARGIN, 3)
    return g.snap()
