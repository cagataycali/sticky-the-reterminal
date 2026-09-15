"""
≈ tide — the water for the next eighteen hours.

One curve, drawn the way a tide table wants to be read: the sea is the filled
shape (light gray under a hairline), the sky is white, the past six hours sit
left of a thin now-line so you see where the water came from. Highs and lows
are labelled at their turning points with time and height; the hours run along
the floor every three hours. Beside the curve the numbers a swimmer or a sailor
wants first: the height now with an arrow for rising/falling, the next high and
the next low with a countdown, and today's range. Station and date in the header.
Data: glass.adapters.tide (NOAA CO-OPS, no key; fixture = The Battery NY).
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import tide as td
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _fmt(v: float, unit: str) -> str:
    return f"{v:.2f} {unit}" if unit == "m" else f"{v:.1f} {unit}"


def _curve(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, h: int) -> None:
    pts = d["points"]
    if len(pts) < 2:
        return
    vmin, vmax = d["vmin"], d["vmax"]
    pad = (vmax - vmin) * 0.12 or 0.1
    lo, hi = vmin - pad, vmax + pad
    top, floor = y + 26, y + h - 22           # room for labels above, hours below

    def X(t: float) -> int:
        return round(x + t / 24 * w)

    def Y(v: float) -> int:
        return round(floor - (v - lo) / (hi - lo) * (floor - top))

    poly: List[Tuple[int, int]] = [(X(p["t"]), Y(p["v"])) for p in pts]
    # the sea: filled to the floor
    g.d.polygon([(poly[0][0], floor)] + poly + [(poly[-1][0], floor)], fill=LIGHT)
    g.d.line(poly, fill=BLACK, width=2)
    g.hairline(x, floor, x + w, DARK)
    # hours along the floor, every 3 h from the window start's next whole multiple of 3
    sh = d["start_hour"]
    first = (3 - (sh % 3)) % 3
    t = first
    while t <= 24:
        hh = int((sh + t) % 24)
        xx = X(t)
        g.d.line([(xx, floor), (xx, floor + 4)], fill=DARK, width=1)
        g.text((xx, floor + 7), f"{hh:02d}", mono(11, 500), DARK, anchor="ma")
        t += 3
    # turning points
    for e in d["extremes"]:
        xx, yy = X(e["t"]), Y(e["v"])
        g.circle(xx, yy, 3, fill=WHITE, outline=BLACK, width=2)
        lab = f"{e['time_label']}  {_fmt(e['v'], d['unit'])}"
        f = mono(11, 700 if e["type"] == "H" else 500)
        tw, _ = g.text_size(lab, f)
        lx = min(max(x, xx - tw // 2), x + w - tw)
        # both labels stand above their point, in BLACK: a low sits in a trough that may still have sea behind it
        g.text((lx, yy - 20), lab, f, BLACK)
    # now
    nx, ny = X(d["now"]["t"]), Y(d["now"]["v"])
    g.d.line([(nx, top - 10), (nx, floor)], fill=BLACK, width=1)
    g.circle(nx, ny, 5, fill=BLACK)
    g.text((nx + 6, top - 12), "now", sans(11, 700), BLACK)


def _facts(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, stacked: bool) -> None:
    arrow = "↑" if d["trend"] == "rising" else "↓"
    rows = [(_fmt(d["now"]["v"], d["unit"]), f"{arrow} {d['trend']} · {abs(d['rate_m_h']) * 100:.0f} cm/h" if d["unit"] == "m"
             else f"{arrow} {d['trend']} · {abs(d['rate_m_h']):.1f} ft/h")]
    for key, name in (("next_high", "high"), ("next_low", "low")):
        n = d.get(key)
        if n:
            mins = n["in_min"]
            when = f"in {mins // 60} h {mins % 60:02d}" if mins >= 60 else f"in {mins} min"
            rows.append((n["time_label"], f"{name} · {_fmt(n['v'], d['unit'])} · {when}"))
    rows.append((_fmt(d["range"], d["unit"]), "today's range"))
    if stacked:
        for i, (big, small) in enumerate(rows):
            yy = y + i * 58
            g.text((x, yy), big, mono(24, 700), BLACK)
            g.text((x, yy + 30), g.fit_text(small, w, sans(12, 500)), sans(12, 500), DARK)
    else:
        cw = w / len(rows)
        for i, (big, small) in enumerate(rows):
            cx = round(x + i * cw)
            g.text((cx, y), big, mono(24, 700), BLACK)
            g.text((cx, y + 30), g.fit_text(small, int(cw) - 12, sans(12, 500)), sans(12, 500), DARK)


@component(
    "tide", "Tide",
    "The water for the next 18 h: sea as a filled light shape under a hairline curve, six hours of past left of a "
    "now-line, highs/lows labelled at their turning points, hours every 3 h along the floor; beside it height now with "
    "↑/↓, next high and low with countdown, today's range.",
    params={"station": "NOAA station id (default 8518750 The Battery NY)", "at": "YYYY-MM-DD HH:MM pin", "units": "metric|english"},
    fetch=td.fetch,
    native_hint="type:'tide' {pts:'48×u8', now, hi:[…], lo:[…]} ≈ 70 B — a polyline the ESP32 can fill itself",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    st = d["station"]
    title = f"Tide · {st['name']}" + (f", {st['state']}" if st["state"] else "")
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        g.text((MARGIN, MARGIN - 6), title, sans(24, 700), BLACK)
        g.text((g.w - MARGIN, MARGIN - 2), d["at_label"] + ("  · demo" if d["demo"] else ""), mono(12, 500), DARK, anchor="ra")
        top = MARGIN + 46
        _curve(g, d, MARGIN, top, g.w - 2 * MARGIN, 270)
        y = top + 270 + 14
        g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
        _facts(g, d, MARGIN, y + 14, g.w - 2 * MARGIN, False)
    else:
        g = Glass(PORTRAIT)
        g.text((MARGIN, MARGIN - 6), title, sans(22, 700), BLACK)
        g.text((MARGIN, MARGIN + 26), d["at_label"] + ("  · demo" if d["demo"] else ""), mono(12, 500), DARK)
        top = MARGIN + 60
        _curve(g, d, MARGIN, top, g.w - 2 * MARGIN, 330)
        y = top + 330 + 16
        g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
        _facts(g, d, MARGIN, y + 16, g.w - 2 * MARGIN, True)
    return g.snap()
