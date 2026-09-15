"""
◎ goals — rings for anything with a target.

Up to four goals as progress rings (12 o'clock start, a thick black arc on a
light track, over-achievement marked by a filled centre dot), the value in mono
inside, name and "of target" beneath, then a seven-day bar row per goal with
today ringed. A hairline marker on each ring shows where the *day* is — at 18:00
you have used 75 % of the day; a ring behind that marker is behind schedule,
which is the one judgement the card makes. Data: glass.adapters.goals.
"""
from __future__ import annotations

import math
from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import goals as gd
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _ring(g: Glass, goal: Dict[str, Any], cx: int, cy: int, r: int, day_frac: float, big: bool) -> None:
    width = max(10, r // 5)
    g.ring(cx, cy, r, min(1.0, goal["fraction"]), width=width, fill=BLACK, track=LIGHT)
    # day marker: where the day stands, as a tick across the track
    a = math.radians(day_frac * 360 - 90)
    x0, y0 = cx + (r - width - 4) * math.cos(a), cy + (r - width - 4) * math.sin(a)
    x1, y1 = cx + (r + 4) * math.cos(a), cy + (r + 4) * math.sin(a)
    g.d.line([(x0, y0), (x1, y1)], fill=DARK, width=2)
    if goal["done"]:
        g.circle(cx, cy - r + width // 2, width // 2 - 1, fill=WHITE)
    vf = mono(int(r * 0.62) if big else int(r * 0.55), 700)
    val = goal["display"].replace(" " + goal["unit"], "") if goal["unit"] else goal["display"]
    while g.text_size(val, vf)[0] > 2 * (r - width) - 8 and vf.size > 12:
        vf = mono(vf.size - 2, 700)
    g.text((cx, cy - (8 if goal["unit"] else 0)), val, vf, BLACK, anchor="mm")
    if goal["unit"]:
        g.text((cx, cy + int(r * 0.30)), goal["unit"], sans(max(11, r // 6), 500), DARK, anchor="mm")


def _bars(g: Glass, goal: Dict[str, Any], x: int, y: int, w: int, h: int) -> None:
    hist = goal["history"]
    if not hist:
        return
    n = len(hist)
    gap = 4
    bw = (w - gap * (n - 1)) // n
    vmax = max(max(hist), goal["target"]) or 1
    ty = y + h - int(goal["target"] / vmax * h)
    for i, v in enumerate(hist):
        bx = x + i * (bw + gap)
        bh = int(v / vmax * h)
        g.rect((bx, y + h - bh, bx + bw, y + h), fill=BLACK if i == n - 1 else (DARK if v >= goal["target"] else LIGHT))
    g.hairline(x, ty, x + w, DARK)


def _cell(g: Glass, goal: Dict[str, Any], x: int, y: int, w: int, h: int, day_frac: float) -> None:
    bars_h = int(min(64, max(22, h * 0.16)))
    reserve = 14 + 24 + 22 + 12 + bars_h + 18           # gap, name, sub, gap, bars, hits line
    r = min(w // 2 - 8, (h - reserve - 8) // 2)
    cx, cy = x + w // 2, y + r + 4
    _ring(g, goal, cx, cy, r, day_frac, big=r > 70)
    ty = cy + r + 14
    g.text((cx, ty), goal["name"], sans(18, 700), BLACK, anchor="ma")
    sub = ("done · " if goal["done"] else f"{goal['remaining_display']} to go · ") + f"of {goal['target_display']}"
    g.text((cx, ty + 24), g.fit_text(sub, w - 8, sans(12, 500)), sans(12, 500), DARK, anchor="ma")
    by = ty + 24 + 22 + 12
    _bars(g, goal, x + 10, by, w - 20, bars_h)
    if goal["history"]:
        g.text((x + 10, by + bars_h + 4), "7 days", sans(11, 500), DARK)
        g.text((x + w - 10, by + bars_h + 4), f"target hit {goal['hits']}/{len(goal['history'])}", sans(11, 500), DARK, anchor="ra")


@component(
    "goals", "Goals",
    "Up to four daily quantities as progress rings with the value inside, a day-progress tick on each ring "
    "(behind the tick = behind schedule), remaining-to-target, and a seven-day bar row with the target line.",
    params={"goals": "JSON [{name,value,target,unit,history[7]}] (or ~/.tiny/sticky-goals.json)", "demo": "1 → fixture"},
    fetch=gd.fetch,
    native_hint="type:'goals' {n, items:[{name, v:u16, t:u16, unit, hist:7×u16}]} ≈ 120 B; rings are arcs the firmware can draw",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    goals = d["goals"]
    n = max(1, len(goals))
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        g.label((MARGIN, MARGIN), "today", 13, DARK)
        g.text((MARGIN + 70, MARGIN - 4), f"{d['done']} of {n} done", sans(15, 600), BLACK)
        g.text((g.w - MARGIN, MARGIN - 2), f"{d['date_label']}  ·  {d['now']}", sans(13, 600), DARK, anchor="ra")
        top = MARGIN + 30
        g.hairline(MARGIN, top - 6, g.w - MARGIN, DARK)
        gap = 16
        cw = (g.w - 2 * MARGIN - gap * (n - 1)) // n
        for i, goal in enumerate(goals):
            x = MARGIN + i * (cw + gap)
            _cell(g, goal, x, top + 6, cw, g.h - top - MARGIN - 6, d["day_fraction"])
            if i:
                g.vline(x - gap // 2, top + 4, g.h - MARGIN, LIGHT)
    else:
        g = Glass(PORTRAIT)
        g.label((MARGIN, MARGIN), "today", 13, DARK)
        g.text((MARGIN + 70, MARGIN - 4), f"{d['done']} of {n} done", sans(15, 600), BLACK)
        g.text((g.w - MARGIN, MARGIN - 2), d["now"], mono(15, 500), DARK, anchor="ra")
        top = MARGIN + 30
        g.hairline(MARGIN, top - 6, g.w - MARGIN, DARK)
        cols = 2 if n > 1 else 1
        rows = (n + cols - 1) // cols
        gap = 16
        cw = (g.w - 2 * MARGIN - gap * (cols - 1)) // cols
        ch = (g.h - top - MARGIN - gap * (rows - 1)) // rows
        for i, goal in enumerate(goals):
            x = MARGIN + (i % cols) * (cw + gap)
            y = top + (i // cols) * (ch + gap)
            _cell(g, goal, x, y + 6, cw, ch - 6, d["day_fraction"])
            if i % cols:
                g.vline(x - gap // 2, y + 4, y + ch - 4, LIGHT)
            if i // cols and i % cols == 0:
                g.hairline(MARGIN, y - gap // 2, g.w - MARGIN, LIGHT)
    return g.snap()
