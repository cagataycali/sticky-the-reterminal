"""
🖌️ glass.canvas — drawing primitives in the panel's own four grays.

Rules baked in (docs/dashboard/glass/README.md "Design language"):
  • text is drawn DIRECTLY in palette values — never anti-aliased (that would
    dither into speckle): Pillow renders with mode "1" masks (no AA) via
    fontmode "1" on the draw object.
  • four values only: BLACK 0 · DARK 85 · LIGHT 170 · WHITE 255 — the exact
    luminances dither.py's quantizer maps to codes 0..3, so a canvas built
    here survives _pack_4gray with ZERO error diffusion.
  • Inter for words, JetBrains Mono for numerals/time (variable fonts with a
    wght axis; weights 400/500/600/700/800 via set_variation_by_axes).
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

BLACK, DARK, LIGHT, WHITE = 0, 85, 170, 255
PALETTE = (BLACK, DARK, LIGHT, WHITE)
MARGIN = 24                      # generous margins, ≥24 px everywhere

FONTS = Path(__file__).resolve().parent / "fonts"
_INTER = FONTS / "Inter.ttf"
_MONO = FONTS / "JetBrainsMono.ttf"


@lru_cache(maxsize=128)
def font(px: int, weight: int = 400, mono: bool = False) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(_MONO if mono else _INTER), px)
    try:
        f.set_variation_by_axes([weight])
    except OSError:
        pass
    return f


def sans(px: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    return font(px, weight, mono=False)


def mono(px: int, weight: int = 500) -> ImageFont.FreeTypeFont:
    return font(px, weight, mono=True)


class Glass:
    """An L-mode canvas with palette-only drawing helpers."""

    def __init__(self, size: Tuple[int, int], bg: int = WHITE):
        self.im = Image.new("L", size, bg)
        self.d = ImageDraw.Draw(self.im)
        self.d.fontmode = "1"            # NO anti-aliasing: text lands on palette values
        self.w, self.h = size

    # ── text ─────────────────────────────────────────────────────────────
    def text_size(self, s: str, f: ImageFont.FreeTypeFont) -> Tuple[int, int]:
        l, t, r, b = self.d.textbbox((0, 0), s, font=f)
        return r - l, b - t

    def text(self, xy: Tuple[int, int], s: str, f: ImageFont.FreeTypeFont,
             fill: int = BLACK, anchor: str = "la") -> Tuple[int, int, int, int]:
        """Draw and return the ink bbox. anchor uses Pillow's 2-char scheme
        (l/m/r + a/m/s/d): 'ra' = right-top, 'ms' = centered baseline…"""
        self.d.text(xy, s, font=f, fill=fill, anchor=anchor)
        return self.d.textbbox(xy, s, font=f, anchor=anchor)

    def label(self, xy: Tuple[int, int], s: str, px: int = 18, fill: int = DARK,
              anchor: str = "la", tracking: int = 2) -> int:
        """Small-caps style label: uppercase, semibold, letterspaced, mid gray.
        Returns the x after the last glyph."""
        f = sans(px, 600)
        x, y = xy
        s = s.upper()
        if anchor[0] in "mr":
            total = sum(self.text_size(ch, f)[0] for ch in s) + tracking * (len(s) - 1)
            x = x - total // 2 if anchor[0] == "m" else x - total
        for ch in s:
            self.d.text((x, y), ch, font=f, fill=fill, anchor="l" + anchor[1])
            x += self.text_size(ch, f)[0] + tracking
        return x

    def fit_text(self, s: str, max_w: int, f: ImageFont.FreeTypeFont, ellipsis: str = "…") -> str:
        if self.text_size(s, f)[0] <= max_w:
            return s
        while s and self.text_size(s + ellipsis, f)[0] > max_w:
            s = s[:-1]
        return s.rstrip() + ellipsis

    def wrap(self, s: str, max_w: int, f: ImageFont.FreeTypeFont, max_lines: int = 2) -> List[str]:
        words, lines, cur = s.split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if self.text_size(trial, f)[0] <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
            if len(lines) == max_lines:
                break
        if len(lines) < max_lines and cur:
            lines.append(cur)
        if len(lines) == max_lines and " ".join(lines) != s.strip():
            lines[-1] = self.fit_text(lines[-1] + " " + " ".join(words[len(" ".join(lines).split()):]), max_w, f)
        return lines

    # ── rules & shapes ───────────────────────────────────────────────────
    def hairline(self, x0: int, y: int, x1: int, fill: int = LIGHT) -> None:
        self.d.line((x0, y, x1, y), fill=fill, width=1)

    def vline(self, x: int, y0: int, y1: int, fill: int = LIGHT) -> None:
        self.d.line((x, y0, x, y1), fill=fill, width=1)

    def rect(self, box: Tuple[int, int, int, int], fill: Optional[int] = None,
             outline: Optional[int] = None, width: int = 1, radius: int = 0) -> None:
        if radius:
            self.d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
        else:
            self.d.rectangle(box, fill=fill, outline=outline, width=width)

    def circle(self, cx: int, cy: int, r: int, fill: Optional[int] = None,
               outline: Optional[int] = None, width: int = 1) -> None:
        self.d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill, outline=outline, width=width)

    def dots(self, x0: int, y: int, x1: int, step: int = 6, fill: int = LIGHT) -> None:
        for x in range(x0, x1, step):
            self.d.point((x, y), fill=fill)

    # ── data glyphs ──────────────────────────────────────────────────────
    def sparkline(self, box: Tuple[int, int, int, int], values: Sequence[float],
                  fill: int = BLACK, width: int = 3, baseline: bool = True,
                  area: Optional[int] = LIGHT, mark_last: bool = True) -> None:
        """Polyline through values scaled into box; optional light area fill."""
        x0, y0, x1, y1 = box
        vals = [float(v) for v in values if v is not None]
        if len(vals) < 2:
            return
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-9:
            hi, lo = hi + 1, lo - 1
        n = len(vals)
        pts = []
        for i, v in enumerate(vals):
            x = x0 + round(i * (x1 - x0) / (n - 1))
            y = y1 - round((v - lo) / (hi - lo) * (y1 - y0))
            pts.append((x, y))
        if area is not None:
            self.d.polygon([(x0, y1)] + pts + [(x1, y1)], fill=area)
        if baseline:
            self.hairline(x0, y1, x1, LIGHT)
        self.d.line(pts, fill=fill, width=width, joint="curve")
        if mark_last:
            lx, ly = pts[-1]
            self.circle(lx, ly, width + 2, fill=WHITE, outline=fill, width=2)

    def bars(self, box: Tuple[int, int, int, int], values: Sequence[float],
             fill: int = DARK, gap: int = 3, vmax: Optional[float] = None) -> None:
        x0, y0, x1, y1 = box
        n = len(values)
        if n == 0:
            return
        vmax = vmax or max(max(values), 1e-9)
        bw = max(1, (x1 - x0 - gap * (n - 1)) // n)
        for i, v in enumerate(values):
            bx = x0 + i * (bw + gap)
            bh = round((v / vmax) * (y1 - y0))
            if bh > 0:
                self.d.rectangle((bx, y1 - bh, bx + bw - 1, y1), fill=fill)

    def ring(self, cx: int, cy: int, r: int, fraction: float, width: int = 14,
             fill: int = BLACK, track: int = LIGHT) -> None:
        """Progress ring, 12 o'clock start, clockwise."""
        box = (cx - r, cy - r, cx + r, cy + r)
        self.d.arc(box, 0, 360, fill=track, width=width)
        if fraction > 0:
            self.d.arc(box, -90, -90 + 360 * min(1.0, fraction), fill=fill, width=width)

    # ── output ───────────────────────────────────────────────────────────
    def snap(self) -> Image.Image:
        """Force every pixel onto the palette (shapes drawn with palette fills
        already are; this is the belt for the braces) and return the image."""
        return snap_palette(self.im)


def snap_palette(im: Image.Image) -> Image.Image:
    lut = [min(PALETTE, key=lambda p: abs(p - v)) for v in range(256)]
    return im.convert("L").point(lut)


def palette_values(im: Image.Image) -> set:
    return set(im.convert("L").getcolors(256) and [c for _, c in im.convert("L").getcolors(256)] or [])


def paste_dithered(g: Glass, photo: Image.Image, box: Tuple[int, int, int, int]) -> None:
    """Photos/icons are the ONLY thing we dither. Fit into box, FS-dither to
    the 4-gray palette, paste."""
    from dither import _PAL  # the same palette image the rail packs with
    x0, y0, x1, y1 = box
    fitted = photo.convert("L").copy()
    fitted.thumbnail((x1 - x0, y1 - y0), Image.LANCZOS)
    q = fitted.convert("RGB").quantize(palette=_PAL, dither=Image.Dither.FLOYDSTEINBERG)
    q = q.convert("RGB").convert("L")
    g.im.paste(q, (x0 + (x1 - x0 - fitted.width) // 2, y0 + (y1 - y0 - fitted.height) // 2))
