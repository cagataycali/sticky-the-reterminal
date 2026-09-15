"""
🌙 overnight — the frame the glass holds while everything sleeps.

No clock. Nothing on this card is wrong at 06:00. A large "Tomorrow, Tuesday"
with the first event as the headline (07:30 Run · Riverside loop), then the
day in one line — 6 events · high 82° low 58° overcast · sunrise 06:30 — the
to-do items due tomorrow, the moon tonight drawn true, and a quiet footer:
"lights out Monday, 7 September · 4 of 13 awake". Mostly white: an e-ink
panel in a dark room is a page, not a screen, and this one should read like
a note left on the desk. Pair with the `sleep` verb: show, then sleep.
Data: glass.adapters.overnight (calendar+1 day, weather.days[1], sun.tomorrow,
moon, todo, fleet — all with fixtures).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from . import icons
from .adapters import overnight as od
from .canvas import BLACK, DARK, LIGHT, MARGIN, Glass, mono, sans
from .moon import draw_moon


def _deg(v) -> str:
    return "—" if v is None else f"{round(v)}°"


def _headline(g: Glass, d: Dict[str, Any], x: int, y: int, w: int) -> int:
    g.label((x, y), "tomorrow", 13, DARK)
    y += 22
    g.text((x, y), d["tomorrow_label"], sans(34, 700), BLACK)
    y += 50
    f = d["first"]
    if f:
        g.text((x, y + 6), f["start"], mono(30, 700), BLACK)
        tw = g.text_size(f["start"], mono(30, 700))[0] + 16
        lines = g.wrap(f["title"], w - tw, sans(24, 700), max_lines=2)
        for ln in lines:
            g.text((x + tw, y), ln, sans(24, 700), BLACK)
            y += 30
        meta = " · ".join(s for s in (f"until {f['end']}", f.get("location", "")) if s)
        g.text((x + tw, y), meta, sans(13, 500), DARK)
        y += 22
    else:
        g.text((x, y), "Nothing scheduled.", sans(24, 700), BLACK)
        y += 34
    if d["all_day"]:
        g.text((x, y), g.fit_text("all day · " + ", ".join(d["all_day"]), w, sans(13, 500)), sans(13, 500), DARK)
        y += 20
    return y


def _dayline(g: Glass, d: Dict[str, Any], x: int, y: int, w: int) -> int:
    parts = [f"{d['n_events']} event{'s' if d['n_events'] != 1 else ''}",
             f"high {_deg(d['hi'])}  low {_deg(d['lo'])}" + (f"  ·  {d['precip']}% rain" if (d["precip"] or 0) >= 20 else ""),
             d["cond"].lower(), f"sunrise {d['sunrise']}"]
    icons.draw(g, d["glyph"], x + 14, y + 10, 13)
    g.text((x + 36, y), g.fit_text("  ·  ".join(parts), w - 36, sans(15, 500)), sans(15, 500), BLACK)
    return y + 28


def _due(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, y_max: int) -> int:
    g.label((x, y), f"due tomorrow · {len(d['due'])}" if d["due"] else "due tomorrow", 11, DARK)
    y += 18
    if not d["due"]:
        g.text((x, y), f"Nothing due — {d['n_open']} open in the week.", sans(14, 500), DARK)
        return y + 22
    for it in d["due"]:
        if y + 22 > y_max:
            break
        g.rect((x, y + 3, x + 12, y + 15), outline=BLACK, width=1)
        tag_w = g.text_size(it["tag"], sans(11, 500))[0] + 8 if it["tag"] else 0
        g.text((x + 20, y), g.fit_text(it["text"], w - 20 - tag_w, sans(15, 500)), sans(15, 500), BLACK)
        if it["tag"]:
            g.text((x + w, y + 3), it["tag"], sans(11, 500), DARK, anchor="ra")
        y += 24
    return y


def _then(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, y_max: int) -> int:
    rest = d["events"][1:]
    if not rest:
        return y
    g.label((x, y), "then", 11, DARK)
    y += 18
    for e in rest:
        if y + 22 > y_max:
            g.text((x, y), f"+{len(rest) - rest.index(e)} more", sans(11, 500), DARK)
            break
        g.text((x, y), e["start"], mono(14, 500), DARK)
        g.text((x + 56, y), g.fit_text(e["title"], w - 56, sans(15, 500)), sans(15, 500), BLACK)
        y += 24
    return y


def _moon(g: Glass, d: Dict[str, Any], cx: int, cy: int, r: int) -> None:
    draw_moon(g, cx, cy, r, d["moon_f"])
    g.text((cx, cy + r + 10), d["moon_name"], sans(13, 600), BLACK, anchor="ma")
    g.text((cx, cy + r + 28), f"{round(d['moon_illum'] * 100)}% lit tonight", sans(11, 500), DARK, anchor="ma")


def _footer(g: Glass, d: Dict[str, Any], y: int) -> None:
    g.hairline(MARGIN, y - 10, g.w - MARGIN, LIGHT)
    left = f"lights out {d['tonight']}"
    if d["fleet"] and d["fleet"].get("total"):
        left += f"  ·  {d['fleet']['online']} of {d['fleet']['total']} awake"
    g.text((MARGIN, y), left, sans(12, 500), DARK)
    right = "asleep · press to wake"
    if d["missing"]:
        right = "offline for " + ", ".join(d["missing"]) + " · " + right
    g.text((g.w - MARGIN, y), right, sans(12, 500), DARK, anchor="ra")


@component(
    "overnight", "Overnight",
    "The sleep frame: no clock, nothing that goes stale by morning — tomorrow's first event as the headline, "
    "the day in one line (events · high/low · condition · sunrise), what's due tomorrow, the moon tonight, "
    "and a footer with the lights-out date. Show it, then `sleep`.",
    params={"ics_url": "calendar", "place": "city", "units": "f|c", "date": "YYYY-MM-DD (tonight) override", "demo": "1 → fixtures"},
    fetch=od.fetch,
    native_hint="type:'overnight' {tomorrow, first:{t,title,where}, line, due:[…], moon_f, footer} ≈ 300 B; the firmware's `sleep` verb keeps it at 0 draw",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        if d["demo"]:
            g.text((g.w - MARGIN, MARGIN - 2), "demo", sans(11, 500), DARK, anchor="ra")
        cw = g.w - 2 * MARGIN - 190
        y = _headline(g, d, MARGIN, MARGIN, cw)
        y = _dayline(g, d, MARGIN, y + 12, cw)
        g.hairline(MARGIN, y + 6, MARGIN + cw, LIGHT)
        half = (cw - 30) // 2
        _due(g, d, MARGIN, y + 20, half, g.h - MARGIN - 40)
        g.vline(MARGIN + half + 15, y + 22, g.h - MARGIN - 44, LIGHT)
        _then(g, d, MARGIN + half + 30, y + 20, cw - half - 30, g.h - MARGIN - 40)
        _moon(g, d, g.w - MARGIN - 80, MARGIN + 92, 62)
        _footer(g, d, g.h - MARGIN - 6)
    else:
        g = Glass(PORTRAIT)
        if d["demo"]:
            g.text((g.w - MARGIN, MARGIN - 2), "demo", sans(11, 500), DARK, anchor="ra")
        w = g.w - 2 * MARGIN
        _moon(g, d, g.w // 2, MARGIN + 90, 70)
        y = _headline(g, d, MARGIN, MARGIN + 220, w)
        y = _dayline(g, d, MARGIN, y + 12, w)
        g.hairline(MARGIN, y + 6, g.w - MARGIN, LIGHT)
        y = _due(g, d, MARGIN, y + 20, w, g.h - MARGIN - 160)
        _then(g, d, MARGIN, y + 14, w, g.h - MARGIN - 40)
        _footer(g, d, g.h - MARGIN - 6)
    return g.snap()
