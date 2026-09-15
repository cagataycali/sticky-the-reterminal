#!/usr/bin/env python3
"""
mock_device — a software reTerminal Sticky (STICKY_MOCK=1).

Firmware M3 (device on the fleet) isn't done yet, so the dashboard must be
provable end-to-end WITHOUT hardware. This module implements the DEVICE end
of the envelope contract exactly as docs/API_CONTRACT.md + docs/ARCHITECTURE.md
specify it:

  • dispatch(prompt) parses `<command> <json-args?>` and answers like the
    firmware's tiny_commands.c table (unknown command → help text, never silence)
  • render_ui renders card spec v1 (text/list/kv/chart/image/buttons/composite)
    to an 800×480 4-gray PNG — the same framebuffer the real display would show,
    so the dashboard's screen-mirror card works for real
  • status/sensors return plausible, slowly-evolving values
  • button taps (ui_tap) are simulated via tap(), feeding the activity feed

Live mode swaps this out for TinyRelay — the dashboard code path is identical.
"""
from __future__ import annotations

import io
import json
import math
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

W, H = 800, 480
GRAYS = [0, 85, 170, 255]  # 4-level palette (SSD1677-class)
FW_VERSION = "tiny-sticky 0.1.0-mock"


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in ("/System/Library/Fonts/Helvetica.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


F_TITLE, F_BODY, F_SMALL, F_BTN = _font(44), _font(30), _font(20), _font(26)


def _quantize4(img: Image.Image) -> Image.Image:
    """Snap an L-mode image to the 4 e-ink gray levels."""
    return img.point(lambda p: min(GRAYS, key=lambda g: abs(g - p)))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> List[str]:
    lines: List[str] = []
    for para in str(text).split("\n"):
        cur = ""
        for word in para.split(" "):
            trial = f"{cur} {word}".strip()
            if draw.textlength(trial, font=font) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


class MockSticky:
    def __init__(self) -> None:
        self.boot_ts = time.time()
        self.sleeping = False
        self.card: Dict[str, Any] = {
            "type": "text",
            "title": "sticky (mock)",
            "body": "Software Sticky is up. Send a render_ui card from the composer →",
        }
        self.card_id = "c_boot"
        self._card_seq = 0
        self._buttons_bbox: List[Tuple[str, Tuple[int, int, int, int]]] = []
        self._render()  # prime framebuffer

    # ── envelope dispatch (mirrors tiny_commands.c) ────────────────────
    def dispatch(self, prompt: str) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        cmd, _, rest = prompt.partition(" ")
        cmd = cmd.lower()
        args: Dict[str, Any] = {}
        if rest.strip():
            try:
                parsed = json.loads(rest)
                args = parsed if isinstance(parsed, dict) else {"value": parsed}
            except ValueError:
                return {"result": f"bad json args for '{cmd}'"}

        if self.sleeping and cmd not in ("wake", "status", "help"):
            return {"result": "asleep — send `wake` first (EXT1 button would also wake the real unit)"}

        handler = getattr(self, f"_cmd_{cmd}", None)
        if handler is None:
            return {"result": self._help()}
        return handler(args)

    def _help(self) -> str:
        return ("commands: screenshot · render_ui <card-json> · ask {text} · say {pattern} · "
                "status · sensors · image {url} · stream {urls,interval_s} · sleep/wake · "
                "ota {channel} · reprovision (mock device)")

    # ── commands ───────────────────────────────────────────────────────
    def _cmd_help(self, a):  # noqa: ANN001
        return {"result": self._help()}

    def _cmd_status(self, a) -> Dict[str, Any]:
        up = time.time() - self.boot_ts
        battery = max(5, 96 - int(up / 3600 * 2))  # ~2%/h mock drain
        return {"result": json.dumps({
            "battery_pct": battery,
            "charging": False,
            "rssi_dbm": -52 + random.randint(-4, 4),
            "heap_free": 214000 + random.randint(-9000, 9000),
            "psram_free": 6900000 + random.randint(-40000, 40000),
            "fw": FW_VERSION,
            "uptime_s": int(up),
            "sleeping": self.sleeping,
        })}

    def _cmd_sensors(self, a) -> Dict[str, Any]:
        t = time.time()
        return {"result": json.dumps({
            "sht40": {"temp_c": round(22.5 + math.sin(t / 900) * 1.5, 2),
                      "rh_pct": round(48 + math.sin(t / 1300) * 6, 1)},
            "imu": {"orientation": "wall-vertical",
                    "accel_g": [round(0.01 + random.uniform(-.005, .005), 3), 0.02, 0.99]},
            "rtc": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "gauge": {"voltage_mv": 3921, "current_ma": -14},
        })}

    def _cmd_screenshot(self, a) -> Dict[str, Any]:
        # real fw: framebuffer → BMP → POST /api/media → hosted URL.
        # mock: server exposes the PNG at /api/mock/screen.png (URL rewritten there).
        return {"result": "screenshot", "images": [{"url": "mock://screen", "format": "png"}]}

    def _cmd_render_ui(self, a) -> Dict[str, Any]:
        if not a or "type" not in a:
            return {"result": "render_ui needs a card spec: {type: text|list|kv|chart|image|buttons|composite, …}"}
        self._card_seq += 1
        self.card = a
        self.card_id = a.get("card_id") or f"c_{self._card_seq:04d}"
        self._render()
        return {"result": "rendered", "card_id": self.card_id}

    def _cmd_ask(self, a) -> Dict[str, Any]:
        q = a.get("text", "")
        card = {"type": "text", "title": "tiny", "body": f"(mock agent) You asked: “{q}”. "
                "On the real device this goes through POST /api/devices/ask → owner agent turn."}
        self._card_seq += 1
        self.card, self.card_id = card, f"c_{self._card_seq:04d}"
        self._render()
        return {"result": json.dumps({"text": card["body"], "card_id": self.card_id})}

    def _cmd_say(self, a) -> Dict[str, Any]:
        return {"result": f"chime played: {a.get('melody') or a.get('pattern') or 'default blip'} (buzzer GPIO48 — mock)"}

    def _cmd_image(self, a) -> Dict[str, Any]:
        return self._cmd_render_ui({"type": "image", "src": a.get("url", ""), "title": a.get("title", "")})

    def _cmd_stream(self, a) -> Dict[str, Any]:
        n = len(a.get("urls", [])) or 1
        return {"result": f"stream accepted: {n} frame(s) at {a.get('interval_s', 5)}s cadence (partial refresh, mock)"}

    def _cmd_sleep(self, a) -> Dict[str, Any]:
        self.sleeping = True
        return {"result": f"deep sleep{' for ' + str(a['s']) + 's' if a.get('s') else ''} — EXT1 (AI btn) wakes"}

    def _cmd_wake(self, a) -> Dict[str, Any]:
        self.sleeping = False
        return {"result": "awake (mic-mux fix applied on real hw: GPIO19/20 reclaim + rail cycle)"}

    def _cmd_ota(self, a) -> Dict[str, Any]:
        return {"result": f"ota staged from channel '{a.get('channel', 'sticky-stable')}' → ota_1, trial boot next restart (mock)"}

    def _cmd_reprovision(self, a) -> Dict[str, Any]:
        return {"result": "wifi wiped (identity kept) — portal tiny-XXXX/tinysetup back up (mock)"}

    # ── touch ─────────────────────────────────────────────────────────
    def tap(self, button_id: str) -> Dict[str, Any]:
        """Simulate a ui_tap → real fw would POST /api/devices/event."""
        known = [b.get("id") for b in self.card.get("buttons", [])]
        if self.card.get("type") == "composite":
            for part in self.card.get("parts", []):
                known += [b.get("id") for b in part.get("buttons", [])]
        return {"kind": "ui_tap", "card_id": self.card_id, "button_id": button_id,
                "known": button_id in known, "ts": time.time()}

    # ── renderer (card spec v1 → 800×480 4-gray) ──────────────────────
    def _render(self) -> None:
        img = Image.new("L", (W, H), 255)
        d = ImageDraw.Draw(img)
        self._buttons_bbox = []
        # status bar (as the firmware draws it)
        d.rectangle([0, 0, W, 36], fill=0)
        d.text((12, 6), time.strftime("%a %H:%M"), font=F_SMALL, fill=255)
        st = json.loads(self._cmd_status({})["result"])
        right = f"▂▄▆█ {st['rssi_dbm']}dBm   🔋{st['battery_pct']}%"
        d.text((W - 12 - d.textlength(right, font=F_SMALL), 6), right, font=F_SMALL, fill=255)

        if self.sleeping:
            d.text((W / 2 - 60, H / 2 - 30), "☾ sleeping", font=F_TITLE, fill=85)
        else:
            card = dict(self.card)
            parts = card.pop("parts", None) if card.get("type") == "composite" else None
            y = self._draw_card(d, img, card, 56)
            for part in parts or []:
                y = self._draw_card(d, img, part, y + 24)

        self.framebuffer = _quantize4(img)

    def _draw_card(self, d: ImageDraw.ImageDraw, img: Image.Image,
                   card: Dict[str, Any], y: int) -> int:
        pad = 28
        if card.get("title"):
            d.text((pad, y), str(card["title"]), font=F_TITLE, fill=0)
            y += 62
        t = card.get("type", "text")

        if t == "text" and card.get("body"):
            for line in _wrap(d, card["body"], F_BODY, W - 2 * pad):
                d.text((pad, y), line, font=F_BODY, fill=0)
                y += 42
        elif t == "list":
            for it in card.get("items", [])[:8]:
                d.ellipse([pad, y + 14, pad + 10, y + 24], fill=0)
                d.text((pad + 26, y), str(it), font=F_BODY, fill=0)
                y += 46
        elif t == "kv":
            rows = card.get("rows", {})
            items = rows.items() if isinstance(rows, dict) else [(r.get("k"), r.get("v")) for r in rows]
            for k, v in list(items)[:8]:
                d.text((pad, y), str(k), font=F_BODY, fill=85)
                d.text((W / 2, y), str(v), font=F_BODY, fill=0)
                y += 48
        elif t == "chart":
            data = [float(x) for x in card.get("data", []) if isinstance(x, (int, float))][:24]
            if data:
                ch_h, ch_w = 200, W - 2 * pad
                base = y + ch_h
                bw = ch_w / len(data)
                mx = max(data) or 1
                for i, v in enumerate(data):
                    bh = int(v / mx * (ch_h - 10))
                    d.rectangle([pad + i * bw + 4, base - bh, pad + (i + 1) * bw - 4, base],
                                fill=85 if i % 2 else 0)
                d.line([pad, base, pad + ch_w, base], fill=0, width=2)
                y = base + 12
        elif t == "image":
            box = (pad, y, W - pad, min(H - 30, y + 300))
            src = card.get("src", "")
            d.rectangle(box, outline=0, width=2)
            d.text((pad + 16, y + 16), "🖼 image card", font=F_BODY, fill=85)
            for line in _wrap(d, src or "(no src)", F_SMALL, W - 2 * pad - 32):
                y += 30
                d.text((pad + 16, y + 16), line, font=F_SMALL, fill=170)
            y = box[3] + 8

        # buttons row (any card may carry ≤4)
        btns = card.get("buttons", [])[:4]
        if btns:
            y = max(y + 16, H - 96)
            bw = (W - 2 * pad - (len(btns) - 1) * 16) / len(btns)
            for i, b in enumerate(btns):
                x0 = pad + i * (bw + 16)
                bbox = (int(x0), y, int(x0 + bw), y + 64)
                d.rounded_rectangle(bbox, radius=12, outline=0, width=3)
                label = str(b.get("label", b.get("id", "?")))
                tw = d.textlength(label, font=F_BTN)
                d.text((x0 + (bw - tw) / 2, y + 17), label, font=F_BTN, fill=0)
                self._buttons_bbox.append((str(b.get("id", f"b{i}")), bbox))
            y += 72
        return y

    def screen_png(self) -> bytes:
        buf = io.BytesIO()
        self._render()  # refresh clock/battery
        self.framebuffer.save(buf, format="PNG")
        return buf.getvalue()

    def buttons(self) -> List[Dict[str, Any]]:
        return [{"id": bid, "bbox": bbox} for bid, bbox in self._buttons_bbox]
