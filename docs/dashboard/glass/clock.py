"""
🕰 clock — four faces for the same minute.

analog  — a dial the height of the card: sixty hairline ticks, twelve heavy
          ones, 12/3/6/9 in Inter, a date window at three o'clock, hour and
          minute hands with counterweights, no second hand (e-ink refreshes by
          the minute; a stopped second hand is a lie).
digits  — the time as wide as the card in JetBrains Mono; date beneath.
world   — local time large, then cities: time, weekday when it differs, offset,
          a day/night dot.
minimal — small time and one line, nothing else. For the shelf.
Data: glass.adapters.clock (no network).
"""
from __future__ import annotations

import math
from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import clock as ck
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _pt(cx: int, cy: int, r: float, ang_deg: float):
    a = math.radians(ang_deg - 90)
    return (cx + r * math.cos(a), cy + r * math.sin(a))


def draw_dial(g: Glass, cx: int, cy: int, r: int, h: int, m: int, date_text: str = "") -> None:
    g.circle(cx, cy, r, outline=BLACK, width=3)
    for i in range(60):
        heavy = i % 5 == 0
        a = i * 6
        p0 = _pt(cx, cy, r - (18 if heavy else 10), a)
        p1 = _pt(cx, cy, r - 4, a)
        g.d.line([p0, p1], fill=BLACK if heavy else DARK, width=4 if heavy else 1)
    nf = sans(max(11, int(r * 0.19)), 700)   # never under the 11 px legibility floor
    small = r < 60                            # a tiny dial keeps its ticks, drops the numerals and the date
    for n, a in ((12, 0), (3, 90), (6, 180), (9, 270)):
        if small or (n == 3 and date_text):
            continue
        x, y = _pt(cx, cy, r - int(r * 0.30), a)
        g.text((int(x), int(y)), str(n), nf, BLACK, anchor="mm")
    if date_text and not small:
        x, y = _pt(cx, cy, r - int(r * 0.30), 90)
        df = sans(max(11, int(r * 0.11)), 700)
        tw, th = g.text_size(date_text, df)
        g.rect((int(x) - tw // 2 - 8, int(y) - th // 2 - 6, int(x) + tw // 2 + 8, int(y) + th // 2 + 8), outline=BLACK, width=2, radius=3)
        g.text((int(x), int(y)), date_text, df, BLACK, anchor="mm")
    # hands
    ha = (h % 12 + m / 60) * 30
    ma = m * 6
    for ang, length, width, tail in ((ha, r * 0.55, max(8, r // 18), r * 0.14), (ma, r * 0.82, max(5, r // 30), r * 0.16)):
        tip = _pt(cx, cy, length, ang)
        back = _pt(cx, cy, -tail, ang)
        g.d.line([back, tip], fill=BLACK, width=width)
    g.circle(cx, cy, max(6, r // 20), fill=BLACK)
    g.circle(cx, cy, max(2, r // 60), fill=WHITE)


def _analog(g: Glass, d: Dict[str, Any]) -> None:
    if g.w > g.h:
        r = (g.h - 2 * MARGIN) // 2 - 4
        cx, cy = MARGIN + r + 6, g.h // 2
        draw_dial(g, cx, cy, r, d["h"], d["m"], str(d["day"]))
        x = cx + r + 44
        w = g.w - MARGIN - x
        g.text((x, MARGIN + 6), d["clock"], mono(64, 700), BLACK)
        g.text((x, MARGIN + 86), d["weekday"], sans(28, 700), BLACK)
        g.text((x, MARGIN + 122), f"{d['day']} {d['month']} {d['year']}", sans(18, 500), DARK)
        y = MARGIN + 170
        g.hairline(x, y, g.w - MARGIN, LIGHT)
        y += 14
        for k, v in (("Week", str(d["week"])), ("Day", f"{d['doy']} of {d['days_in_year']}"), ("Zone", d["tz_short"])):
            g.label((x, y), k, 12, DARK)
            g.text((x + w, y - 4), v, mono(18, 600), BLACK, anchor="ra")
            y += 34
        # year progress
        yy = g.h - MARGIN - 20
        g.rect((x, yy, x + w, yy + 6), outline=LIGHT, width=1)
        g.rect((x, yy, x + int(d["doy"] / d["days_in_year"] * w), yy + 6), fill=DARK)
        g.text((x, yy - 18), f"{d['doy'] / d['days_in_year'] * 100:.0f} % of the year", sans(12, 500), DARK)
    else:
        r = (g.w - 2 * MARGIN) // 2 - 4
        cx, cy = g.w // 2, MARGIN + r + 30
        draw_dial(g, cx, cy, r, d["h"], d["m"], str(d["day"]))
        y = cy + r + 40
        g.text((g.w // 2, y), d["clock"], mono(64, 700), BLACK, anchor="ma")
        g.text((g.w // 2, y + 80), d["date"], sans(22, 600), BLACK, anchor="ma")
        g.text((g.w // 2, y + 112), f"week {d['week']} · day {d['doy']} of {d['days_in_year']} · {d['tz_short']}", sans(14, 500), DARK, anchor="ma")


def _digits(g: Glass, d: Dict[str, Any]) -> None:
    w = g.w - 2 * MARGIN
    px = 300
    while g.text_size(d["clock"], mono(px, 700))[0] > w and px > 60:
        px -= 8
    f = mono(px, 700)
    tw, th = g.text_size(d["clock"], f)
    y = (g.h - th) // 2 - (60 if g.w > g.h else 90)
    g.text((g.w // 2, y), d["clock"], f, BLACK, anchor="ma")
    g.text((g.w // 2, y + int(px * 1.15)), d["date"], sans(30 if g.w > g.h else 24, 600), BLACK, anchor="ma")
    g.text((g.w // 2, y + int(px * 1.15) + 42), f"week {d['week']} · {d['tz_short']}", sans(15, 500), DARK, anchor="ma")


def _world(g: Glass, d: Dict[str, Any]) -> None:
    g.label((MARGIN, MARGIN), d["tz_short"], 13, DARK)
    lx = g.w - MARGIN
    g.text((lx, MARGIN - 2), "night", sans(12, 500), DARK, anchor="ra")
    lx -= g.text_size("night", sans(12, 500))[0] + 12
    g.circle(lx, MARGIN + 6, 5, fill=BLACK)
    lx -= 16
    g.text((lx, MARGIN - 2), "day", sans(12, 500), DARK, anchor="ra")
    lx -= g.text_size("day", sans(12, 500))[0] + 12
    g.circle(lx, MARGIN + 6, 5, outline=BLACK, width=2)
    g.text((MARGIN - 4, MARGIN + 14), d["clock"], mono(120 if g.w > g.h else 96, 700), BLACK)
    g.text((MARGIN, MARGIN + (150 if g.w > g.h else 122)), d["date"], sans(20, 600), BLACK)
    if g.w > g.h:
        x, y, w = MARGIN, MARGIN + 196, g.w - 2 * MARGIN
    else:
        x, y, w = MARGIN, MARGIN + 176, g.w - 2 * MARGIN
    g.hairline(x, y, x + w, DARK)
    y += 14
    rh = (g.h - MARGIN - y) // max(1, len(d["zones"])) if d["zones"] else 60
    rh = min(rh, 78)
    for z in d["zones"]:
        cy = y + rh // 2
        g.circle(x + 8, cy, 7, fill=BLACK if not z["is_day"] else None, outline=BLACK, width=2)
        g.text((x + 28, cy), z["city"], sans(24, 600), BLACK, anchor="lm")
        g.text((x + w, cy), z["time"], mono(40, 700), BLACK, anchor="rm")
        tw = g.text_size(z["time"], mono(40, 700))[0]
        meta = (z["day"] + " · " if z["day"] else "") + z["delta_label"]
        g.text((x + w - tw - 16, cy), meta, sans(15, 500), DARK, anchor="rm")
        y += rh
        g.hairline(x, y - 1, x + w, LIGHT)


def _minimal(g: Glass, d: Dict[str, Any]) -> None:
    g.text((g.w // 2, g.h // 2 - 60), d["clock"], mono(96, 500), BLACK, anchor="mm")
    g.hairline(g.w // 2 - 30, g.h // 2 + 8, g.w // 2 + 30, BLACK)
    g.text((g.w // 2, g.h // 2 + 40), d["date"], sans(18, 500), DARK, anchor="mm")


@component(
    "clock", "Clock",
    "Four faces: analog dial with a date window and no second hand, wall-wide digits, a world "
    "clock with day/night dots, and a minimal face for the shelf.",
    params={"face": "analog|digits|world|minimal", "zones": "IANA list for world", "clock": "HH:MM", "date": "YYYY-MM-DD", "tz": ""},
    fetch=ck.fetch,
    native_hint="type:'clock' {face:u8, zones:[{city, off:i8}]} ≈ 60 B — the firmware has the RTC; draw locally each minute",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    g = Glass(LANDSCAPE if orientation == "landscape" else PORTRAIT)
    {"analog": _analog, "digits": _digits, "world": _world, "minimal": _minimal}.get(d["face"], _analog)(g, d)
    return g.snap()
