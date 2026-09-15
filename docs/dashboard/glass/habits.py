"""
✅ habits — thirty days of dots.

One row per habit: the name, thirty dots (filled = done, outline = missed,
today ringed), the current streak in mono, the 30-day rate as a small bar.
Under the matrix, a column of tiny bars — how many habits were done each day —
so a bad week shows as a dip. Header: done today / total, month rate. This is a
Seinfeld calendar the size of a postcard; the data is a text file.
Portrait: the last fourteen days per row.
Data: glass.adapters.habits (params → ~/.tiny/sticky-habits.json → fixture).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import habits as hd
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _header(g: Glass, d: Dict[str, Any], y: int) -> int:
    g.text((MARGIN, y), "Habits", sans(34, 700), BLACK)
    x = MARGIN + g.text_size("Habits", sans(34, 700))[0] + 14
    g.text((x, y + 12), g.fit_text(d["today_label"], g.w // 2 - x + 40, sans(16, 500)), sans(16, 500), DARK)
    g.text((g.w - MARGIN, y + 4), d["now"], mono(18, 500), BLACK, anchor="ra")
    n = len(d["habits"])
    g.text((g.w - MARGIN, y + 28), f"{d['done_today']} of {n} today · {d['month_rate'] * 100:.0f} % this month",
           sans(15, 600), DARK, anchor="ra")
    return y + 54


def _matrix(g: Glass, d: Dict[str, Any], x: int, y: int, name_w: int, dot_step: int, r: int,
            tail_w: int, window: int, rh: int) -> int:
    habits = d["habits"]
    n = d["n"]
    start = n - window
    mx = x + name_w
    # month + day-of-month labels above
    lf = mono(11, 500)
    for i in range(start, n):
        cx = mx + (i - start) * dot_step + r
        if d["months"][i] or i == start:
            g.text((cx - r, y), d["months"][i] or d["months"][max(j for j in range(i + 1) if d["months"][j])], sans(11, 600), DARK)
        if d["doms"][i] in (1, 5, 10, 15, 20, 25) or i == n - 1:
            g.text((cx, y + 14), str(d["doms"][i]), lf, BLACK if i == n - 1 else DARK, anchor="ma")
    y += 32
    nf, sf = sans(16, 600), mono(16, 700)
    for k, h in enumerate(habits):
        ry = y + k * rh
        cy = ry + rh // 2
        if k:
            g.hairline(x, ry - 1, g.w - MARGIN, LIGHT)
        g.text((x, cy), g.fit_text(h["name"], name_w - 14, nf), nf, BLACK, anchor="lm")
        for i in range(start, n):
            cx = mx + (i - start) * dot_step + r
            if h["marks"][i]:
                g.circle(cx, cy, r, fill=BLACK)
            else:
                g.circle(cx, cy, r, outline=DARK, width=1)
            if i == n - 1:
                g.circle(cx, cy, r + 4, outline=BLACK, width=1)
        tx = mx + window * dot_step + 14
        streak = f"{h['streak']} d"
        g.text((tx, cy), streak, sf, BLACK if h["streak"] else DARK, anchor="lm")
        bx = tx + 54
        bw = tail_w - 54 - 14
        if bw > 20:
            g.rect((bx, cy - 3, bx + bw, cy + 3), outline=LIGHT, width=1)
            g.rect((bx, cy - 3, bx + int(h["rate"] * bw), cy + 3), fill=DARK)
            g.text((bx + bw, cy - 16), f"{h['rate'] * 100:.0f} %", mono(11, 500), DARK, anchor="ra")
    y += len(habits) * rh
    # per-day totals as tiny bars
    g.hairline(x, y + 2, g.w - MARGIN, DARK)
    bh = 34 if g.w > g.h else 26
    top = y + 8
    mxn = max(1, len(habits))
    for i in range(start, n):
        cx = mx + (i - start) * dot_step + r
        t = d["totals"][i]
        hpx = int(t / mxn * bh)
        if hpx:
            g.rect((cx - r + 1, top + bh - hpx, cx + r - 1, top + bh), fill=BLACK if i == n - 1 else DARK)
    g.text((x, top + 4), "done / day", sans(12, 500), DARK)
    return top + bh + 6


@component(
    "habits", "Habits",
    "Dot-matrix habit tracker: one row per habit, 30 days of dots, streak, 30-day rate, and a "
    "done-per-day bar row. Fed by params or ~/.tiny/sticky-habits.json.",
    params={"habits": "[{name, days:'0110…'}] (last char = today)", "days": "window (30)", "date": "YYYY-MM-DD", "demo": "1 → fixture"},
    fetch=hd.fetch,
    native_hint="type:'habits' {names[], bits: n×30 packed (4 B/habit), today:u8} ≈ 120 B; refresh on edit",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        y = _header(g, d, MARGIN)
        rows = max(1, len(d["habits"]))
        rh = min(50, (g.h - MARGIN - y - 32 - 48) // rows)
        _matrix(g, d, MARGIN, y, name_w=180, dot_step=15, r=5, tail_w=g.w - MARGIN - (MARGIN + 180 + 30 * 15 + 14), window=30, rh=rh)
    else:
        g = Glass(PORTRAIT)
        y = _header(g, d, MARGIN)
        rows = max(1, len(d["habits"]))
        rh = min(64, (g.h - MARGIN - y - 32 - 40) // rows)
        _matrix(g, d, MARGIN, y, name_w=130, dot_step=17, r=6, tail_w=g.w - MARGIN - (MARGIN + 130 + 14 * 17 + 14), window=14, rh=rh)
    return g.snap()
