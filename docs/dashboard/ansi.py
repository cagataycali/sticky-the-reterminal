"""
🦆 ansi.py — source (4): the ANSI TERMINAL TWIN.

`curl sticky.cagatay.my/duck` — the demo that markets the thesis. The SAME
frame sequences /api/frames serves to the device (rendered by frames.py,
stored as grayscale previews next to the exact-size .raw files) are
downsampled to ~100×30 terminal cells and emitted as 256-color-gray
HALF-BLOCK art (▀ carries two pixel rows: fg = top, bg = bottom), one
frame per manifest interval, looped up to the same 60 s cap the device
enforces, then closed with a one-line signature.

Nothing here is a second renderer: we read the <n>.png previews the
sequence already owns, so the terminal shows byte-for-byte what the
e-ink would blit — one renderer, N transports. ANSI strings are cached per (seq_id, cols) in memory; previews
are content-addressed and immutable, so the cache never goes stale.

The duck itself is authored IN the anim grammar (scene ops of rect +
ellipse primitives — charm over fidelity) and rendered through the same
render_sequence() as any owner-posted spec: /duck is not a special
pipeline, it is a public bookmark to one public sequence.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image

import frames as sticky_frames

# terminal raster: 100 cols × 30 half-block rows = 100×60 px, ~the 800×480
# panel's aspect (1.667) at terminal scale.
COLS = 100
ROWS = 30

STREAM_CAP_S = 60.0            # same law as the device: mandatory cap
SIGNATURE = "rendered by tiny \u2014 sticky.cagatay.my"

_CSI = "\x1b["
CLEAR = _CSI + "2J" + _CSI + "H"          # clear screen + home, between frames
HIDE_CURSOR = _CSI + "?25l"
SHOW_CURSOR = _CSI + "?25h"
RESET = _CSI + "0m"

_cache: Dict[Tuple[str, int], Tuple[List[str], int]] = {}


def _gray256(v: int) -> int:
    """Luminance 0..255 → xterm-256 code: 16 (black), 231 (white), 232-255 ramp."""
    if v < 8:
        return 16
    if v > 247:
        return 231
    return 232 + min(23, (v - 8) * 24 // 240)


def _frame_to_ansi(im: Image.Image, cols: int) -> str:
    """One grayscale panel frame → half-block ANSI rows (▀, fg=top bg=bottom)."""
    rows = max(1, round(cols * im.height / im.width / 2))       # cell ≈ 1:2
    px = im.convert("L").resize((cols, rows * 2), Image.LANCZOS).load()
    out: List[str] = []
    for y in range(rows):
        line: List[str] = []
        last_fg = last_bg = -1
        for x in range(cols):
            fg, bg = _gray256(px[x, 2 * y]), _gray256(px[x, 2 * y + 1])
            if fg != last_fg or bg != last_bg:                  # elide repeats
                line.append(f"{_CSI}38;5;{fg};48;5;{bg}m")
                last_fg, last_bg = fg, bg
            line.append("\u2580")
        line.append(RESET)
        out.append("".join(line))
    return "\r\n".join(out) + "\r\n"


def sequence_to_ansi(seq_id: str, cols: int = COLS) -> Tuple[List[str], int]:
    """All frames of a stored sequence as ANSI strings + interval_ms.

    Reads the sequence's own <n>.png previews (the grayscale twin of the
    exact-size raw bytes). Raises FileNotFoundError if the sequence or a
    preview is missing — the route turns that into a 404, not a guess.
    """
    key = (seq_id, cols)
    if key in _cache:
        return _cache[key]
    manifest = sticky_frames.load_manifest(seq_id)
    if manifest is None:
        raise FileNotFoundError(f"no such sequence {seq_id}")
    seq_dir: Path = sticky_frames.ANIM_DIR / seq_id
    ansi_frames: List[str] = []
    for n in range(manifest["count"]):
        p = seq_dir / f"{n}.png"
        if not p.is_file():
            raise FileNotFoundError(f"sequence {seq_id} missing preview {n}.png")
        with Image.open(p) as im:
            ansi_frames.append(_frame_to_ansi(im, cols))
    _cache[key] = (ansi_frames, int(manifest["interval_ms"]))
    return _cache[key]


# ── the duck ────────────────────────────────────────────────────────────
# A waddling duck in the anim grammar itself: 8 scene frames, each a full
# repaint (clear + rect/ellipse shapes). Body bobs, head leans into the
# step, feet alternate. 500 ms interval → one waddle cycle = 4 s; the
# stream loops it to the cap.

def _duck_pose(bob: int, lean: int, step: int) -> Dict[str, Any]:
    """One waddle pose as a scene op. bob = body dip px, lean = forward px,
    step = 0 left foot forward / 1 right foot forward."""
    b = bob            # body/tail/wing ride the bob
    h = bob + lean     # head/beak/eye also lean forward
    feet_y = 352       # ground is fixed — feet never bob, knees do the work
    if step == 0:
        feet = [dict(kind="rect", tile=dict(x=380, y=feet_y, w=46, h=16), color="black"),
                dict(kind="rect", tile=dict(x=460, y=feet_y, w=34, h=12), color="black")]
    else:
        feet = [dict(kind="rect", tile=dict(x=368, y=feet_y, w=34, h=12), color="black"),
                dict(kind="rect", tile=dict(x=448, y=feet_y, w=46, h=16), color="black")]
    shapes = [
        # tail nub, body, wing (light gray dithers into feather texture)
        dict(kind="ellipse", tile=dict(x=282, y=208 + b, w=70, h=56), color="black"),
        dict(kind="ellipse", tile=dict(x=300, y=218 + b, w=230, h=140), color="black"),
        dict(kind="ellipse", tile=dict(x=348, y=252 + b, w=110, h=62), color="light"),
        # neck bridge, head
        dict(kind="rect", tile=dict(x=470 + lean, y=190 + h, w=44, h=70), color="black"),
        dict(kind="ellipse", tile=dict(x=452 + lean, y=138 + h, w=104, h=94), color="black"),
        # beak (two stacked slivers read as an open-ish bill), eye
        dict(kind="ellipse", tile=dict(x=546 + lean, y=168 + h, w=58, h=20), color="black"),
        dict(kind="ellipse", tile=dict(x=550 + lean, y=186 + h, w=44, h=14), color="dark"),
        dict(kind="ellipse", tile=dict(x=502 + lean, y=158 + h, w=20, h=20), color="white"),
        dict(kind="ellipse", tile=dict(x=508 + lean, y=163 + h, w=9, h=9), color="black"),
    ] + feet + [
        # ground line
        dict(kind="rect", tile=dict(x=120, y=368, w=560, h=4), color="black"),
    ]
    return dict(op="scene", clear="white", shapes=shapes)


def duck_spec() -> Dict[str, Any]:
    """The canonical duck — content-addressed, so this exact spec always
    maps to the same seq_id and render_sequence() dedupes on re-boot."""
    poses = [(0, 0, 0), (4, 3, 0), (8, 6, 1), (4, 3, 1),
             (0, 0, 1), (4, 3, 1), (8, 6, 0), (4, 3, 0)]
    return {
        "anim_id": "duck_waddle",
        "version": 1,
        "mode": "1bit_partial",
        "frame_budget_ms": 500,
        "loop": 1,                      # the ANSI stream loops it to the cap
        "background": "white",
        "frames": [_duck_pose(*p) for p in poses],
    }


def ensure_duck() -> str:
    """Render (or dedupe-load) the duck sequence; returns its seq_id."""
    return sticky_frames.render_sequence(duck_spec())["seq_id"]
