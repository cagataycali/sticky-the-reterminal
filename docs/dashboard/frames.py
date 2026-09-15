"""
🎞️ frames.py — anim-grammar JSON → server-rendered e-ink frame sequences.

Remote render rail, source (1): the dashboard rasterizes the
anim grammar (docs/research/EINK_ANIMATION.md §4 — blit/card_diff/delay/
flush ops) with Pillow at panel-native 800×480, dithers through the SAME
packers as /api/dither (dither.py — the confirmed canvas-space format),
and serves a manifest + exact-size raw frames that the firmware's
tiny_stream_play(manifest_url, interval_s, max_frames) can drain.

The device is a dumb pixel-pusher: NO grammar interpreter on-device for
this rail. Semantics rendered server-side:

  • The canvas PERSISTS across ops (e-ink semantics: a blit paints over
    what is already there). Frame N = snapshot after paint op N.
  • `blit`  — draw the glyph/text centered in its tile, then snapshot.
  • `delay` — hold: repeat the previous snapshot round(ms/interval)
    extra times (min 1). tiny_stream_play has ONE uniform interval, so
    time is quantized to it — honestly, in whole frames.
  • `flush` — server-side this is a snapshot marker only; the DEVICE owns
    refresh hygiene (its own full-wipe-every-~20-frames rule). We do not
    pretend to control the panel's waveform from here.
  • `card_diff` — apply a precomputed patch from the spec's companion
    `patches` table ({region:{x,y,w,h}, bitmap_b64: raw 1-bit MSB-first
    rows, or fill:"black"|"white"}), then snapshot. Missing patch id =
    refusal, not a guess.
  • `loop` — loop==-1 (ambient/infinite) marks the manifest
    closed_loop:true; the device replays URLs. loop>1 is materialized by
    repeating the op-derived snapshots up to the caps; over-cap refuses
    with arithmetic in the message.

CAPS (mandatory — battery holds radio+panel busy):
  MAX_FRAMES = 120 rendered frames per sequence
  MAX_DURATION_S = 60 (count × interval)
Refusals carry the reason and the numbers; nothing is silently truncated.

Frame bytes are EXACT (48000 1-bit / 96000 gray4) via dither.py's
asserting packers. Sequences land in .anim_frames/<seq_id>/ next to the
image gallery's .frames/; seq_id = sha256(spec_json)[:16] — a capability
URL and a natural dedupe (same spec → same id, re-render skipped).
"""
from __future__ import annotations

import hashlib
import json
import time
from base64 import b64decode
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from dither import (BYTES_1BIT, BYTES_4GRAY, LANDSCAPE, _letterbox, _pack_1bit,
                    _pack_4gray, _preview_1bit, _preview_4gray)

PANEL_W, PANEL_H = LANDSCAPE                      # 800, 480

MAX_FRAMES = 120                                  # hard cap
MAX_DURATION_S = 60                               # hard cap
MIN_INTERVAL_MS = 250                             # EINK_ANIMATION.md §2: <250ms impossible
DEFAULT_INTERVAL_MS = 500

ANIM_DIR = Path(__file__).resolve().parent / ".anim_frames"
ANIM_DIR.mkdir(exist_ok=True)

_MODES = {"1bit_partial": "1bit", "4gray_partial": "gray4", "full": "gray4"}

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",            # this bench (macOS)
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",  # linux deploy
)


def _font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            continue
    return ImageFont.load_default()


class SpecError(ValueError):
    """Validation refusal — message is the reason, verbatim, for the body."""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise SpecError(msg)


def _tile(obj: Any, what: str) -> Tuple[int, int, int, int]:
    _require(isinstance(obj, dict), f"{what} must be an object {{x,y,w,h}}")
    try:
        x, y, w, h = int(obj["x"]), int(obj["y"]), int(obj["w"]), int(obj["h"])
    except (KeyError, TypeError, ValueError):
        raise SpecError(f"{what} needs integer x,y,w,h")
    _require(w > 0 and h > 0, f"{what}: w and h must be > 0")
    _require(0 <= x and 0 <= y and x + w <= PANEL_W and y + h <= PANEL_H,
             f"{what}: {x},{y} {w}x{h} exceeds the 800x480 panel")
    return x, y, w, h


