"""
🔔 notifications — one inbox for DMs, calendar nudges, fleet events, GitHub, system.

Header: title, an inverted "N new" chip, per-source counts, the clock.
Rows: a vector glyph per source (bubble / calendar / device / branch / info),
sender in semibold, age right-aligned in mono, one-line preview in mid-gray;
unread rows carry a 4 px black bar at the left edge and a black sender.
Read rows fade (DARK glyph). Landscape goes two-column once one column is
full (12 rows instead of 6 — density is the point). Footer counts the rest.

Data: glass.adapters.notifications (params.items push, live rails, or fixture).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import notifications as notif
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans

SOURCE_LABEL = {"dm": "DM", "calendar": "cal", "fleet": "fleet", "github": "github", "system": "system"}


def _age(s: int) -> str:
    if s < 60:
        return "now"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def _glyph(g: Glass, src: str, cx: int, cy: int, ink: int) -> None:
    """20 px line glyphs, 2 px strokes — legible at arm's length, no emoji."""
    d = g.d
    if src == "dm":
        d.rounded_rectangle((cx - 10, cy - 8, cx + 10, cy + 5), radius=5, outline=ink, width=2)
        d.polygon((cx - 5, cy + 4, cx - 2, cy + 4, cx - 7, cy + 9), fill=ink)
        d.rectangle((cx - 5, cy + 4, cx - 2, cy + 5), fill=WHITE)
    elif src == "calendar":
        d.rounded_rectangle((cx - 9, cy - 7, cx + 9, cy + 9), radius=2, outline=ink, width=2)
        d.rectangle((cx - 9, cy - 7, cx + 9, cy - 3), fill=ink)
        d.line((cx - 5, cy - 10, cx - 5, cy - 6), fill=ink, width=2)
        d.line((cx + 5, cy - 10, cx + 5, cy - 6), fill=ink, width=2)
        d.rectangle((cx - 4, cy + 1, cx - 1, cy + 4), fill=ink)
    elif src == "fleet":
        d.rounded_rectangle((cx - 10, cy - 7, cx + 10, cy + 6), radius=2, outline=ink, width=2)
        d.line((cx - 5, cy + 9, cx + 5, cy + 9), fill=ink, width=2)
        d.rectangle((cx - 6, cy - 3, cx + 6, cy - 1), fill=ink)
        d.rectangle((cx - 6, cy + 1, cx + 1, cy + 3), fill=ink)
    elif src == "github":
        d.ellipse((cx - 8, cy - 9, cx - 2, cy - 3), outline=ink, width=2)
        d.ellipse((cx - 8, cy + 3, cx - 2, cy + 9), outline=ink, width=2)
        d.ellipse((cx + 3, cy - 6, cx + 9, cy), outline=ink, width=2)
        d.line((cx - 5, cy - 3, cx - 5, cy + 3), fill=ink, width=2)
        d.line((cx - 5, cy + 3, cx + 6, cy), fill=ink, width=2)
    else:
        d.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), outline=ink, width=2)
        d.rectangle((cx - 1, cy - 2, cx + 1, cy + 5), fill=ink)
        d.rectangle((cx - 1, cy - 6, cx + 1, cy - 4), fill=ink)


