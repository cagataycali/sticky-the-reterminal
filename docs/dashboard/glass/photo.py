"""
📷 photo — a picture on paper.

Floyd–Steinberg dithered to the panel's four grays (the only honest way to put a
photograph on this glass — thresholding loses the sky, ordered dither shows its
grid at arm's length). Two modes: `cover` — the photo fills the card, a white
caption band at the bottom (title 20 px, credit in DARK); `frame` — the photo
matted inside a hairline frame with the caption beneath, the way a gallery
labels a print. Portrait crops toward `focus`.
Data: glass.adapters.photo (https url / docs path / Ansel Adams fixture).
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import photo as ph
from .canvas import BLACK, DARK, LIGHT, MARGIN, PALETTE, WHITE, Glass, mono, sans

_PAL = Image.new("P", (1, 1))
_PAL.putpalette(sum(([v, v, v] for v in PALETTE), []) + [0, 0, 0] * 252)


def dither4(gray: Image.Image) -> Image.Image:
    """Floyd–Steinberg onto exactly the four panel levels; returns mode L in palette values."""
    q = gray.convert("RGB").quantize(palette=_PAL, dither=Image.Dither.FLOYDSTEINBERG)
    return q.convert("L")          # palette entries are the four gray values, so L == panel levels


def cover(gray: Image.Image, size: Tuple[int, int], focus: Tuple[float, float]) -> Image.Image:
    w, h = size
    s = max(w / gray.width, h / gray.height)
    im = gray.resize((max(w, round(gray.width * s)), max(h, round(gray.height * s))), Image.LANCZOS)
    fx, fy = focus
    x0 = int((im.width - w) * min(1.0, max(0.0, fx)))
    y0 = int((im.height - h) * min(1.0, max(0.0, fy)))
    return im.crop((x0, y0, x0 + w, y0 + h))


def contain(gray: Image.Image, size: Tuple[int, int]) -> Image.Image:
    w, h = size
    s = min(w / gray.width, h / gray.height)
    return gray.resize((max(1, round(gray.width * s)), max(1, round(gray.height * s))), Image.LANCZOS)


def _caption(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, dark_credit: bool = True) -> None:
    meta = f"{d['w']}×{d['h']} · 4 gray"
    mw = g.text_size(meta, mono(11, 500))[0] + 16
    if d["caption"]:
        g.text((x, y), g.fit_text(d["caption"], w - mw, sans(20, 700)), sans(20, 700), BLACK)
    if d["by"]:
        g.text((x, y + (28 if d["caption"] else 4)), g.fit_text(d["by"], w - mw, sans(14, 500)), sans(14, 500), DARK if dark_credit else BLACK)
    g.text((x + w, y + 4), meta, mono(11, 500), DARK, anchor="ra")


@component(
    "photo", "Photo",
    "A photograph Floyd–Steinberg-dithered to the four panel grays — full-bleed with a caption band, "
    "or matted in a hairline frame with a gallery label.",
    params={"url": "https image", "path": "file under docs/", "caption": "", "by": "credit", "mode": "cover|frame", "focus": "x,y 0..1"},
    fetch=ph.fetch,
    native_hint="the whole point is the 96 000-byte frame — there is no smaller native form; ship the raw",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    size = LANDSCAPE if orientation == "landscape" else PORTRAIT
    g = Glass(size)
    band = 64 if d["caption"] or d["by"] else 0
    if d["mode"] == "frame":
        pad = MARGIN + 6
        box_w, box_h = g.w - 2 * pad, g.h - 2 * pad - (band + 6 if band else 0)
        im = dither4(contain(d["image"], (box_w - 8, box_h - 8)))
        x0 = pad + (box_w - im.width) // 2
        y0 = pad + (box_h - im.height) // 2
        g.rect((x0 - 4, y0 - 4, x0 + im.width + 3, y0 + im.height + 3), outline=BLACK, width=1)
        g.im.paste(im, (x0, y0))
        if band:
            _caption(g, d, pad, g.h - pad - band + 18, box_w)
    else:
        im = dither4(cover(d["image"], (g.w, g.h - band), d["focus"]))
        g.im.paste(im, (0, 0))
        if band:
            g.hairline(0, g.h - band, g.w, DARK)
            _caption(g, d, MARGIN, g.h - band + 14, g.w - 2 * MARGIN)
    return g.snap()
