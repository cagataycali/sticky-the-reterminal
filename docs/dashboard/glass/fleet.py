"""
📡 fleet — a Sticky showing its siblings.

Every device on the owner's tiny.technology account, grouped Glass · Sensors ·
Computers · Phones · Endpoints. Row: presence dot (filled = online inside the
dashboard's window, outlined = seen before, dotted = never), name with the platform in mono
beside it, last-seen right-aligned. Glass rows add firmware and a battery bar with a
charging tick. Groups flow into two columns in landscape, one in portrait; the
header carries "N of M online" and the clock.

Data: glass.adapters.fleet (server._fleet live, fixture demo).
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import fleet as fl
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans

ROW, ROW_GLASS, GROUP_HEAD = 30, 46, 26


def _seen(age) -> str:
    if age is None:
        return "never"
    a = int(age)
    if a < 60:
        return f"{a} s"
    if a < 3600:
        return f"{a // 60} min"
    if a < 86400:
        return f"{a // 3600} h"
    return f"{a // 86400} d"


def _dot(g: Glass, cx: int, cy: int, dev: Dict[str, Any]) -> None:
    if dev["online"]:
        g.circle(cx, cy, 6, fill=BLACK)
    elif dev["age_s"] is None:
        for a in range(0, 360, 60):
            import math
            g.d.point((cx + round(6 * math.cos(math.radians(a))), cy + round(6 * math.sin(math.radians(a)))), fill=DARK)
    else:
        g.circle(cx, cy, 6, outline=DARK, width=2)


def _battery(g: Glass, x: int, y: int, pct, charging) -> None:
    w, h = 34, 12
    g.rect((x, y, x + w, y + h), outline=BLACK, width=1)
    g.rect((x + w + 1, y + 3, x + w + 3, y + h - 3), fill=BLACK)
    if pct is not None:
        fw = max(2, round((w - 4) * min(100, max(0, pct)) / 100))
        g.rect((x + 2, y + 2, x + 2 + fw, y + h - 2), fill=BLACK if pct > 20 else DARK)
    if charging:
        g.d.polygon((x + w // 2 + 1, y - 2, x + w // 2 - 3, y + h // 2 + 1, x + w // 2, y + h // 2 + 1,
                     x + w // 2 - 1, y + h + 2, x + w // 2 + 3, y + h // 2 - 1, x + w // 2, y + h // 2 - 1), fill=WHITE)
        g.d.polygon((x + w // 2 + 1, y - 1, x + w // 2 - 2, y + h // 2, x + w // 2, y + h // 2,
                     x + w // 2 - 1, y + h + 1, x + w // 2 + 2, y + h // 2 - 1, x + w // 2, y + h // 2 - 1), fill=BLACK)


def _group_h(grp: Dict[str, Any]) -> int:
    rh = ROW_GLASS if grp["key"] == "glass" else ROW
    return GROUP_HEAD + rh * len(grp["devices"]) + 12


def _group(g: Glass, grp: Dict[str, Any], x0: int, y: int, x1: int) -> int:
    rh = ROW_GLASS if grp["key"] == "glass" else ROW
    g.label((x0, y), grp["label"], 13, DARK)
    g.text((x1, y - 1), f"{grp['online']}/{len(grp['devices'])}", mono(13, 500), DARK, anchor="ra")
    g.hairline(x0, y + 18, x1, DARK)
    y += GROUP_HEAD
    nf, pf, sf = sans(18, 600), mono(12, 500), mono(13, 500)
    for i, d in enumerate(grp["devices"]):
        if i:
            g.hairline(x0 + 22, y - 3, x1, LIGHT)
        ink = BLACK if d["online"] else DARK
        _dot(g, x0 + 7, y + 11, d)
        tx = x0 + 22
        seen = _seen(d["age_s"])
        sw = g.text_size(seen, sf)[0]
        g.text((x1, y + 3), seen, sf, ink if d["online"] else DARK, anchor="ra")
        if grp["key"] != "glass":
            # single line: name, then platform in mono right after it
            name = g.fit_text(str(d["name"]), x1 - tx - sw - 80, nf)
            g.text((tx, y), name, nf, ink)
            px = tx + g.text_size(name, nf)[0] + 10
            g.text((px, y + 6), g.fit_text(str(d.get("platform") or ""), x1 - px - sw - 10, pf), pf, DARK)
            y += rh
            continue
        g.text((tx, y), g.fit_text(str(d["name"]), x1 - tx - sw - 10, nf), nf, ink)
        if grp["key"] == "glass":
            sub = d.get("fw") or "fw ?"
            g.text((tx, y + 24), sub, pf, DARK)
            bx = x1 - 40
            _battery(g, bx, y + 24, d.get("battery_pct"), d.get("charging"))
            if d.get("battery_pct") is not None:
                g.text((bx - 6, y + 24), f"{round(d['battery_pct'])} %", pf, DARK, anchor="ra")
        y += rh
    return y + 12


def _flow(g: Glass, groups: List[Dict[str, Any]], cols: List[Tuple[int, int]], y0: int, y1: int) -> None:
    """Greedy column fill: a group goes to the first column with room."""
    ys = [y0] * len(cols)
    for grp in groups:
        h = _group_h(grp)
        for c, (x0, x1) in enumerate(cols):
            if ys[c] + h <= y1 or c == len(cols) - 1:
                ys[c] = _group(g, grp, x0, ys[c], x1)
                break


def _header(g: Glass, d: Dict[str, Any], y: int) -> int:
    big = sans(34, 700)
    g.text((MARGIN, y), "Fleet", big, BLACK)
    x = MARGIN + g.text_size("Fleet", big)[0] + 14
    f = sans(15, 700)
    s = f"{d['online']} of {d['total']} online"
    w = g.text_size(s, f)[0] + 20
    g.rect((x, y + 8, x + w, y + 34), fill=BLACK, radius=13)
    g.text((x + w // 2, y + 21), s, f, WHITE, anchor="mm")
    g.text((g.w - MARGIN, y + 4), d["now"], mono(18, 500), BLACK, anchor="ra")
    g.text((g.w - MARGIN, y + 28), f"online = seen < {round(d['window_s'])} s", mono(12, 500), DARK, anchor="ra")
    return y + 56


@component(
    "fleet", "Fleet",
    "Every device on the account — Glass, Sensors, Computers, Phones, Endpoints — with presence "
    "dots, last seen, firmware and battery for the Stickies.",
    params={"demo": "1 → fixture", "online_window_s": "presence window (default: dashboard's 90 s)"},
    fetch=fl.fetch,
    native_hint="type:'fleet' {n_on, n, groups:[{label, rows:[{name, plat, age_s|-1, on:u8, batt?, fw?}]}]} ≈ 600 B; refresh every 5 min",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        y = _header(g, d, MARGIN)
        gap = 44
        cw = (g.w - 2 * MARGIN - gap) // 2
        g.vline(MARGIN + cw + gap // 2, y + 2, g.h - MARGIN, LIGHT)
        _flow(g, d["groups"], [(MARGIN, MARGIN + cw), (MARGIN + cw + gap, g.w - MARGIN)], y + 4, g.h - MARGIN)
    else:
        g = Glass(PORTRAIT)
        y = _header(g, d, MARGIN)
        _flow(g, d["groups"], [(MARGIN, g.w - MARGIN)], y + 4, g.h - MARGIN)
    return g.snap()
