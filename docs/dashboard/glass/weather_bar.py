"""
🌤 weather_bar — today at a glance.

Landscape 800×480: hero temperature + glyph on the left, condition / hi-lo /
precip / wind / sun column, a 12-hour temperature sparkline with hour ticks
and precip bars, and a 5-day row along the bottom.
Portrait 480×800: the same blocks stacked.

Data: glass.adapters.weather (Open-Meteo, no key). params: place, lat, lon,
units (f|c), demo.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import weather as wx
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans
from . import icons


def _deg(v: float) -> str:
    return f"{round(v)}°"


def _hero(g: Glass, d: Dict[str, Any], x: int, y: int, w: int) -> int:
    """Temperature + glyph + condition column. Returns the bottom y."""
    icon_r = 74
    icons.draw(g, d["glyph"], x + icon_r + 6, y + icon_r + 10, icon_r)
    tx = x + icon_r * 2 + 40
    digits = f"{round(d['temp'])}"
    big = mono(136 if len(digits) <= 2 else 104, 700)   # 3 digits (104°, -12°) must not eat the facts column
    bb = g.text((tx, y - 10 if len(digits) <= 2 else y + 14), digits, big, BLACK)
    g.text((bb[2] + 4, y + 8), "°" + d["units"].upper(), mono(44, 500), DARK)
    cond_y = y + 138
    g.text((tx + 4, cond_y), g.fit_text(d["condition"], w - (tx - x) - 8, sans(34, 600)), sans(34, 600), BLACK)
    feels = d.get("feels")
    sub = f"feels {_deg(feels)}" if feels is not None else ""
    g.text((tx + 4, cond_y + 44), sub, sans(22, 400), DARK)
    return cond_y + 44 + 30


def _facts(g: Glass, d: Dict[str, Any], x: int, y: int, col_w: int, cols: int = 2) -> int:
    facts = [("high", _deg(d["hi"])), ("low", _deg(d["lo"])), ("precip", f"{d['precip']}%"),
             ("wind", f"{round(d['wind'])} {d['wind_unit']}"), ("humidity", f"{d['humidity']}%"),
             ("sunset", d["sunset"])]
    row_h = 74
    for i, (k, v) in enumerate(facts):
        cx = x + (i % cols) * col_w
        cy = y + (i // cols) * row_h
        g.label((cx, cy), k, 16, DARK)
        g.text((cx - 2, cy + 20), v, mono(38, 600), BLACK)
    return y + ((len(facts) + cols - 1) // cols) * row_h


def _hours(g: Glass, d: Dict[str, Any], box) -> None:
    x0, y0, x1, y1 = box
    hours = d["hours"]
    temps = [h["temp"] for h in hours]
    g.label((x0, y0), "next 12 hours", 16, DARK)
    g.text((x1, y0), f"{_deg(min(temps))} – {_deg(max(temps))}", mono(18, 500), DARK, anchor="ra")
    # precip bars under the sparkline, then the curve
    chart = (x0, y0 + 30, x1, y1 - 26)
    n = len(hours)
    step = (x1 - x0) / max(1, n - 1)
    g.bars((x0, chart[1] + 10, x1, chart[3]), [h["precip"] for h in hours], fill=LIGHT, gap=6, vmax=100)
    g.sparkline(chart, temps, fill=BLACK, width=4, area=None, baseline=True)
    f = mono(15, 500)
    for i, h in enumerate(hours):
        if i % 3 == 0 or i == n - 1:
            hh = int(h["t"][:2])
            lab = f"{hh:02d}"
            anchor = "la" if i == 0 else ("ra" if i == n - 1 else "ma")
            g.text((x0 + round(i * step), y1 - 18), lab, f, DARK, anchor=anchor)


def _days(g: Glass, d: Dict[str, Any], box, count: int = 5) -> None:
    x0, y0, x1, y1 = box
    days = d["days"][1:1 + count]
    col = (x1 - x0) / max(1, len(days))
    for i, day in enumerate(days):
        cx = round(x0 + col * i + col / 2)
        if i:
            g.vline(round(x0 + col * i), y0 + 6, y1 - 6, LIGHT)
        g.label((cx, y0), day["dow"], 16, DARK, anchor="ma")
        _, glyph = wx.describe(day["code"])
        icons.draw(g, glyph, cx, y0 + 48, 21)
        g.text((cx, y0 + 76), _deg(day["hi"]), mono(26, 700), BLACK, anchor="ma")
        g.text((cx, y0 + 104), _deg(day["lo"]), mono(20, 400), DARK, anchor="ma")
        if day["precip"] and day["precip"] >= 20:
            g.text((cx, y0 + 126), f"{day['precip']}%", mono(13, 500), DARK, anchor="ma")


def _header(g: Glass, d: Dict[str, Any], y: int) -> None:
    admin = d.get("admin") or ""
    place = d["place"] + (f", {admin}" if admin and admin != d["place"] and len(admin) <= 14 else "")
    g.text((MARGIN, y), g.fit_text(place, g.w // 2, sans(24, 600)), sans(24, 600), BLACK)
    try:
        t = dt.datetime.fromisoformat(d["time"])
        stamp = t.strftime("%a %-d %b · %H:%M")
    except ValueError:
        stamp = d["time"]
    g.text((g.w - MARGIN, y + 3), stamp, mono(18, 500), DARK, anchor="ra")


@component(
    "weather_bar", "Weather", "Today at a glance: hero temperature, condition glyph, hi/lo, "
    "precip, 12-hour temperature curve, five-day row.",
    params={"place": "city name (default: this machine's timezone city)", "lat": "latitude (with lon; skips geocoding)",
            "lon": "longitude", "units": "f | c (default f)", "demo": "1 → fixture, no network"},
    fetch=wx.fetch,
    native_hint="type:'weather' {temp,unit,code,hi,lo,precip,hours:[12×{t,temp,p}],days:[5×{dow,code,hi,lo}]} ≈ 600 B",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        _header(g, d, MARGIN)
        g.hairline(MARGIN, MARGIN + 36, g.w - MARGIN, LIGHT)
        _hero(g, d, MARGIN, 78, 440)
        _facts(g, d, 500, 84, 150, cols=2)
        g.hairline(MARGIN, 298, g.w - MARGIN, LIGHT)
        _hours(g, d, (MARGIN, 310, 380, 456))
        g.vline(404, 310, 456, LIGHT)
        _days(g, d, (428, 310, g.w - MARGIN, 456))
    else:
        g = Glass(PORTRAIT)
        _header(g, d, MARGIN)
        g.hairline(MARGIN, MARGIN + 36, g.w - MARGIN, LIGHT)
        _hero(g, d, MARGIN, 84, g.w - 2 * MARGIN)
        g.hairline(MARGIN, 318, g.w - MARGIN, LIGHT)
        _facts(g, d, MARGIN + 4, 334, 144, cols=3)
        g.hairline(MARGIN, 486, g.w - MARGIN, LIGHT)
        _hours(g, d, (MARGIN, 500, g.w - MARGIN, 626))
        g.hairline(MARGIN, 640, g.w - MARGIN, LIGHT)
        _days(g, d, (MARGIN, 654, g.w - MARGIN, 794))
    return g.snap()