def _header(g: Glass, d: Dict[str, Any], y: int, wide: bool) -> int:
    big = sans(34, 700)
    x = MARGIN
    g.text((x, y), d["title"], big, BLACK)
    x += g.text_size(d["title"], big)[0] + 14
    if d["unread"]:
        f = sans(15, 700)
        s = f"{d['unread']} new"
        w = g.text_size(s, f)[0] + 20
        g.rect((x, y + 8, x + w, y + 34), fill=BLACK, radius=13)
        g.text((x + w // 2, y + 21), s, f, WHITE, anchor="mm")
    parts = " · ".join(f"{n} {SOURCE_LABEL.get(s, s)}" for s, n in d["counts"].items())
    if wide:
        g.text((g.w - MARGIN, y + 4), d["now"], mono(18, 500), BLACK, anchor="ra")
        g.text((g.w - MARGIN, y + 28), g.fit_text(parts, 360, mono(13, 500)), mono(13, 500), DARK, anchor="ra")
        return y + 52
    g.text((g.w - MARGIN, y + 4), d["now"], mono(18, 500), BLACK, anchor="ra")
    g.text((MARGIN, y + 46), g.fit_text(parts, g.w - 2 * MARGIN, mono(13, 500)), mono(13, 500), DARK)
    return y + 70


def _rows(g: Glass, items, box, rh: int) -> int:
    """Rows inside box; returns how many were drawn."""
    x0, y0, x1, y1 = box
    n_fit = max(1, (y1 - y0) // rh)
    shown = items[:n_fit]
    ff, tf, af = sans(18, 600), sans(16, 400), mono(13, 500)
    y = y0
    for i, it in enumerate(shown):
        unread = it["unread"]
        ink = BLACK if unread else DARK
        if i:
            g.hairline(x0 + 44, y, x1, LIGHT)
        if unread:
            g.rect((x0 - 8, y + 8, x0 - 5, y + rh - 8), fill=BLACK)
        _glyph(g, it["source"], x0 + 16, y + rh // 2 - 2, ink)
        tx = x0 + 44
        age = _age(it["age_s"])
        aw = g.text_size(age, af)[0]
        g.text((x1, y + 10), age, af, DARK, anchor="ra")
        g.text((tx, y + 6), g.fit_text(it["from"] or SOURCE_LABEL[it["source"]], x1 - tx - aw - 12, ff), ff, ink)
        g.text((tx, y + 6 + 22), g.fit_text(it["text"], x1 - tx, tf), tf, DARK if not unread else BLACK)
        y += rh
    return len(shown)


def _list(g: Glass, d: Dict[str, Any], y0: int, y1: int, rh: int, columns: int) -> None:
    items = d["items"]
    if not items:
        g.text((MARGIN, y0 + 8), "All quiet.", sans(22, 500), DARK)
        g.text((MARGIN, y0 + 40), "Nothing new on any rail.", sans(16, 400), DARK)
        return
    per_col = max(1, (y1 - y0 - 18) // rh)
    columns = 1 if len(items) <= per_col else columns
    gap = 40
    cw = (g.w - 2 * MARGIN - gap * (columns - 1)) // columns
    drawn = 0
    for c in range(columns):
        x0 = MARGIN + c * (cw + gap)
        if c:
            g.vline(x0 - gap // 2, y0 + 4, y1 - 4, LIGHT)
        drawn += _rows(g, items[drawn:], (x0, y0, x0 + cw, y1 - 18), rh)
    rest = len(items) - drawn
    if rest > 0:
        g.label((MARGIN + 44, y1 - 14), f"and {rest} more", 13, DARK)


@component(
    "notifications", "Inbox",
    "One inbox: DMs, calendar nudges, fleet events, GitHub, system — unread-first rows with "
    "source glyphs, sender, age and a one-line preview.",
    params={"items": "list of {source,from,text,ts|age_s,unread} (push path)",
            "demo": "1 → fixture", "live": "0 → fixture, default: dashboard rails", "limit": "max rows (12)",
            "title": "header (Inbox)"},
    fetch=notif.fetch,
    native_hint="type:'inbox' {title, n_new, rows:[{src:u8, from, text≤80, age_s, unread}] ≤10} ≈ 1 KB; partial refresh per row on new item",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        y = _header(g, d, MARGIN, True)
        g.hairline(MARGIN, y, g.w - MARGIN, DARK)
        _list(g, d, y + 6, g.h - MARGIN, 56, columns=2)
    else:
        g = Glass(PORTRAIT)
        y = _header(g, d, MARGIN, False)
        g.hairline(MARGIN, y, g.w - MARGIN, DARK)
        _list(g, d, y + 6, g.h - MARGIN, 60, columns=1)
    return g.snap()
