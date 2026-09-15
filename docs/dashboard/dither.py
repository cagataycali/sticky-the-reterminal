"""
🖼️ dither.py — image → reTerminal Sticky e-ink frames.

Turns any Pillow-readable image into the raw framebuffer formats the
firmware's SSD1677 driver blits, at EXACT byte counts:

  • 1-bit  (mono):   800×480 / 8      = 48000 bytes
  • 4-gray (2bpp):   800×480 × 2 / 8  = 96000 bytes

Both in landscape (800×480 canvas) and portrait (composed on a 480×800
canvas, then rotated 90° CW into the panel-native 800×480 raster — the
panel only has one native orientation; "portrait" is a composition
choice, not a different buffer size).

FORMAT — CONFIRMED against the firmware's own Canvas on 2026-08-26 (fw
0.16.5-m14), not assumed from a datasheet:
  canvas.cpp:12   stride_((width + 3U) / 4U)      → 4 px/byte, 200 B/row
  canvas.cpp:105  shift = (3 - (x & 0x03)) * 2    → MSB-first
  canvas.h:7-10   Black=0 DarkGray=1 LightGray=2 White=3
  sticky_display.cpp convert_gray4_to_monochrome_in_place:
                  gray >= 2 → bit (7 - bit)       → mono bit 1 = WHITE, MSB-first
  driver declares SEEED_EPAPER_PIXEL_FORMAT_MONO1_MSB
gray4 is ONE interleaved 2bpp buffer, NOT two 1bpp planes.
DO NOT pre-rotate 180°: sticky_display_refresh() applies
rotate_framebuffer_180() + reverse_pixel_order() itself. These bytes are
CANVAS-space, and the driver does the panel-mounting compensation.

(historical, kept so the diff reads honestly):
  1-bit : row-major, MSB-first, 8 px/byte, bit 1 = WHITE (PIL "1" tobytes
          convention, matches typical SSD1677 full-refresh LUT polarity).
  4-gray: row-major, MSB-first, 4 px/byte, 2 bits/px,
          0b00 = BLACK · 0b01 dark gray · 0b10 light gray · 0b11 = WHITE.
  If the driver wants inverted polarity or plane-split (SSD1677 gray4 is
  often two 1bpp planes), say so and this module gains a
  flag — the math is one XOR / one shuffle away.

Dithering is Floyd-Steinberg in both depths (Pillow's default error
diffusion for convert("1") and quantize(dither=FLOYDSTEINBERG)).
Letterboxing pads with WHITE — on e-ink, white is "no ink", black bars
would look like a hardware bezel and burn ghosting for nothing.
"""
from __future__ import annotations

import hashlib
import io
import json
import time
from pathlib import Path
from typing import Any, Dict

from PIL import Image

LANDSCAPE = (800, 480)
PORTRAIT = (480, 800)
BYTES_1BIT = 800 * 480 // 8          # 48000
BYTES_4GRAY = 800 * 480 * 2 // 8     # 96000
GRAY_LEVELS = (0, 85, 170, 255)      # 2bpp value 0..3 → luminance

FRAMES_DIR = Path(__file__).resolve().parent / ".frames"
FRAMES_DIR.mkdir(exist_ok=True)

# 4-entry grayscale palette image for quantize()
_PAL = Image.new("P", (1, 1))
_PAL.putpalette(sum(([g, g, g] for g in GRAY_LEVELS), []) + [0] * (768 - 12))


