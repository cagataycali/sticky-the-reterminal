"""
🚇 departures — the board at the top of the stairs.

A departure board, not a map: the stop as the headline, then one row per line
with the route in a black roundel (letters and numbers the way the subway prints
them, a square for a bus), the destination, and the next departures as
minutes — the first one large, the rest small and quiet. Because the desk is
a walk away, each row also says when to LEAVE: minutes-to minus the walk, the
number that actually matters. Service alerts sit in the footer, one line each.
Data: glass.adapters.departures (pushed list · ~/.tiny/sticky-departures.json · fixture).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import departures as dp
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _roundel(g: Glass, cx: int, cy: int, r: int, line: str, kind: str) -> None:
    f = sans(int(r * 1.1), 700) if len(line) <= 1 else sans(int(r * 0.85), 700) if len(line) == 2 else sans(int(r * 0.62), 700)
    if kind == "bus":
        g.rect((cx - r, cy - r, cx + r, cy + r), fill=BLACK)
    else:
        g.circle(cx, cy, r, fill=BLACK)
    g.text((cx, cy), line, f, WHITE, anchor="mm")


def _row(g: Glass, ln: Dict[str, Any], x: int, y: int, w: int, h: int, walk: int) -> None:
    r = min(22, h // 2 - 6)
    _roundel(g, x + r + 2, y + h // 2, r, ln["line"], ln["kind"])
    tx = x + 2 * r + 18
    mins = ln["mins"]
    # right side: a fixed 72 px column for the first departure (large, "min" beneath),
    # the following departures to its left in the quiet gray — three columns that never touch
    rx = x + w
    col = 72
    if mins:
        first = mins[0]
        big = "now" if first == 0 else f"{first}"
        g.text((rx, y + h // 2 - 8), big, mono(30, 700), BLACK, anchor="rm")
        if first > 0:
            g.text((rx, y + h - 4), "min", sans(11, 500), DARK, anchor="rd")
        rest = " · ".join(str(m) for m in mins[1:])
        if rest:
            g.text((rx - col - 8, y + h // 2), rest, mono(13, 500), DARK, anchor="rm")
            right_w = col + 8 + g.text_size(rest, mono(13, 500))[0] + 16
        else:
            right_w = col + 16
    else:
        g.text((rx, y + h // 2), "—", mono(24, 500), DARK, anchor="rm")
        right_w = col
    dw = w - (tx - x) - right_w
    g.text((tx, y + 6), g.fit_text(ln["dest"], dw, sans(17, 700)), sans(17, 700), BLACK)
    sub = ln["note"] or ""
    li = ln.get("leave_in")
    if li is not None and walk:
        leave = "leave now" if li <= 0 else f"leave in {li} min"
        sub = f"{leave} · {sub}" if sub else leave
    if sub:
        g.text((tx, y + 30), g.fit_text(sub, dw, sans(12, 500)), sans(12, 500), DARK)


def _board(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, h: int) -> None:
    n = max(1, len(d["lines"]))
    rows_h = min(96 if g.h > g.w else 84, h // n)
    for i, ln in enumerate(d["lines"]):
        yy = y + i * rows_h
        if yy + rows_h > y + h + 2:
            break
        _row(g, ln, x, yy + 6, w, rows_h - 12, d["walk_min"])
        if i < n - 1:
            g.hairline(x, yy + rows_h, x + w, LIGHT)


@component(
    "departures", "Departures",
    "The board at the top of the stairs: stop as headline, one row per line with the route in a black roundel (square "
    "for a bus), destination, next departures as minutes — first large, rest quiet — and when to LEAVE (minutes minus "
    "the walk). Service alerts in the footer.",
    params={"lines": "JSON list [{line, dest, times:['+3','14:07'], kind, note}]", "stop": "stop name", "walk_min": "minutes from desk to platform", "demo": "1 = fixture"},
    fetch=dp.fetch,
    native_hint="type:'departures' {stop, rows:[{line, dest, mins:[…]}]} ≈ 160 B — roundels are a circle + one glyph",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    sub = " · ".join(s for s in (d["agency"], f"{d['walk_min']} min walk" if d["walk_min"] else "") if s)
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        g.text((MARGIN, MARGIN - 8), d["stop"], sans(30, 700), BLACK)
        g.text((MARGIN, MARGIN + 30), sub + ("  · demo" if d["demo"] else ""), sans(12, 500), DARK)
        g.text((g.w - MARGIN, MARGIN - 2), d["at"], mono(16, 700), BLACK, anchor="ra")
        g.text((g.w - MARGIN, MARGIN + 22), "departures", sans(11, 500), DARK, anchor="ra")
        y0 = MARGIN + 58
        g.hairline(MARGIN, y0 - 6, g.w - MARGIN, DARK)
        foot = 26 * min(2, len(d["alerts"])) + (8 if d["alerts"] else 0)
        _board(g, d, MARGIN, y0, g.w - 2 * MARGIN, g.h - MARGIN - y0 - foot)
        if d["alerts"]:
            fy = g.h - MARGIN - foot + 4
            g.hairline(MARGIN, fy - 6, g.w - MARGIN, LIGHT)
            for i, a in enumerate(d["alerts"][:2]):
                g.rect((MARGIN, fy + i * 22 + 4, MARGIN + 6, fy + i * 22 + 10), fill=BLACK)
                g.text((MARGIN + 14, fy + i * 22), g.fit_text(a, g.w - 2 * MARGIN - 14, sans(12, 500)), sans(12, 500), DARK)
    else:
        g = Glass(PORTRAIT)
        g.text((MARGIN, MARGIN - 8), g.fit_text(d["stop"], g.w - 2 * MARGIN - 70, sans(28, 700)), sans(28, 700), BLACK)
        g.text((MARGIN, MARGIN + 30), sub + ("  · demo" if d["demo"] else ""), sans(12, 500), DARK)
        g.text((g.w - MARGIN, MARGIN - 2), d["at"], mono(16, 700), BLACK, anchor="ra")
        y0 = MARGIN + 58
        g.hairline(MARGIN, y0 - 6, g.w - MARGIN, DARK)
        foot = 22 * len(d["alerts"][:3]) + (12 if d["alerts"] else 0)
        _board(g, d, MARGIN, y0, g.w - 2 * MARGIN, min(4 * 96, g.h - MARGIN - y0 - foot))
        if d["alerts"]:
            fy = g.h - MARGIN - foot + 6
            g.hairline(MARGIN, fy - 8, g.w - MARGIN, LIGHT)
            for i, a in enumerate(d["alerts"][:3]):
                g.rect((MARGIN, fy + i * 22 + 4, MARGIN + 6, fy + i * 22 + 10), fill=BLACK)
                g.text((MARGIN + 14, fy + i * 22), g.fit_text(a, g.w - 2 * MARGIN - 14, sans(12, 500)), sans(12, 500), DARK)
    return g.snap()
