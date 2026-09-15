"""
🌦 glass.icons — weather glyphs drawn with primitives, in palette values.

Every glyph is `draw_<name>(g, cx, cy, r)` — centered, radius-scaled, so the
same icon serves the 120 px hero and the 28 px forecast row. No bitmaps.
"""
from __future__ import annotations

import math

from .canvas import BLACK, DARK, LIGHT, WHITE, Glass


def _cloud_shape(g: Glass, cx: float, cy: float, r: float, color: int) -> None:
    lobes = [(cx - r * 0.45, cy + r * 0.05, r * 0.42), (cx + r * 0.05, cy - r * 0.2, r * 0.55),
             (cx + r * 0.5, cy + r * 0.1, r * 0.4)]
    for (x, y, rr) in lobes:
        g.d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=color)
    g.d.rounded_rectangle((cx - r * 0.87, cy + r * 0.02, cx + r * 0.9, cy + r * 0.5),
                          radius=max(1, int(r * 0.2)), fill=color)


def _cloud(g: Glass, cx: int, cy: int, r: int, fill: int = WHITE, outline: int = BLACK, w: int | None = None) -> None:
    """Outlined cloud = the union shape in the outline color, then the same
    shape shrunk by the stroke in the fill color — a clean silhouette, no
    interior seams from overlapping arcs."""
    w = w or max(2, r // 9)
    _cloud_shape(g, cx, cy, r, outline)
    inner = r - w * 1.6
    if inner > 2:
        _cloud_shape(g, cx, cy + w * 0.2, inner, fill)


def draw_sun(g: Glass, cx: int, cy: int, r: int) -> None:
    w = max(2, r // 9)
    core = int(r * 0.5)
    g.circle(cx, cy, core, fill=WHITE, outline=BLACK, width=w)
    for k in range(8):
        a = math.radians(k * 45)
        x0, y0 = cx + math.cos(a) * r * 0.68, cy + math.sin(a) * r * 0.68
        x1, y1 = cx + math.cos(a) * r * 0.98, cy + math.sin(a) * r * 0.98
        g.d.line((x0, y0, x1, y1), fill=BLACK, width=w)


def draw_moon(g: Glass, cx: int, cy: int, r: int) -> None:
    w = max(2, r // 9)
    rr = int(r * 0.75)
    g.circle(cx, cy, rr, fill=BLACK)
    g.circle(cx + int(rr * 0.55), cy - int(rr * 0.35), int(rr * 0.78), fill=WHITE)
    g.d.arc((cx - rr, cy - rr, cx + rr, cy + rr), 20, 300, fill=BLACK, width=w)  # crisp edge


def draw_cloud(g: Glass, cx: int, cy: int, r: int) -> None:
    _cloud(g, cx, cy, r, fill=WHITE)


def draw_sun_cloud(g: Glass, cx: int, cy: int, r: int) -> None:
    draw_sun(g, cx + int(r * 0.35), cy - int(r * 0.35), int(r * 0.62))
    _cloud(g, cx - int(r * 0.1), cy + int(r * 0.2), int(r * 0.8), fill=WHITE)


def draw_moon_cloud(g: Glass, cx: int, cy: int, r: int) -> None:
    draw_moon(g, cx + int(r * 0.4), cy - int(r * 0.4), int(r * 0.55))
    _cloud(g, cx - int(r * 0.1), cy + int(r * 0.2), int(r * 0.8), fill=WHITE)


def _drops(g: Glass, cx: int, cy: int, r: int, n: int, slant: float, w: int, length: float = 0.35) -> None:
    for i in range(n):
        x = cx - r * 0.5 + i * (r * 1.0 / max(1, n - 1))
        y0 = cy + r * 0.62
        g.d.line((x, y0, x - slant * r, y0 + r * length), fill=BLACK, width=w)


def draw_rain(g: Glass, cx: int, cy: int, r: int) -> None:
    _cloud(g, cx, cy - int(r * 0.15), int(r * 0.85), fill=DARK, outline=BLACK)
    _drops(g, cx, cy, r, 3, 0.12, max(2, r // 9))


def draw_drizzle(g: Glass, cx: int, cy: int, r: int) -> None:
    _cloud(g, cx, cy - int(r * 0.15), int(r * 0.85), fill=LIGHT, outline=BLACK)
    _drops(g, cx, cy, r, 4, 0.08, max(2, r // 12), length=0.22)


def draw_sleet(g: Glass, cx: int, cy: int, r: int) -> None:
    _cloud(g, cx, cy - int(r * 0.15), int(r * 0.85), fill=LIGHT, outline=BLACK)
    _drops(g, cx, cy, r, 2, 0.12, max(2, r // 9))
    for x in (cx + r * 0.3,):
        g.circle(int(x), int(cy + r * 0.85), max(2, r // 10), fill=BLACK)


def draw_snow(g: Glass, cx: int, cy: int, r: int) -> None:
    _cloud(g, cx, cy - int(r * 0.15), int(r * 0.85), fill=WHITE, outline=BLACK)
    w = max(2, r // 12)
    for i in range(3):
        x = cx - r * 0.45 + i * r * 0.45
        y = cy + r * 0.8
        s = r * 0.16
        for a in (0, 60, 120):
            aa = math.radians(a)
            g.d.line((x - math.cos(aa) * s, y - math.sin(aa) * s, x + math.cos(aa) * s, y + math.sin(aa) * s),
                     fill=BLACK, width=w)


def draw_storm(g: Glass, cx: int, cy: int, r: int) -> None:
    _cloud(g, cx, cy - int(r * 0.2), int(r * 0.85), fill=DARK, outline=BLACK)
    bolt = [(cx + r * 0.1, cy + r * 0.3), (cx - r * 0.2, cy + r * 0.7), (cx + r * 0.02, cy + r * 0.7),
            (cx - r * 0.12, cy + r * 1.05), (cx + r * 0.3, cy + r * 0.55), (cx + r * 0.06, cy + r * 0.55),
            (cx + r * 0.28, cy + r * 0.3)]
    g.d.polygon(bolt, fill=BLACK)


def draw_fog(g: Glass, cx: int, cy: int, r: int) -> None:
    _cloud(g, cx, cy - int(r * 0.25), int(r * 0.8), fill=WHITE, outline=BLACK)
    w = max(2, r // 9)
    for i, (a, b) in enumerate(((-0.7, 0.6), (-0.5, 0.8), (-0.75, 0.45))):
        y = cy + r * (0.62 + i * 0.2)
        g.d.line((cx + a * r, y, cx + b * r, y), fill=DARK, width=w)


GLYPHS = {
    "sun": draw_sun, "moon": draw_moon, "cloud": draw_cloud, "sun_cloud": draw_sun_cloud,
    "moon_cloud": draw_moon_cloud, "rain": draw_rain, "drizzle": draw_drizzle, "sleet": draw_sleet,
    "snow": draw_snow, "storm": draw_storm, "fog": draw_fog,
}


def draw(g: Glass, name: str, cx: int, cy: int, r: int) -> None:
    GLYPHS.get(name, draw_cloud)(g, cx, cy, r)
