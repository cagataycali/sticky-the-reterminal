"""
♪ playing — the record sleeve on the desk.

What the desk is listening to, printed the way a sleeve is: the artwork dithered
to four grays fills a square with a one-pixel frame, the title set large enough
to read from the door, artist under it, album in the quiet gray. The progress
line is the only live element — elapsed on the left, time remaining on the
right, a black mark where the needle is. State is a word plus a glyph (▶ ▮▮),
never a coloured button. Shuffle/repeat/volume sit in the footer as small facts.
Data: glass.adapters.playing (Spotify desktop app, read-only; pushed params; fixture).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import playing as pl
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans
from .photo import cover, dither4


def _mmss(s: int) -> str:
    return f"{s // 60}:{s % 60:02d}"


def _art(g: Glass, d: Dict[str, Any], x: int, y: int, size: int) -> None:
    if d.get("art"):
        try:
            im = Image.open(d["art"]).convert("L")
            g.im.paste(dither4(cover(im, (size, size), (0.5, 0.5))), (x, y))
        except Exception:
            d["art"] = None
    if not d.get("art"):
        g.rect((x, y, x + size, y + size), fill=LIGHT)
        g.text((x + size // 2, y + size // 2), "♪", sans(size // 3, 700), DARK, anchor="mm")
    g.rect((x, y, x + size - 1, y + size - 1), outline=BLACK, width=1)


def _state_glyph(g: Glass, x: int, cy: int, state: str) -> int:
    if state == "playing":
        g.d.polygon([(x, cy - 6), (x, cy + 6), (x + 10, cy)], fill=BLACK)
        return 16
    if state == "paused":
        g.rect((x, cy - 6, x + 3, cy + 6), fill=BLACK)
        g.rect((x + 6, cy - 6, x + 9, cy + 6), fill=BLACK)
        return 16
    g.rect((x, cy - 5, x + 10, cy + 5), fill=DARK)
    return 16


def _progress(g: Glass, d: Dict[str, Any], x: int, y: int, w: int) -> None:
    g.text((x, y), _mmss(d["position_s"]), mono(13, 500), BLACK)
    rem = max(0, d["duration_s"] - d["position_s"])
    g.text((x + w, y), f"−{_mmss(rem)}", mono(13, 500), DARK, anchor="ra")
    by = y + 24
    g.rect((x, by - 1, x + w, by + 1), fill=LIGHT)
    px = x + round(w * max(0.0, min(1.0, d["progress"])))
    g.rect((x, by - 1, px, by + 1), fill=DARK)
    g.rect((px - 2, by - 7, px + 2, by + 7), fill=BLACK)


def _facts(g: Glass, d: Dict[str, Any], x: int, y: int, w: int) -> None:
    items = [("volume", f"{d['volume']} %"), ("shuffle", "on" if d["shuffle"] else "off"),
             ("repeat", d["repeat"] if d["repeat"] != "context" else "on"), ("source", d["source"])]
    cw = w / len(items)
    for i, (k, v) in enumerate(items):
        cx = round(x + i * cw)
        g.text((cx, y), k, sans(11, 500), DARK)
        g.text((cx, y + 14), g.fit_text(v, int(cw) - 8, mono(13, 700)), mono(13, 700), BLACK)


def _text_block(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, title_px: int) -> int:
    state = d["state"]
    adv = _state_glyph(g, x, y + 8, state)
    g.text((x + adv, y), state.upper() if state in ("playing", "paused") else "STOPPED", sans(12, 700), BLACK)
    g.text((x + w, y), d["at"] + ("  · demo" if d["demo"] else ""), mono(12, 500), DARK, anchor="ra")
    yy = y + 30
    tf = sans(title_px, 700)
    lines = g.wrap(d["track"], w, tf, 2)
    for ln in lines:
        g.text((x, yy), ln, tf, BLACK)
        yy += title_px + 6
    yy += 4
    g.text((x, yy), g.fit_text(d["artist"], w, sans(20, 500)), sans(20, 500), BLACK)
    yy += 30
    if d["album"] and d["album"] != d["track"]:
        g.text((x, yy), g.fit_text(d["album"], w, sans(14, 500)), sans(14, 500), DARK)
        yy += 24
    return yy


@component(
    "playing", "Now playing",
    "The record sleeve on the desk: artwork dithered to four grays in a framed square, title large, artist, album in "
    "the quiet gray; a progress line with elapsed / remaining and a black needle mark; state as a word plus ▶ / ▮▮; "
    "shuffle, repeat, volume, source as footer facts.",
    params={"track": "push a track (with artist, album, duration_s, position_s, state, art_url)", "demo": "1 = fixture"},
    fetch=pl.fetch,
    native_hint="type:'playing' {track, artist, progress, state} + `image <art url>` ≈ 120 B; the sleeve is the only heavy part",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        size = g.h - 2 * MARGIN
        _art(g, d, MARGIN, MARGIN, size)
        x = MARGIN + size + 32
        w = g.w - MARGIN - x
        yy = _text_block(g, d, x, MARGIN, w, 30)
        _progress(g, d, x, max(yy + 16, g.h - MARGIN - 118), w)
        g.hairline(x, g.h - MARGIN - 48, x + w, LIGHT)
        _facts(g, d, x, g.h - MARGIN - 36, w)
    else:
        g = Glass(PORTRAIT)
        size = g.w - 2 * MARGIN
        _art(g, d, MARGIN, MARGIN + 30, size)
        x, w = MARGIN, size
        g.text((x, MARGIN - 4), "Now playing", sans(20, 700), BLACK)
        yy = _text_block(g, d, x, MARGIN + 30 + size + 20, w, 28)
        _progress(g, d, x, max(yy + 10, g.h - MARGIN - 112), w)
        g.hairline(x, g.h - MARGIN - 46, x + w, LIGHT)
        _facts(g, d, x, g.h - MARGIN - 34, w)
    return g.snap()
