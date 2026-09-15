"""
☀ sun — the day drawn as an arc.

A half-ellipse from sunrise (left) to sunset (right) over a horizon hairline;
the sun disc sits where the day is now (at night: a moon on the dotted mirror
arc below the horizon). Golden hours are the thick ends of the arc, solar noon
is a tick at the apex, the horizon carries the two times. Below: four facts —
day length with its delta vs yesterday, golden hour, civil twilight (≈ ±30 min,
labelled as an approximation), tomorrow. Header: place, date, clock, and
"sunset in 3 h 12" — the one number you actually came for.

Data: glass.adapters.sun (Open-Meteo daily; fixture demo).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component, icons
from .adapters import sun as sund
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _in(s: int) -> str:
    m = s // 60
    if m < 1:
        return "now"
    if m < 60:
        return f"in {m} min"
    h, r = divmod(m, 60)
    return f"in {h} h {r:02d}"


def _pt(cx: int, hy: int, rx: int, ry: int, p: float, below: bool = False) -> Tuple[int, int]:
    a = math.pi * (1 - p)
    return round(cx + rx * math.cos(a)), round(hy + ry * math.sin(a)) if below else round(hy - ry * math.sin(a))


def _arc(g: Glass, d: Dict[str, Any], x0: int, x1: int, hy: int, ry: int, night_ry: int = 52) -> None:
    cx, rx = (x0 + x1) // 2, (x1 - x0) // 2
    n = 180
    pts: List[Tuple[int, int]] = [_pt(cx, hy, rx, ry, i / n) for i in range(n + 1)]
    golden = min(0.45, 3600 / max(3600.0, d["day_len_s"]))
    # thin day arc, thick golden ends
    for i in range(n):
        p = i / n
        w = 7 if (p < golden or p > 1 - golden) else 2
        g.d.line((*pts[i], *pts[i + 1]), fill=BLACK, width=w)
    # dotted night arc below the horizon
    for i in range(0, n + 1, 3):
        x, y = _pt(cx, hy, rx, night_ry, i / n, below=True)   # shallow: the facts live below
        g.d.point((x, y), fill=DARK)
    # horizon
    g.d.line((x0 - 30, hy, x1 + 30, hy), fill=BLACK, width=2)
    # noon tick
    ax, ay = _pt(cx, hy, rx, ry, 0.5)
    g.d.line((ax, ay - 14, ax, ay - 4), fill=DARK, width=2)
    g.text((ax, ay - 20), d["noon"], mono(14, 500), DARK, anchor="ms")
    g.label((ax, ay - 40), "solar noon", 11, DARK, anchor="ms")
    # the sun / the moon
    if d["phase"] == "day":
        sx, sy = _pt(cx, hy, rx, ry, d["progress"])
        g.circle(sx, sy, 22, fill=WHITE)   # clear the arc behind the disc
        icons.draw_sun(g, sx, sy, 20)
    else:
        mx, my = _pt(cx, hy, rx, night_ry, d["progress"], below=True)
        g.circle(mx, my, 16, fill=WHITE)
        icons.draw_moon(g, mx, my, 15)
    # endpoints
    f, lf = mono(22, 700), mono(12, 500)
    g.text((x0 - 30, hy + 10), "↑ " + d["sunrise"], f, BLACK)
    g.text((x0 - 30, hy + 38), "sunrise", lf, DARK)
    g.text((x1 + 30, hy + 10), d["sunset"] + " ↓", f, BLACK, anchor="ra")
    g.text((x1 + 30, hy + 38), "sunset", lf, DARK, anchor="ra")


def _fact(g: Glass, x: int, y: int, w: int, label: str, value: str, sub: str = "") -> None:
    g.label((x, y), label, 12, DARK)
    vf = mono(19, 700)
    g.text((x, y + 20), g.fit_text(value, w - 4, vf), vf, BLACK)
    if sub:
        g.text((x, y + 50), g.fit_text(sub, w - 4, sans(14, 400)), sans(14, 400), DARK)


def _facts(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, cols: int) -> None:
    tm = d.get("tomorrow")
    items = [
        ("day length", d["day_len"], d["delta"]),
        ("golden hour", f"{d['golden_pm'][0]}–{d['golden_pm'][1]}", f"morning {d['golden_am'][0]}–{d['golden_am'][1]}"),
        ("civil twilight", f"{d['twilight'][0]}–{d['twilight'][1]}", "±30 min of the sun, approx."),
        ("tomorrow", f"↑{tm['sunrise']} ↓{tm['sunset']}" if tm else "—",
         (f"{'−' if tm['daylight_s'] < d['day_len_s'] else '+'}{abs(int(round((tm['daylight_s'] - d['day_len_s']) / 60)))} min of daylight" if tm else "")),
    ]
    cw = w // cols
    for i, (lab, val, sub) in enumerate(items):
        c, r = i % cols, i // cols
        fx, fy = x + c * cw, y + r * 78
        if c:
            g.vline(fx - 14, fy + 2, fy + 66, LIGHT)
        _fact(g, fx, fy, cw - 20, lab, val, sub)


def _header(g: Glass, d: Dict[str, Any], y: int) -> int:
    g.text((MARGIN, y), "Sun", sans(34, 700), BLACK)
    x = MARGIN + g.text_size("Sun", sans(34, 700))[0] + 14
    g.text((x, y + 12), g.fit_text(f"{d['place']} · {d['date']}", g.w // 2 - x, sans(16, 500)), sans(16, 500), DARK)
    g.text((g.w - MARGIN, y + 4), d["now"], mono(18, 500), BLACK, anchor="ra")
    nx = d["next"]
    g.text((g.w - MARGIN, y + 28), f"{nx['what']} {_in(nx['in_s'])}", sans(15, 600), BLACK, anchor="ra")
    return y + 48


@component(
    "sun", "Sun",
    "Sunrise→sunset arc with the sun where the day is now, golden hours as thick ends, solar "
    "noon, day length with its delta, civil twilight, tomorrow.",
    params={"place": "city", "lat": "", "lon": "", "clock": "HH:MM override", "demo": "1 → fixture"},
    fetch=sund.fetch,
    native_hint="type:'sun' {rise_min, set_min, now_min, day_len_s, delta_s, tm_rise, tm_set} ≈ 40 B — the arc is pure geometry the firmware can draw; refresh every 10 min",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        y = _header(g, d, MARGIN)
        hy = 292
        _arc(g, d, MARGIN + 130, g.w - MARGIN - 130, hy, 190)
        g.hairline(MARGIN, hy + 70, g.w - MARGIN, LIGHT)
        _facts(g, d, MARGIN, hy + 84, g.w - 2 * MARGIN, cols=4)
    else:
        g = Glass(PORTRAIT)
        y = _header(g, d, MARGIN)
        hy = 372
        _arc(g, d, MARGIN + 72, g.w - MARGIN - 72, hy, 210)
        g.hairline(MARGIN, hy + 72, g.w - MARGIN, LIGHT)
        _facts(g, d, MARGIN, hy + 88, g.w - 2 * MARGIN, cols=2)
    return g.snap()
