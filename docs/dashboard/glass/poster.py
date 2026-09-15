"""
🅰 poster — the typographic set: one big thing, one small line.

Three faces from one renderer. `word`: the word as large as the width allows
(auto-fit from 128 px down), pronunciation and part of speech in mono, a
hairline, the definition wrapped at 26 px, an etymology note in DARK. `line`:
a line of poetry set ragged-right at the largest size that fits in three lines,
an em-dash attribution. `text`: whatever the owner sends — a mantra, a status,
a sign for the door — with optional kicker/sub/by. The e-ink screensaver set:
no data, no clock competition; the clock is small in the corner.
Data: glass.adapters.poster (fixture rotated by day; text mode is params only).
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import poster as pd
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _fit_lines(g: Glass, s: str, max_w: int, max_h: int, weight: int, lo: int, hi: int,
               max_lines: int, leading: float = 1.08) -> Tuple[int, List[str]]:
    """Largest size in [lo, hi] at which s wraps into ≤ max_lines within max_h."""
    for px in range(hi, lo - 1, -4):
        f = sans(px, weight)
        lines = g.wrap(s, max_w, f, max_lines=max_lines)
        if " ".join(lines) == " ".join(s.split()) and len(lines) * int(px * leading) <= max_h:
            return px, lines
    return lo, g.wrap(s, max_w, sans(lo, weight), max_lines=max_lines)


def _corner(g: Glass, d: Dict[str, Any]) -> None:
    if d.get("kicker"):
        g.label((MARGIN, MARGIN), d["kicker"], 13, DARK)
    g.text((g.w - MARGIN, MARGIN - 2), d["now"], mono(16, 500), DARK, anchor="ra")


def _word(g: Glass, d: Dict[str, Any]) -> None:
    _corner(g, d)
    w = g.w - 2 * MARGIN
    px, lines = _fit_lines(g, d["text"], w, int(g.h * 0.36), 700, 48, 128 if g.w > g.h else 104, max_lines=1)
    y = MARGIN + 44
    f = sans(px, 700)
    g.text((MARGIN - 4, y), lines[0], f, BLACK)
    y += int(px * 1.24)
    # neither bundled font has IPA glyphs (boxes at /ˈpɛt.rɪ.kɔːr/) → fixtures carry a respelling, PET·ri·kor
    pron = d.get("pron", "")
    g.text((MARGIN, y), pron, sans(18, 500), DARK)
    pw = g.text_size(pron, sans(18, 500))[0] + (18 if pron else 0)
    g.label((MARGIN + pw, y + 3), d.get("pos", ""), 12, DARK)
    y += 34
    g.hairline(MARGIN, y, g.w - MARGIN, BLACK)
    y += 18
    dpx = 30 if g.w > g.h else 24
    for ln in g.wrap(d["sub"], w, sans(dpx, 500), max_lines=4):
        g.text((MARGIN, y), ln, sans(dpx, 500), BLACK)
        y += int(dpx * 1.3)
    if d.get("by"):
        y += 10
        for ln in g.wrap(d["by"], w, sans(16, 500), max_lines=2):
            g.text((MARGIN, y), ln, sans(16, 500), DARK)
            y += 22
    g.text((g.w - MARGIN, g.h - MARGIN - 14), d["date_label"], sans(13, 500), DARK, anchor="ra")


def _line(g: Glass, d: Dict[str, Any]) -> None:
    _corner(g, d)
    w = g.w - 2 * MARGIN
    px, lines = _fit_lines(g, d["text"], w, g.h - 2 * MARGIN - 130, 600, 28, 72 if g.w > g.h else 60, max_lines=3 if g.w > g.h else 5, leading=1.15)
    f = sans(px, 600)
    total = len(lines) * int(px * 1.15)
    y = (g.h - total) // 2 - 24
    for ln in lines:
        g.text((MARGIN - 2, y), ln, f, BLACK)
        y += int(px * 1.15)
    y += 14
    g.hairline(MARGIN, y, MARGIN + 60, BLACK)
    y += 12
    g.text((MARGIN, y), f"— {d.get('by', '')}", sans(18, 600), BLACK)
    if d.get("src"):
        g.text((MARGIN, y + 26), d["src"], sans(14, 500), DARK)
    g.text((g.w - MARGIN, g.h - MARGIN - 14), d["date_label"], sans(13, 500), DARK, anchor="ra")


def _text(g: Glass, d: Dict[str, Any]) -> None:
    _corner(g, d)
    w = g.w - 2 * MARGIN
    px, lines = _fit_lines(g, d["text"] or "…", w, g.h - 2 * MARGIN - 110, 700, 28, 140 if g.w > g.h else 110, max_lines=3 if g.w > g.h else 5, leading=1.05)
    f = sans(px, 700)
    total = len(lines) * int(px * 1.05)
    extra = 30 if d.get("sub") else 0
    y = (g.h - total - extra) // 2 - 10
    for ln in lines:
        g.text((MARGIN - 4, y), ln, f, BLACK)
        y += int(px * 1.05)
    if d.get("sub"):
        y += int(px * 0.2) + 6      # clear the descenders before the rule
        g.hairline(MARGIN, y, MARGIN + 60, BLACK)
        y += 12
        for ln in g.wrap(d["sub"], w, sans(20, 500), max_lines=2):
            g.text((MARGIN, y), ln, sans(20, 500), DARK)
            y += 27
    if d.get("by"):
        g.text((MARGIN, g.h - MARGIN - 16), f"— {d['by']}", sans(15, 600), DARK)
    g.text((g.w - MARGIN, g.h - MARGIN - 14), d["date_label"], sans(13, 500), DARK, anchor="ra")


@component(
    "poster", "Poster",
    "Typographic set: word of the day (auto-fit word, pronunciation, definition, note), a line of "
    "poetry with attribution, or the owner's own text. The screensaver family.",
    params={"mode": "word|line|text", "text": "text mode", "sub": "second line", "by": "attribution", "kicker": "small label", "index": "pin an entry", "date": "YYYY-MM-DD"},
    fetch=pd.fetch,
    native_hint="type:'poster' {kicker, text, sub, by} ≤ 300 B — the firmware already has a text card; this adds auto-fit + wrap rules",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    g = Glass(LANDSCAPE if orientation == "landscape" else PORTRAIT)
    {"word": _word, "line": _line, "text": _text}.get(d["mode"], _text)(g, d)
    return g.snap()
