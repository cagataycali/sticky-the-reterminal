"""
🌙 moon — one big moon, drawn from the number.

The disc is rendered pixel-row by pixel-row from the phase fraction: for each
row the terminator sits at w·cos(2πf) (the lit limb projected), lit side to the
right while waxing — the sky as seen from the northern hemisphere. Lit = paper
white with a black limb; dark = black. A few LIGHT maria on the lit side so it
reads as the moon, not a pie chart. Beside it: phase name, illumination, age,
the next four principal phases with dates, and seven mini-moons for the week.
Data: glass.adapters.moon (arithmetic, ±½ day, no network).
"""
from __future__ import annotations

import math
from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import moon as md
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans

MARIA = [(-0.25, -0.35, 0.16), (0.05, -0.45, 0.12), (0.30, -0.15, 0.13), (-0.15, 0.05, 0.11),
         (0.15, 0.20, 0.09), (-0.40, 0.25, 0.08), (0.40, 0.42, 0.06)]


def draw_moon(g: Glass, cx: int, cy: int, r: int, f: float, maria: bool = True) -> None:
    """f: 0 new → 0.5 full → 1 new. Pixel-exact terminator, no anti-aliasing to fight the palette."""
    c = math.cos(2 * math.pi * f)
    g.circle(cx, cy, r, fill=BLACK)
    px = g.im.load()
    for dy in range(-r, r + 1):
        w = math.sqrt(max(0.0, r * r - dy * dy))
        xt = w * c
        if f < 0.5:
            lo, hi = xt, w
        else:
            lo, hi = -w, -xt
        if hi - lo < 1:
            continue
        for dx in range(int(math.ceil(lo)), int(math.floor(hi)) + 1):
            px[cx + dx, cy + dy] = WHITE
    if maria and r >= 40:
        for mx, my, mr in MARIA:
            x, y, rr = cx + int(mx * r), cy + int(my * r), max(2, int(mr * r))
            # only where the disc is lit: sample the centre
            if px[x, y] == WHITE:
                g.circle(x, y, rr, fill=LIGHT)
    g.circle(cx, cy, r, outline=BLACK, width=max(2, r // 40))


def _header(g: Glass, d: Dict[str, Any], y: int) -> int:
    g.text((MARGIN, y), "Moon", sans(34, 700), BLACK)
    x = MARGIN + g.text_size("Moon", sans(34, 700))[0] + 14
    g.text((x, y + 12), g.fit_text(d["date_label"], g.w // 2 - x + 40, sans(16, 500)), sans(16, 500), DARK)
    g.text((g.w - MARGIN, y + 4), d["now"], mono(18, 500), BLACK, anchor="ra")
    g.text((g.w - MARGIN, y + 28), d["note"], sans(13, 500), DARK, anchor="ra")
    return y + 50


def _facts(g: Glass, d: Dict[str, Any], x: int, y: int, w: int) -> int:
    g.text((x, y), d["name"], sans(30, 700), BLACK)
    g.text((x, y + 40), f"{d['illumination'] * 100:.0f} % lit · day {d['age_days']:.1f} of 29.5 · {'waxing' if d['waxing'] else 'waning'}",
           sans(15, 500), DARK)
    y += 76
    g.label((x, y), "Next", 12, DARK)
    g.hairline(x, y + 16, x + w, DARK)
    y += 26
    for u in d["upcoming"]:
        g.text((x, y), u["name"], sans(16, 600), BLACK)
        g.text((x + w, y), u["in"], mono(15, 500), BLACK, anchor="ra")
        inw = g.text_size(u["in"], mono(15, 500))[0]
        g.text((x + w - inw - 12, y + 1), u["label"], sans(14, 500), DARK, anchor="ra")
        y += 30
    return y


def _strip(g: Glass, d: Dict[str, Any], x: int, y: int, w: int) -> None:
    n = len(d["strip"])
    step = w // n
    r = min(16, step // 2 - 6)
    for i, s in enumerate(d["strip"]):
        cx = x + i * step + step // 2
        draw_moon(g, cx, y + r, r, s["fraction"], maria=False)
        g.text((cx, y + 2 * r + 8), s["dow"], sans(12, 600), DARK, anchor="ma")


@component(
    "moon", "Moon",
    "The moon's disc rendered from the phase fraction (pixel-exact terminator, maria on the lit side), "
    "phase name, illumination, age, the next four principal phases, seven mini-moons for the week.",
    params={"date": "YYYY-MM-DD (tests)", "time": "HH:MM"},
    fetch=md.fetch,
    native_hint="type:'moon' {f:u16 (fraction×65535), next:[{k, in_d}]} ≈ 24 B — the firmware can draw the disc from f alone; refresh hourly",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        y = _header(g, d, MARGIN)
        r = 160
        draw_moon(g, MARGIN + r + 20, y + r + 12, r, d["fraction"])
        fx = MARGIN + 2 * r + 76
        fw = g.w - MARGIN - fx
        _facts(g, d, fx, y + 6, fw)
        g.label((fx, g.h - MARGIN - 84), "The week ahead", 12, DARK)
        g.hairline(fx, g.h - MARGIN - 66, g.w - MARGIN, LIGHT)
        _strip(g, d, fx, g.h - MARGIN - 54, fw)
    else:
        g = Glass(PORTRAIT)
        y = _header(g, d, MARGIN)
        r = 150
        draw_moon(g, g.w // 2, y + r + 16, r, d["fraction"])
        fy = y + 2 * r + 44
        g.text((g.w // 2, fy), d["name"], sans(30, 700), BLACK, anchor="ma")
        g.text((g.w // 2, fy + 40), f"{d['illumination'] * 100:.0f} % lit · day {d['age_days']:.1f} · {'waxing' if d['waxing'] else 'waning'}",
               sans(15, 500), DARK, anchor="ma")
        ly = fy + 76
        g.hairline(MARGIN, ly, g.w - MARGIN, DARK)
        ly += 12
        for u in d["upcoming"][:3]:
            g.text((MARGIN, ly), u["name"], sans(16, 600), BLACK)
            g.text((g.w - MARGIN, ly), u["in"], mono(15, 500), BLACK, anchor="ra")
            inw = g.text_size(u["in"], mono(15, 500))[0]
            g.text((g.w - MARGIN - inw - 12, ly + 1), u["label"], sans(14, 500), DARK, anchor="ra")
            ly += 30
        g.hairline(MARGIN, g.h - MARGIN - 66, g.w - MARGIN, LIGHT)
        _strip(g, d, MARGIN, g.h - MARGIN - 54, g.w - 2 * MARGIN)
    return g.snap()
