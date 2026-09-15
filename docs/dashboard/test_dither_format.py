#!/usr/bin/env python
"""Does dither.py still pack bytes the way the FIRMWARE unpacks them?

The dither contract (GET /api/dither) says the frame format is CONFIRMED, not
assumed. This file is what makes that word survive: the firmware's own bit
formulas are transcribed here from the C++, and the packer is checked against
them. If someone changes either side, this fails instead of the panel quietly
painting garbage — the failure mode that costs a night of "the bytes looked fine".

Sources transcribed (fw 0.16.5-m14):
  firmware/main/canvas.cpp:12    stride_((width + 3U) / 4U)
  firmware/main/canvas.cpp:104-108  index/shift/mask 2bpp write
  firmware/main/canvas.h:7-10    Black=0 DarkGray=1 LightGray=2 White=3
  firmware/main/sticky_display.cpp  convert_gray4_to_monochrome_in_place
                                    (gray >= 2 -> bit (7 - bit))

Run: ~/.tiny/pypi/bin/python test_dither_format.py
"""
from __future__ import annotations

import sys

from PIL import Image

import dither

W, H = dither.LANDSCAPE
failures: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got!r} want {want!r}")
    if not ok:
        failures.append(name)


def fw_pack_4gray_byte(codes: list[int]) -> int:
    """canvas.cpp:104-108, transcribed. 4 pixel codes -> one byte."""
    byte = 0
    for x, code in enumerate(codes):
        shift = (3 - (x & 0x03)) * 2
        mask = 0x03 << shift
        byte = (byte & ~mask) | (code << shift)
    return byte


def fw_stride() -> int:
    """canvas.cpp:12, transcribed."""
    return (W + 3) // 4


print("4-gray packing (2bpp interleaved, MSB-first)")
im = Image.new("L", (W, H), 0)
for x, lvl in enumerate(dither.GRAY_LEVELS):
    im.putpixel((x, 0), lvl)          # x=0..3 get Black, DarkGray, LightGray, White
raw4 = dither._pack_4gray(im)
check("byte 0 matches firmware formula", raw4[0], fw_pack_4gray_byte([0, 1, 2, 3]))
check("total size", len(raw4), dither.BYTES_4GRAY)
check("bytes per row == firmware stride", len(raw4) // H, fw_stride())

print("4-gray polarity (canvas.h enum)")
white = Image.new("L", (W, H), 255)
black = Image.new("L", (W, H), 0)
check("all-WHITE source -> 0b11111111 (White=3)", dither._pack_4gray(white)[0], 0xFF)
check("all-BLACK source -> 0b00000000 (Black=0)", dither._pack_4gray(black)[0], 0x00)

print("1-bit packing (MONO1_MSB, bit 1 = WHITE)")
raw1 = dither._pack_1bit(white)
check("all-WHITE source -> 0xFF", raw1[0], 0xFF)
check("all-BLACK source -> 0x00", dither._pack_1bit(black)[0], 0x00)
check("total size", len(raw1), dither.BYTES_1BIT)
check("bytes per row", len(raw1) // H, W // 8)

print("aspect policy: LETTERBOX, never crop")
square = Image.new("L", (900, 900), 0)          # all ink, square
box = dither._letterbox(square, dither.LANDSCAPE)
px = box.load()
check("left column padded WHITE", px[0, H // 2], 255)
check("right column padded WHITE", px[W - 1, H // 2], 255)
check("centre preserved (not cropped away)", px[W // 2, H // 2], 0)
check("canvas is panel-sized", box.size, dither.LANDSCAPE)

print("\n4-gray round-trip yields ONLY the declared levels")
import numpy as np  # noqa: E402  (kept local: dither imports it lazily too)

lum = sorted(np.unique(np.asarray(dither._preview_4gray(raw4))).tolist())
# raw4 is the planted probe: black canvas with the 4 declared levels in row 0,
# so unpacking must give back exactly those four and nothing invented.
check("planted levels survive the round-trip", lum, list(dither.GRAY_LEVELS))
mixed = dither._letterbox(Image.new("L", (900, 900), 128), dither.LANDSCAPE)
lum2 = sorted(np.unique(np.asarray(dither._preview_4gray(dither._pack_4gray(mixed)))).tolist())
check("dithered mid-grey uses only declared levels",
      all(v in dither.GRAY_LEVELS for v in lum2), True)

print()
if failures:
    print(f"FAILED ({len(failures)}): {', '.join(failures)}")
    sys.exit(1)
print("all format checks passed — dither.py agrees with the firmware's Canvas")