def _letterbox(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Fit im into size keeping aspect, centered on a white canvas."""
    canvas = Image.new("L", size, 255)
    fitted = im.copy()
    fitted.thumbnail(size, Image.LANCZOS)
    canvas.paste(fitted, ((size[0] - fitted.width) // 2,
                          (size[1] - fitted.height) // 2))
    return canvas


def _pack_1bit(im_l: Image.Image) -> bytes:
    """Floyd-Steinberg to mono, packed 8px/byte MSB-first (PIL native)."""
    out = im_l.convert("1").tobytes()   # FS dither is convert("1")'s default
    assert len(out) == BYTES_1BIT, f"1bit pack: {len(out)} != {BYTES_1BIT}"
    return out


def _pack_4gray(im_l: Image.Image) -> bytes:
    """Floyd-Steinberg to 4 gray levels, packed 4px/byte MSB-first."""
    q = im_l.convert("RGB").quantize(palette=_PAL, dither=Image.Dither.FLOYDSTEINBERG)
    px = q.tobytes()                    # one palette index (0..3) per byte
    import numpy as np
    a = np.frombuffer(px, dtype=np.uint8).reshape(-1, 4)
    packed = ((a[:, 0] << 6) | (a[:, 1] << 4) | (a[:, 2] << 2) | a[:, 3]).astype(np.uint8)
    out = packed.tobytes()
    assert len(out) == BYTES_4GRAY, f"4gray pack: {len(out)} != {BYTES_4GRAY}"
    return out


def _preview_1bit(raw: bytes) -> Image.Image:
    return Image.frombytes("1", LANDSCAPE, raw).convert("L")


def _preview_4gray(raw: bytes) -> Image.Image:
    import numpy as np
    b = np.frombuffer(raw, dtype=np.uint8)
    px = np.empty(len(b) * 4, dtype=np.uint8)
    px[0::4] = (b >> 6) & 3
    px[1::4] = (b >> 4) & 3
    px[2::4] = (b >> 2) & 3
    px[3::4] = b & 3
    lut = np.array(GRAY_LEVELS, dtype=np.uint8)
    return Image.fromarray(lut[px].reshape(480, 800), "L")


def dither_image(data: bytes, filename: str = "upload") -> Dict[str, Any]:
    """The whole pipeline. Returns a manifest dict; frames land in .frames/<sha>/."""
    src = Image.open(io.BytesIO(data))
    src.load()
    if src.mode in ("RGBA", "LA", "PA"):  # flatten alpha onto white
        bg = Image.new("RGBA", src.size, (255, 255, 255, 255))
        bg.alpha_composite(src.convert("RGBA"))
        src = bg
    gray = src.convert("L")

    sha = hashlib.sha256(data).hexdigest()[:16]
    out_dir = FRAMES_DIR / sha
    out_dir.mkdir(exist_ok=True)

    frames: Dict[str, Any] = {}
    for orient, size in (("landscape", LANDSCAPE), ("portrait", PORTRAIT)):
        composed = _letterbox(gray, size)
        if orient == "portrait":
            # rotate 90° CW into panel-native raster: top of the portrait
            # composition ends up on the panel's right edge (flip to CCW
            # here if the fw portrait convention differs).
            composed = composed.rotate(-90, expand=True)
        assert composed.size == LANDSCAPE
        for depth, pack, preview, want in (
            ("1bit", _pack_1bit, _preview_1bit, BYTES_1BIT),
            ("4gray", _pack_4gray, _preview_4gray, BYTES_4GRAY),
        ):
            raw = pack(composed)
            name = f"{orient}_{depth}"
            (out_dir / f"{name}.bin").write_bytes(raw)
            preview(raw).save(out_dir / f"{name}.png")
            frames[name] = {
                "url": f"/frames/{sha}/{name}.bin",
                "preview": f"/frames/{sha}/{name}.png",
                "bytes": len(raw),
                "bytes_ok": len(raw) == want,
            }

    # thumbnail of the original for the gallery page
    thumb = gray.copy()
    thumb.thumbnail((200, 200))
    thumb.save(out_dir / "thumb.png")

    manifest = {
        "sha": sha,
        "filename": filename,
        "source": {"w": src.width, "h": src.height, "bytes": len(data)},
        "frames": frames,
        "ts": time.time(),
        "format_note": ("1bit: 8px/byte MSB-first, 1=white · "
                        "4gray: 4px/byte MSB-first, 0=black..3=white · "
                        "row-major 800x480 panel-native"),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def list_gallery() -> list[Dict[str, Any]]:
    items = []
    for d in FRAMES_DIR.iterdir():
        m = d / "manifest.json"
        if m.is_file():
            try:
                items.append(json.loads(m.read_text()))
            except ValueError:
                continue
    items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return items
