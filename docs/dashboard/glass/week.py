"""
📆 week — seven days at a glance: weather and load.

Landscape: seven columns. Day name and date (today's numeral ringed), weather
glyph, then a temperature BAND chart — each day a vertical bar from its low to
its high on the week's shared scale, hi above, lo below (the bars tell you the
shape of the week; the numbers tell you the values). Rain chance where it is
≥ 20 %. Under a hairline, the day's load: one dot per event (filled) or all-day
(outlined) and the first titles.
Portrait: seven rows — date, glyph, horizontal band, titles.

Data: glass.adapters.week (weather days=7 + calendar events; both fixtures in demo).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component, icons
from .adapters import week as wk
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _header(g: Glass, d: Dict[str, Any], y: int) -> int:
    g.text((MARGIN, y), "Week", sans(34, 700), BLACK)
    x = MARGIN + g.text_size("Week", sans(34, 700))[0] + 14
    g.text((x, y + 12), g.fit_text(f"{d['range']} · {d['place']}", g.w // 2 - x + 60, sans(16, 500)), sans(16, 500), DARK)
    g.text((g.w - MARGIN, y + 4), d["now"], mono(18, 500), BLACK, anchor="ra")
    ev = d["total_events"]
    g.text((g.w - MARGIN, y + 28), f"{ev} event{'s' if ev != 1 else ''} · {d['week_lo']:.0f}–{d['week_hi']:.0f}°",
           sans(15, 600), DARK, anchor="ra")
    return y + 50


def _dots(g: Glass, x: int, y: int, day: Dict[str, Any], max_n: int = 6) -> int:
    n_t, n_a = day["n_events"], day["n_all_day"]
    k = 0
    for _ in range(min(n_t, max_n)):
        g.circle(x + k * 12 + 4, y + 4, 4, fill=BLACK); k += 1
    for _ in range(min(n_a, max_n - k)):
        g.circle(x + k * 12 + 4, y + 4, 4, outline=BLACK, width=1); k += 1
    if n_t + n_a > max_n:
        g.text((x + k * 12 + 2, y - 3), f"+{n_t + n_a - max_n}", mono(11, 500), DARK)
    return x + k * 12


def render_landscape(d: Dict[str, Any]) -> Image.Image:
    g = Glass(LANDSCAPE)
    y = _header(g, d, MARGIN)
    days = d["days"]
    n = len(days)
    cw = (g.w - 2 * MARGIN) // n
    lo_w, hi_w = d["week_lo"], d["week_hi"]
    span = max(1.0, hi_w - lo_w)
    band_top, band_bot = y + 150, y + 262          # the band chart's pixel range
    for i, day in enumerate(days):
        x = MARGIN + i * cw
        cx = x + cw // 2
        if i:
            g.vline(x, y + 6, g.h - MARGIN, LIGHT)
        # day name + date
        today = i == 0
        g.text((cx, y), day["dow"].upper() if not today else "TODAY", sans(12, 700), BLACK if today else DARK, anchor="ma")
        df = mono(26, 700)
        g.text((cx, y + 24), str(day["dom"]), df, BLACK, anchor="ma")
        if today:
            g.circle(cx, y + 40, 21, outline=BLACK, width=2)
        # glyph
        icons.draw(g, day["glyph"], cx, y + 96, 24)
        # band: lo..hi on the shared scale
        yh = round(band_bot - (day["hi"] - lo_w) / span * (band_bot - band_top))
        yl = round(band_bot - (day["lo"] - lo_w) / span * (band_bot - band_top))
        g.rect((cx - 5, yh, cx + 5, max(yl, yh + 4)), fill=BLACK if today else DARK, radius=5)
        g.text((cx, yh - 22), f"{round(day['hi'])}°", mono(18, 700), BLACK, anchor="ma")
        g.text((cx, yl + 8), f"{round(day['lo'])}°", mono(15, 500), DARK, anchor="ma")
        if day["precip"] >= 20:
            g.text((cx, band_bot + 30), f"{round(day['precip'])} % rain", sans(13, 600), BLACK, anchor="ma")
        # load
        ly = band_bot + 54
        g.hairline(x + 8, ly, x + cw - 8, LIGHT)
        if day["n_events"] or day["n_all_day"]:
            _dots(g, x + 10, ly + 10, day)
            tf = sans(13, 500)
            ty = ly + 24
            for t in day["titles"][:3]:
                g.text((x + 10, ty), g.fit_text(t, cw - 20, tf), tf, BLACK if ty == ly + 24 else DARK)
                ty += 17
        else:
            g.text((x + 10, ly + 12), "free", sans(13, 500), DARK)
    return g.snap()


def render_portrait(d: Dict[str, Any]) -> Image.Image:
    g = Glass(PORTRAIT)
    y = _header(g, d, MARGIN)
    days = d["days"]
    lo_w, hi_w = d["week_lo"], d["week_hi"]
    span = max(1.0, hi_w - lo_w)
    rh = (g.h - MARGIN - y - 6) // len(days)
    bx0, bx1 = 300, g.w - MARGIN - 40          # horizontal band lane
    for i, day in enumerate(days):
        ry = y + 6 + i * rh
        if i:
            g.hairline(MARGIN, ry - 2, g.w - MARGIN, LIGHT)
        today = i == 0
        g.text((MARGIN + 30, ry + 8), str(day["dom"]), mono(26, 700), BLACK, anchor="ma")
        g.text((MARGIN + 30, ry + 52), ("TODAY" if today else day["dow"].upper()), sans(11, 700), BLACK if today else DARK, anchor="ma")
        if today:
            g.circle(MARGIN + 30, ry + 24, 21, outline=BLACK, width=2)
        icons.draw(g, day["glyph"], MARGIN + 96, ry + rh // 2 - 4, 22)
        # load column
        tx = MARGIN + 132
        if day["n_events"] or day["n_all_day"]:
            _dots(g, tx, ry + 8, day, max_n=5)
            tf = sans(13, 500)
            ty = ry + 22
            for t in day["titles"][:2]:
                g.text((tx, ty), g.fit_text(t, bx0 - tx - 12, tf), tf, BLACK if ty == ry + 22 else DARK)
                ty += 17
        else:
            g.text((tx, ry + 10), "free", sans(13, 500), DARK)
        # horizontal band
        by = ry + rh // 2 - 4
        xl = round(bx0 + (day["lo"] - lo_w) / span * (bx1 - bx0))
        xh = round(bx0 + (day["hi"] - lo_w) / span * (bx1 - bx0))
        g.rect((xl, by - 5, max(xh, xl + 4), by + 5), fill=BLACK if today else DARK, radius=5)
        g.text((xl - 6, by), f"{round(day['lo'])}", mono(14, 500), DARK, anchor="rm")
        g.text((xh + 6, by), f"{round(day['hi'])}°", mono(16, 700), BLACK, anchor="lm")
        if day["precip"] >= 20:
            g.text((g.w - MARGIN, ry + rh - 22), f"{round(day['precip'])} % rain", sans(12, 600), BLACK, anchor="ra")
    return g.snap()


@component(
    "week", "Week",
    "Seven days: weather glyph, a lo–hi temperature band chart on the week's scale, rain chance, "
    "and each day's calendar load (dots + first titles). Today ringed.",
    params={"place": "city", "units": "f|c", "ics_url": "calendar feed", "tz": "IANA zone", "demo": "1 → fixtures"},
    fetch=wk.fetch,
    native_hint="type:'week' {days:[{dom, dow, code:u8, hi:i8, lo:i8, pop:u8, n_ev:u8, n_all:u8, t1}]} ≈ 350 B; refresh hourly",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    return render_landscape(d) if orientation == "landscape" else render_portrait(d)
