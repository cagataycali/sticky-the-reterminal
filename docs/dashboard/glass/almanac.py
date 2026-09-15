"""
📜 almanac — on this day.

The almanac page a good newspaper still prints: the date set large with the
day-of-year beneath it, then a timeline of what happened on this date — years
in a mono column, one line of history each, spread across the centuries so the
eye travels from a medieval pope to last decade. Beside it the people: born
and died, far past first, and what the day is observed as around the world.
No pictures, no ornament — the density is the beauty. Data: glass.adapters.almanac
(Wikipedia's curated on-this-day feed, no key; fixture = 7 September).
"""
from __future__ import annotations

from typing import Any, Dict, List

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import almanac as al
from .canvas import BLACK, DARK, LIGHT, MARGIN, Glass, mono, sans


def _timeline(g: Glass, items: List[Dict[str, Any]], x: int, y: int, w: int, max_h: int, lines: int = 2) -> int:
    yc = 52
    f = sans(13, 500)
    yy = y
    for e in items:
        if yy + 18 > y + max_h:
            break
        g.text((x + yc - 6, yy), str(e["year"]), mono(15, 700), BLACK, anchor="ra")
        wrapped = g.wrap(e["text"], w - yc, f, lines)
        for i, ln in enumerate(wrapped):
            g.text((x + yc, yy + 1 + i * 17), ln, f, BLACK if i == 0 else DARK)
        yy += 17 * len(wrapped) + 10
    return yy


def _people(g: Glass, d: Dict[str, Any], x: int, y: int, w: int) -> int:
    yy = y
    for key, label in (("births", "Born"), ("deaths", "Died")):
        g.text((x, yy), label, sans(11, 700), DARK)
        yy += 16
        for e in d[key]:
            g.text((x + 40, yy), str(e["year"]), mono(12, 700), BLACK, anchor="ra")
            g.text((x + 48, yy + 1), g.fit_text(e["text"].split(" (")[0], w - 48, sans(12, 500)), sans(12, 500), BLACK)
            yy += 17
        yy += 8
    if d["holidays"]:
        g.text((x, yy), "Observed", sans(11, 700), DARK)
        yy += 16
        for h in d["holidays"]:
            g.text((x, yy), g.fit_text(h, w, sans(12, 500)), sans(12, 500), BLACK)
            yy += 17
    return yy


@component(
    "almanac", "Almanac",
    "On this day, as a newspaper almanac page: the date large with day-of-year, a timeline of events with years in a "
    "mono column spread across the centuries, born/died with the far past first, and what the day is observed as "
    "around the world. Density is the ornament.",
    params={"date": "YYYY-MM-DD (default today)", "n_events": "timeline rows (default 8)", "demo": "1 = fixture"},
    fetch=al.fetch,
    native_hint="type:'list' with year prefixes ≈ 600 B of text — no geometry, the ESP32 can set this itself",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        g.text((MARGIN, MARGIN - 8), d["date_label"], sans(30, 700), BLACK)
        g.text((MARGIN, MARGIN + 30), f"{d['weekday']} · day {d['doy']} of the year" + ("  · demo" if d["demo"] else ""), sans(12, 500), DARK)
        g.text((g.w - MARGIN, MARGIN - 2), "On this day", sans(13, 700), DARK, anchor="ra")
        y0 = MARGIN + 60
        g.hairline(MARGIN, y0 - 8, g.w - MARGIN, LIGHT)
        lw = 470
        _timeline(g, d["events"], MARGIN, y0, lw, g.h - MARGIN - y0)
        sx = MARGIN + lw + 28
        g.d.line([(sx - 14, y0 - 2), (sx - 14, g.h - MARGIN)], fill=LIGHT, width=1)
        _people(g, d, sx, y0, g.w - MARGIN - sx)
    else:
        g = Glass(PORTRAIT)
        g.text((MARGIN, MARGIN - 8), d["date_label"], sans(30, 700), BLACK)
        g.text((MARGIN, MARGIN + 30), f"{d['weekday']} · day {d['doy']}" + ("  · demo" if d["demo"] else ""), sans(12, 500), DARK)
        g.text((g.w - MARGIN, MARGIN - 2), "On this day", sans(13, 700), DARK, anchor="ra")
        y0 = MARGIN + 60
        g.hairline(MARGIN, y0 - 8, g.w - MARGIN, LIGHT)
        yy = _timeline(g, d["events"], MARGIN, y0, g.w - 2 * MARGIN, 400, 3)
        g.hairline(MARGIN, yy + 4, g.w - MARGIN, LIGHT)
        _people(g, d, MARGIN, yy + 16, g.w - 2 * MARGIN)
    return g.snap()