def _draw_blit(canvas: Image.Image, op: Dict[str, Any]) -> None:
    x, y, w, h = _tile(op.get("tile"), "blit.tile")
    glyph = op.get("glyph", op.get("text"))
    _require(isinstance(glyph, str) and glyph != "",
             "blit needs a non-empty 'glyph' (or 'text') string")
    draw = ImageDraw.Draw(canvas)
    fill = 0 if op.get("color", "black") == "black" else 255
    bg = 255 if fill == 0 else 0
    draw.rectangle((x, y, x + w - 1, y + h - 1), fill=bg)   # erase tile first
    font = _font(max(8, int(h * 0.8)))
    bbox = draw.textbbox((0, 0), glyph, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((x + (w - tw) // 2 - bbox[0], y + (h - th) // 2 - bbox[1]),
              glyph, fill=fill, font=font)


def _draw_rect(canvas: Image.Image, op: Dict[str, Any]) -> None:
    # server-side convenience op (grammar superset, documented in
    # API_CONTRACT): solid rectangle — enough for bouncing-box tests and
    # agent-composed shapes without shipping bitmaps.
    x, y, w, h = _tile(op.get("tile"), "rect.tile")
    shade = {"black": 0, "dark": 85, "light": 170, "white": 255}.get(
        op.get("color", "black"))
    _require(shade is not None, "rect.color must be black|dark|light|white")
    ImageDraw.Draw(canvas).rectangle((x, y, x + w - 1, y + h - 1), fill=shade)


_SHADES = {"black": 0, "dark": 85, "light": 170, "white": 255}


def _draw_ellipse(canvas: Image.Image, op: Dict[str, Any]) -> None:
    # server-side grammar superset op (like rect): solid ellipse inscribed
    # in its tile — ducks are made of these.
    x, y, w, h = _tile(op.get("tile"), "ellipse.tile")
    shade = _SHADES.get(op.get("color", "black"))
    _require(shade is not None, "ellipse.color must be black|dark|light|white")
    ImageDraw.Draw(canvas).ellipse((x, y, x + w - 1, y + h - 1), fill=shade)


def _draw_scene(canvas: Image.Image, op: Dict[str, Any]) -> None:
    # server-side grammar superset op: COMPOSE many primitives, snapshot
    # ONCE. Without it a multi-shape pose leaks its brush strokes as
    # frames (every paint op emits one) — a duck would be 14 frames of
    # half-drawn duck. Optional clear repaints the whole canvas first.
    clear = op.get("clear")
    if clear is not None:
        _require(clear in ("white", "black"), "scene.clear must be white|black")
        canvas.paste(255 if clear == "white" else 0, (0, 0, PANEL_W, PANEL_H))
    shapes = op.get("shapes")
    _require(isinstance(shapes, list) and len(shapes) > 0,
             "scene needs a non-empty 'shapes' array")
    for j, shape in enumerate(shapes):
        _require(isinstance(shape, dict), f"scene.shapes[{j}] must be an object")
        kind = shape.get("kind")
        if kind == "rect":
            _draw_rect(canvas, shape)
        elif kind == "ellipse":
            _draw_ellipse(canvas, shape)
        else:
            raise SpecError(f"scene.shapes[{j}]: kind must be rect|ellipse")


def _apply_patch(canvas: Image.Image, patch: Dict[str, Any], pid: str) -> None:
    x, y, w, h = _tile(patch.get("region"), f"patch '{pid}'.region")
    if "fill" in patch:
        _require(patch["fill"] in ("black", "white"),
                 f"patch '{pid}'.fill must be black|white")
        ImageDraw.Draw(canvas).rectangle(
            (x, y, x + w - 1, y + h - 1),
            fill=0 if patch["fill"] == "black" else 255)
        return
    b64 = patch.get("bitmap_b64")
    _require(isinstance(b64, str),
             f"patch '{pid}' needs bitmap_b64 or fill")
    try:
        raw = b64decode(b64, validate=True)
    except Exception:
        raise SpecError(f"patch '{pid}': bitmap_b64 is not valid base64")
    stride = (w + 7) // 8
    _require(len(raw) == stride * h,
             f"patch '{pid}': bitmap is {len(raw)} B, expected {stride * h} B "
             f"(1-bit MSB-first, {stride} B/row x {h} rows)")
    tile = Image.frombytes("1", (stride * 8, h), raw).convert("L").crop((0, 0, w, h))
    canvas.paste(tile, (x, y))


def render_sequence(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + rasterize an anim spec. Returns the stored manifest.

    Raises SpecError with a human sentence on any refusal — the route
    returns it verbatim in the 422 body.
    """
    _require(isinstance(spec, dict), "spec must be a JSON object")
    _require(spec.get("version") == 1, "version must be 1")
    anim_id = spec.get("anim_id")
    _require(isinstance(anim_id, str) and anim_id != "", "missing anim_id")
    mode = spec.get("mode", "1bit_partial")
    _require(mode in _MODES,
             "mode must be 1bit_partial | 4gray_partial | full")
    fmt = _MODES[mode]

    interval_ms = int(spec.get("frame_budget_ms", DEFAULT_INTERVAL_MS))
    _require(interval_ms >= MIN_INTERVAL_MS,
             f"frame_budget_ms {interval_ms} < {MIN_INTERVAL_MS} — faster than "
             "250ms is physically impossible on this panel (EINK_ANIMATION.md §2)")

    ops = spec.get("frames")
    _require(isinstance(ops, list) and len(ops) > 0, "frames must be a non-empty array")
    patches = (spec.get("patches") or {})
    loop = int(spec.get("loop", 1))
    _require(loop == -1 or loop >= 1, "loop must be -1 (infinite) or >= 1")

    background = spec.get("background", "white")
    _require(background in ("white", "black"), "background must be white|black")
    canvas = Image.new("L", LANDSCAPE, 255 if background == "white" else 0)

    hold_per_delay_unit = interval_ms  # quantum
    snapshots: List[Image.Image] = []

    def snap() -> None:
        if len(snapshots) >= MAX_FRAMES + 1:      # +1 so cap check reports the real count
            raise SpecError(
                f"sequence exceeds the {MAX_FRAMES}-frame cap before loop "
                f"expansion — trim ops or raise frame_budget_ms")
        snapshots.append(canvas.copy())

    for i, op in enumerate(ops):
        _require(isinstance(op, dict) and "op" in op,
                 f"frames[{i}] must be an object with an 'op'")
        kind = op["op"]
        if kind == "blit":
            _draw_blit(canvas, op)
            snap()
        elif kind == "rect":
            _draw_rect(canvas, op)
            snap()
        elif kind == "ellipse":
            _draw_ellipse(canvas, op)
            snap()
        elif kind == "scene":
            _draw_scene(canvas, op)
            snap()
        elif kind == "card_diff":
            pid = op.get("patch_id")
            _require(isinstance(pid, str) and pid in patches,
                     f"frames[{i}]: card_diff patch_id "
                     f"'{pid}' not found in spec.patches")
            _apply_patch(canvas, patches[pid], pid)
            snap()
        elif kind == "delay":
            ms = int(op.get("ms", 0))
            _require(ms > 0, f"frames[{i}]: delay.ms must be > 0")
            _require(snapshots, f"frames[{i}]: delay before any paint op has "
                                "nothing to hold")
            holds = max(1, round(ms / hold_per_delay_unit))
            for _ in range(holds):
                snap()
                snapshots[-1] = snapshots[-2]     # literal hold, no re-copy cost
        elif kind == "flush":
            # device-owned refresh hygiene; server-side a no-op marker
            continue
        else:
            raise SpecError(f"frames[{i}]: unknown op '{kind}' "
                            "(blit|rect|ellipse|scene|card_diff|delay|flush)")

    closed_loop = loop == -1
    if loop > 1:
        total = len(snapshots) * loop
        _require(total <= MAX_FRAMES,
                 f"loop={loop} x {len(snapshots)} frames = {total} > "
                 f"{MAX_FRAMES}-frame cap — use loop:-1 (device replays) "
                 "or fewer frames")
        snapshots = snapshots * loop

    count = len(snapshots)
    _require(count <= MAX_FRAMES,
             f"{count} frames > {MAX_FRAMES}-frame cap")
    duration_s = count * interval_ms / 1000.0
    _require(duration_s <= MAX_DURATION_S,
             f"{count} frames x {interval_ms}ms = {duration_s:.1f}s > "
             f"{MAX_DURATION_S}s cap — shorten the sequence or the interval")

    # ── store — seq_id is content-derived (idempotent re-POST) ──────────
    spec_json = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    seq_id = hashlib.sha256(spec_json.encode()).hexdigest()[:16]
    out_dir = ANIM_DIR / seq_id
    manifest_path = out_dir / "manifest.json"
    if manifest_path.is_file():                   # dedupe: same spec, same bytes
        return json.loads(manifest_path.read_text())
    out_dir.mkdir(exist_ok=True)

    pack, preview, want = ((_pack_1bit, _preview_1bit, BYTES_1BIT)
                           if fmt == "1bit" else
                           (_pack_4gray, _preview_4gray, BYTES_4GRAY))
    for n, im in enumerate(snapshots):
        raw = pack(im)
        assert len(raw) == want, f"frame {n}: {len(raw)} != {want}"
        (out_dir / f"{n}.raw").write_bytes(raw)
        preview(raw).save(out_dir / f"{n}.png")

    manifest = {
        "seq_id": seq_id,
        "anim_id": anim_id,
        "frames": [f"/api/frames/{seq_id}/{n}.raw" for n in range(count)],
        "previews": [f"/api/frames/{seq_id}/{n}.png" for n in range(count)],
        "interval_ms": interval_ms,
        "format": fmt,                            # "1bit" | "gray4"
        "frame_bytes": want,
        "count": count,
        "closed_loop": closed_loop,
        "duration_s": duration_s,
        "caps": {"max_frames": MAX_FRAMES, "max_duration_s": MAX_DURATION_S},
        "ts": time.time(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=1))
    (out_dir / "spec.json").write_text(spec_json)
    return manifest


def load_manifest(seq_id: str) -> Optional[Dict[str, Any]]:
    p = ANIM_DIR / seq_id / "manifest.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text())


def frame_path(seq_id: str, name: str) -> Optional[Path]:
    p = ANIM_DIR / seq_id / name
    return p if p.is_file() else None


# ── Source (3): video → ffmpeg → frame sequence ────────────────────────
# The honest contract from EINK_ANIMATION.md §2 stands: real video is
# physically impossible on this panel — this is the nicla_take_video
# philosophy (frames show HOW the scene changes, not smooth motion).
# ffmpeg samples the clip at the e-ink's own cadence (≤ 1000/MIN_INTERVAL
# fps), letterboxes to canvas, Floyd–Steinberg dithers, and packages the
# EXACT same manifest tiny_stream_play expects.

import shutil
import subprocess
import tempfile

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def render_video(data: bytes, filename: str = "clip",
                 fmt: str = "1bit", interval_ms: int = 1000) -> Dict[str, Any]:
    """Video bytes → dithered e-ink frame sequence (same store/manifest).

    interval_ms picks the SAMPLING cadence (default 1s = time-lapse feel);
    clamped to MIN_INTERVAL_MS. Long clips are sampled evenly across their
    whole duration rather than truncated: cap frames = min(MAX_FRAMES,
    MAX_DURATION_S/interval).
    """
    _require(fmt in ("1bit", "gray4"), "format must be 1bit|gray4")
    interval_ms = max(int(interval_ms), MIN_INTERVAL_MS)
    max_count = min(MAX_FRAMES, int(MAX_DURATION_S * 1000 / interval_ms))

    sha = hashlib.sha256(data).hexdigest()[:16]
    seq_id = hashlib.sha256(
        f"video:{sha}:{fmt}:{interval_ms}".encode()).hexdigest()[:16]
    out_dir = ANIM_DIR / seq_id
    manifest_path = out_dir / "manifest.json"
    if manifest_path.is_file():                   # idempotent re-POST
        return json.loads(manifest_path.read_text())

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.bin"
        src.write_bytes(data)
        # probe duration so we can sample evenly instead of truncating
        probe = subprocess.run(
            [FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error",
             "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
            capture_output=True, text=True, timeout=30)
        try:
            duration = float(probe.stdout.strip())
        except ValueError:
            raise SpecError(f"ffprobe cannot read this file as video: "
                            f"{(probe.stderr or '').strip()[:200]}")
        wanted_fps = 1000.0 / interval_ms
        # if the clip at wanted_fps would blow the cap, sample evenly across it
        fps = min(wanted_fps, max_count / duration) if duration > 0 else wanted_fps
        proc = subprocess.run(
            [FFMPEG, "-v", "error", "-i", str(src),
             "-vf", f"fps={fps:.6f}", "-frames:v", str(max_count),
             "-pix_fmt", "gray", str(Path(td) / "f_%03d.png")],
            capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise SpecError(f"ffmpeg refused the clip: "
                            f"{(proc.stderr or '').strip()[:200]}")
        pngs = sorted(Path(td).glob("f_*.png"))
        _require(len(pngs) > 0, "ffmpeg produced no frames")

        out_dir.mkdir(exist_ok=True)
        pack, preview, want = ((_pack_1bit, _preview_1bit, BYTES_1BIT)
                               if fmt == "1bit" else
                               (_pack_4gray, _preview_4gray, BYTES_4GRAY))
        for n, p in enumerate(pngs):
            im = _letterbox(Image.open(p).convert("L"), LANDSCAPE)
            raw = pack(im)
            assert len(raw) == want, f"frame {n}: {len(raw)} != {want}"
            (out_dir / f"{n}.raw").write_bytes(raw)
            preview(raw).save(out_dir / f"{n}.png")

    count = len(pngs)
    manifest = {
        "seq_id": seq_id,
        "anim_id": f"video:{filename}"[:64],
        "source": "video",
        "source_sha": sha,
        "source_duration_s": round(duration, 2),
        "frames": [f"/api/frames/{seq_id}/{n}.raw" for n in range(count)],
        "previews": [f"/api/frames/{seq_id}/{n}.png" for n in range(count)],
        "interval_ms": interval_ms,
        "format": fmt,
        "frame_bytes": want,
        "count": count,
        "closed_loop": False,
        "duration_s": count * interval_ms / 1000.0,
        "caps": {"max_frames": MAX_FRAMES, "max_duration_s": MAX_DURATION_S},
        "note": "sampled at e-ink cadence — a time-lapse of the clip, not "
                "smooth motion (the panel cannot do that; EINK_ANIMATION.md §2)",
        "ts": time.time(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=1))
    return manifest
