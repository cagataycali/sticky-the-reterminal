"""
🦾 arm — Fomo, drawn from its own joint angles.

Left: a side-view silhouette of the SO-101 built from the four arm angles
(base post, upper arm, forearm, wrist, a small camera head whose tilt follows
servo 6), the ground line, and a pan compass for the base. The kinematics are a
sketch — right link ratios, approximate zero offsets — good for "folded / half
up / looking down", not for measurement. Right: pose name, bus voltage with the
LIFT threshold (the leader supply is 5.5 V: "cannot lift"), torque, head link
(transport · RSSI · ToF · roll/pitch), and six joint rows with q from home on a
±window bar. Data: strands-arm dash /api/state, read-only, fixture fallback.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Tuple

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import arm as ad
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans

# sketch kinematics: absolute link angles (deg, CCW from +x, y up) from q (deg from home)
def _angles(q: Dict[str, float]) -> Tuple[float, float, float]:
    a1 = 170 - q["lift"]            # folded: upper arm lies back near horizontal; upright ≈ 90
    a2 = a1 - 165 - q["elbow"]      # folded: forearm folds forward along it; upright ≈ 80
    a3 = a2 - 160 - q["wrist"]      # folded: head tucked; upright ≈ level
    return a1, a2, a3


def _pt(x: float, y: float, r: float, a: float) -> Tuple[float, float]:
    return x + r * math.cos(math.radians(a)), y - r * math.sin(math.radians(a))


def draw_arm(g: Glass, d: Dict[str, Any], x0: int, y0: int, w: int, h: int, caption: bool = True) -> None:
    """Silhouette inside the box; ground at the bottom, base a third in from the left."""
    scale = min(w / 330, h / 340)
    a1, a2, a3 = _angles(d["q"])
    gy = y0 + h - 14

    def _reach(sc: float):
        # horizontal extent of the chain relative to the base, at this scale
        q0 = (0.0, 0.0)
        q1 = _pt(*q0, 130 * sc, a1)
        q2 = _pt(*q1, 140 * sc, a2)
        q3 = _pt(*q2, (46 + 30) * sc, a3)          # + the camera head
        xs = [q0[0], q1[0], q2[0], q3[0]]
        return min(xs) - 30 * sc, max(xs) + 30 * sc

    # keep the whole silhouette inside the box: slide the base, then shrink if it still spills
    for _ in range(6):
        lo, hi = _reach(scale)
        if hi - lo <= w - 8:
            break
        scale *= 0.85
    lo, hi = _reach(scale)
    bx = int(min(max(x0 + w * 0.36, x0 + 4 - lo), x0 + w - 4 - hi))
    L1, L2, L3, post = 130 * scale, 140 * scale, 46 * scale, 50 * scale
    g.hairline(x0, gy, x0 + w, DARK)
    # base
    g.rect((bx - int(26 * scale), gy - int(10 * scale), bx + int(26 * scale), gy), fill=BLACK, radius=2)
    g.d.line([(bx, gy - int(10 * scale)), (bx, gy - post)], fill=BLACK, width=max(4, int(10 * scale)))
    p0 = (bx, gy - post)
    p1 = _pt(*p0, L1, a1)
    p2 = _pt(*p1, L2, a2)
    p3 = _pt(*p2, L3, a3)
    wid = max(5, int(14 * scale))
    g.d.line([p0, p1], fill=BLACK, width=wid)
    g.d.line([p1, p2], fill=BLACK, width=max(4, wid - 3))
    g.d.line([p2, p3], fill=BLACK, width=max(3, wid - 6))
    for p in (p0, p1, p2):
        g.circle(int(p[0]), int(p[1]), max(5, int(9 * scale)), fill=WHITE, outline=BLACK, width=2)
    # camera head: a small box whose tilt follows servo 6, lens as a dot
    tilt = a3 - d["q"]["tilt"]
    hw, hh = 26 * scale, 16 * scale
    hx, hy = _pt(*p3, hw / 2, tilt)
    ca, sa = math.cos(math.radians(tilt)), math.sin(math.radians(tilt))
    corners = []
    for cx, cy in ((-hw / 2, -hh / 2), (hw / 2, -hh / 2), (hw / 2, hh / 2), (-hw / 2, hh / 2)):
        corners.append((hx + cx * ca + cy * sa, hy - (cx * sa - cy * ca)))
    g.d.polygon(corners, fill=WHITE, outline=BLACK)
    lens = _pt(hx, hy, hw / 2, tilt)
    g.circle(int(lens[0]), int(lens[1]), max(2, int(3 * scale)), fill=BLACK)
    # pan compass (top view) bottom-right of the box
    r = int(22 * scale)
    cx, cy = x0 + w - r - 6, y0 + r + 6
    g.circle(cx, cy, r, outline=DARK, width=1)
    pan = -d["q"]["pan"]
    tip = _pt(cx, cy, r - 3, 90 + pan)
    g.d.line([(cx, cy), tip], fill=BLACK, width=2)
    g.text((x0 + w, cy + r + 4), f"pan {d['q']['pan']:+.0f}°", sans(11, 500), DARK, anchor="ra")  # right-aligned: never past the box
    if caption:
        g.text((x0, y0), "side view · sketch kinematics", sans(11, 500), DARK)


def _rows(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, rh: int = 30) -> int:
    for j in d["joints"]:
        g.text((x, y), j["name"].replace("_", " "), sans(13, 600), BLACK)
        g.text((x + w, y - 3), f"{j['q']:+.1f}°", mono(15, 700), BLACK, anchor="ra")
        g.text((x + w - 78, y), f"{j['deg']:.1f}", mono(12, 500), DARK, anchor="ra")
        # window bar with the current position; home tick
        bx0, bx1, by = x, x + w - 140, y + 18
        g.hairline(bx0, by, bx1, LIGHT)
        mn, mx = j.get("min"), j.get("max")
        if mn is not None and mx is not None and float(mx) > float(mn):
            span = float(mx) - float(mn)
            hx = bx0 + int((j["home"] - float(mn)) / span * (bx1 - bx0))
            px = bx0 + int(min(1.0, max(0.0, (j["deg"] - float(mn)) / span)) * (bx1 - bx0))
            g.vline(hx, by - 3, by + 3, DARK)
            g.circle(px, by, 3, fill=BLACK)
        if j["torque"]:
            g.text((bx1 + 8, by - 7), "torque", sans(11, 600), BLACK)
        y += rh
    return y


def _facts(g: Glass, d: Dict[str, Any], x: int, y: int, w: int) -> int:
    hdr = d["pose"]
    g.text((x, y), hdr, sans(32, 700), BLACK)
    g.text((x + w, y + 8), "live" if d["source"] == "live" else "demo", sans(11, 600), DARK, anchor="ra")
    y += 44
    v = d["volts"]
    lift = "can lift" if d["can_lift"] else f"cannot lift · needs {d['lift_min_v']:.1f} V"
    g.text((x, y), f"{v:.1f} V", mono(22, 700), BLACK)
    g.text((x + 82, y + 4), g.fit_text(lift, w - 82, sans(14, 500)), sans(14, 500), DARK)
    y += 30
    g.text((x, y), "torque " + ("ON" if d["torque_any"] else "off") + "  ·  " + ("folded" if d["folded"] else "raised"), sans(14, 500), DARK)
    y += 24
    h = d["head"]
    head = "head " + ("· " + h["transport"] if h["ok"] else "offline")
    if h["ok"]:
        if h.get("rssi") is not None:
            head += f" {h['rssi']} dBm"
        if h.get("tof_mm") is not None:
            head += f" · ToF {h['tof_mm']} mm"
    g.text((x, y), g.fit_text(head, w, sans(14, 500)), sans(14, 500), DARK)
    y += 22
    if h.get("roll") is not None:
        g.text((x, y), f"imu roll {h['roll']:+.0f}° pitch {h['pitch']:+.0f}°  ·  guard {d['guard']['step']}° @ {d['guard']['speed']}°/s", sans(13, 500), DARK)
        y += 24
    return y


@component(
    "arm", "Arm",
    "Fomo drawn from its own joint angles: side-view silhouette + pan compass, pose, bus voltage vs the "
    "lift threshold, torque, head link, and six joint rows with position on their calibrated window.",
    params={"url": "state url (default local strands-arm dash)", "demo": "1 → fixture"},
    fetch=ad.fetch,
    native_hint="type:'arm' {q:[6×i16 ×10], v:u8, torque:u8, pose, head:{rssi,tof}} ≈ 40 B — the silhouette is 4 line segments the firmware can draw",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        g.label((MARGIN, MARGIN), f"{d['role']} arm", 13, DARK)
        g.text((g.w - MARGIN, MARGIN - 2), d["now"], mono(16, 500), DARK, anchor="ra")
        lw = 330
        draw_arm(g, d, MARGIN, MARGIN + 30, lw, g.h - 2 * MARGIN - 56)
        rx = MARGIN + lw + 36
        g.vline(rx - 18, MARGIN + 24, g.h - MARGIN, LIGHT)
        y = _facts(g, d, rx, MARGIN + 24, g.w - MARGIN - rx)
        g.hairline(rx, y + 4, g.w - MARGIN, LIGHT)
        _rows(g, d, rx, y + 14, g.w - MARGIN - rx, rh=29)
        g.text((MARGIN, g.h - MARGIN - 14), d["date_label"] + ("  ·  " + str(d["error"])[:60] if d.get("error") else ""), sans(12, 500), DARK)
    else:
        g = Glass(PORTRAIT)
        g.label((MARGIN, MARGIN), f"{d['role']} arm", 13, DARK)
        g.text((g.w - MARGIN, MARGIN - 2), d["now"], mono(16, 500), DARK, anchor="ra")
        y = _facts(g, d, MARGIN, MARGIN + 24, g.w - 2 * MARGIN)
        draw_arm(g, d, MARGIN, y + 10, g.w - 2 * MARGIN, 280)
        y = y + 300
        g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
        _rows(g, d, MARGIN, y + 14, g.w - 2 * MARGIN, rh=32)
    return g.snap()
