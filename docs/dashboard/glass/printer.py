"""
🖨 printer — the job on the bed, from across the room.

What you check on a print from the desk: how far, when it ends, is anything
hot that shouldn't be, and which spool is running out. So: the percentage
large, a wide bar beneath it with a thin second bar for layers (the honest
one — a 3MF spends its last 10 % slowly), the finish time as a clock reading
rather than a countdown, the job filename in mono. Temperatures as three
thermometer bars with a target tick — filled to actual, tick at target, so a
heater that is not there yet reads at a glance. Spools as four rings filled by
what is left, the active one heavy. Errors, if any, in the footer.
Data: glass.adapters.printer (pushed · ~/.tiny/sticky-printer.json · fixture).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import printer as pr
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _therm(g: Glass, x: int, y: int, w: int, name: str, actual: Optional[float], target: Optional[float], vmax: float) -> None:
    g.text((x, y), name, sans(12, 600), BLACK)
    lab = f"{actual:.0f}°" if actual is not None else "—"
    if target:
        lab += f" / {target:.0f}°"
    g.text((x + w, y), lab, mono(12, 500), BLACK, anchor="ra")
    by = y + 19
    g.rect((x, by, x + w, by + 6), fill=LIGHT)
    if actual is not None:
        g.rect((x, by, x + round(w * min(1.0, actual / vmax)), by + 6), fill=BLACK)
    if target:
        tx = x + round(w * min(1.0, target / vmax))
        g.rect((tx - 1, by - 4, tx + 1, by + 10), fill=BLACK)


def _spool(g: Glass, cx: int, cy: int, r: int, s: Dict[str, Any]) -> None:
    active = bool(s.get("active"))
    rem = max(0.0, min(1.0, float(s.get("remaining") or 0)))
    g.circle(cx, cy, r, fill=None, outline=LIGHT, width=3)
    if rem > 0:
        g.d.arc((cx - r, cy - r, cx + r, cy + r), start=-90, end=-90 + 360 * rem, fill=BLACK, width=6 if active else 3)
    g.circle(cx, cy, r - 12, fill=BLACK if active else None, outline=DARK, width=1)
    g.text((cx, cy), str(s.get("slot", "")), sans(13, 700), WHITE if active else BLACK, anchor="mm")
    g.text((cx, cy + r + 6), str(s.get("material", "")), sans(12, 700 if active else 500), BLACK, anchor="ma")
    g.text((cx, cy + r + 22), f"{s.get('color', '')} · {rem * 100:.0f} %", sans(11, 500), DARK, anchor="ma")


def _progress(g: Glass, d: Dict[str, Any], x: int, y: int, w: int) -> None:
    pct = f"{d['pct']}"
    g.text((x, y), pct, mono(64, 700), BLACK)
    pw = g.text_size(pct, mono(64, 700))[0]
    g.text((x + pw + 4, y + 14), "%", mono(24, 700), DARK)
    tx = x + pw + 48
    if d["state"] == "printing" and d["eta_label"]:
        g.text((tx, y + 10), f"done at {d['eta_label']}", sans(18, 700), BLACK)
        g.text((tx, y + 36), f"{d['remaining_label']} left · {d['elapsed_label']} elapsed", sans(12, 500), DARK)
    else:
        g.text((tx, y + 10), d["state"], sans(18, 700), BLACK)
        g.text((tx, y + 36), f"{d['elapsed_label']} elapsed" if d.get("elapsed_min") else "", sans(12, 500), DARK)
    by = y + 84
    g.rect((x, by, x + w, by + 10), fill=LIGHT)
    g.rect((x, by, x + round(w * d["progress"]), by + 10), fill=BLACK)
    if d.get("layer") is not None and d.get("layers"):
        lf = min(1.0, float(d["layer"]) / float(d["layers"]))
        g.rect((x, by + 14, x + w, by + 16), fill=LIGHT)
        g.rect((x, by + 14, x + round(w * lf), by + 16), fill=DARK)
        g.text((x + w, by + 20), d["layer_label"], mono(11, 500), DARK, anchor="ra")
    g.text((x, by + 20), g.fit_text(str(d.get("job") or ""), w - 130, mono(11, 500)), mono(11, 500), DARK)


@component(
    "printer", "Printer",
    "The job on the bed from across the room: percentage large, a wide bar with a thin layer bar beneath (the honest one), "
    "finish as a clock time, filename in mono; nozzle/bed/chamber as thermometer bars with a target tick; spools as rings "
    "filled by what is left, the active one heavy; errors in the footer.",
    params={"state": "printing|paused|idle|finished|error", "job": "filename", "progress": "0..1", "layer": "n", "layers": "N",
            "remaining_min": "int", "nozzle_c": "…", "bed_c": "…", "ams": "JSON [{slot, material, color, remaining, active}]", "demo": "1 = fixture"},
    fetch=pr.fetch,
    native_hint="type:'printer' {pct, layer, layers, eta, temps:[3×u16], spools:[4×u8]} ≈ 40 B — bars and arcs the ESP32 draws itself",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    title = str(d.get("name") or "Printer")
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        g.text((MARGIN, MARGIN - 6), title, sans(24, 700), BLACK)
        g.text((MARGIN + g.text_size(title, sans(24, 700))[0] + 12, MARGIN + 2), d["state"] + (f" · {d['speed']}" if d.get("speed") else ""), sans(13, 500), DARK)
        g.text((g.w - MARGIN, MARGIN - 2), d["at"] + ("  · demo" if d["demo"] else ""), mono(12, 500), DARK, anchor="ra")
        lw = 470
        _progress(g, d, MARGIN, MARGIN + 40, lw)
        rx = MARGIN + lw + 34
        rw = g.w - MARGIN - rx
        g.d.line([(rx - 16, MARGIN + 40), (rx - 16, g.h - MARGIN)], fill=LIGHT, width=1)
        g.label((rx, MARGIN + 40), "heat", 11, DARK)
        _therm(g, rx, MARGIN + 60, rw, "nozzle", d.get("nozzle_c"), d.get("nozzle_target_c"), 300)
        _therm(g, rx, MARGIN + 100, rw, "bed", d.get("bed_c"), d.get("bed_target_c"), 120)
        if d.get("chamber_c") is not None:
            _therm(g, rx, MARGIN + 140, rw, "chamber", d.get("chamber_c"), None, 60)
        if d.get("filament"):
            g.text((rx, MARGIN + 182), g.fit_text(str(d["filament"]), rw, sans(12, 500)), sans(12, 500), DARK)
        # spools along the bottom-left
        y0 = MARGIN + 200
        g.hairline(MARGIN, y0 - 6, MARGIN + lw, LIGHT)
        g.label((MARGIN, y0 + 4), "spools", 11, DARK)
        ams: List[Dict[str, Any]] = d["ams"][:4]
        if ams:
            cw = lw // len(ams)
            for i, s in enumerate(ams):
                _spool(g, MARGIN + cw * i + cw // 2, y0 + 72, 36, s)
        if d["hms"]:
            fy = g.h - MARGIN - 18
            g.rect((rx, fy + 4, rx + 6, fy + 10), fill=BLACK)
            g.text((rx + 14, fy), g.fit_text(str(d["hms"][0]), rw - 14, sans(12, 500)), sans(12, 500), BLACK)
        elif d["state"] == "printing":
            g.text((rx, g.h - MARGIN - 18), "no printer alerts", sans(12, 500), DARK)
    else:
        g = Glass(PORTRAIT)
        g.text((MARGIN, MARGIN - 6), title, sans(24, 700), BLACK)
        g.text((MARGIN, MARGIN + 26), d["state"] + (f" · {d['speed']}" if d.get("speed") else "") + ("  · demo" if d["demo"] else ""), sans(13, 500), DARK)
        g.text((g.w - MARGIN, MARGIN - 2), d["at"], mono(12, 500), DARK, anchor="ra")
        _progress(g, d, MARGIN, MARGIN + 56, g.w - 2 * MARGIN)
        y = MARGIN + 200
        g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
        g.label((MARGIN, y + 10), "heat", 11, DARK)
        _therm(g, MARGIN, y + 30, g.w - 2 * MARGIN, "nozzle", d.get("nozzle_c"), d.get("nozzle_target_c"), 300)
        _therm(g, MARGIN, y + 70, g.w - 2 * MARGIN, "bed", d.get("bed_c"), d.get("bed_target_c"), 120)
        if d.get("chamber_c") is not None:
            _therm(g, MARGIN, y + 110, g.w - 2 * MARGIN, "chamber", d.get("chamber_c"), None, 60)
        y2 = y + 160
        g.hairline(MARGIN, y2, g.w - MARGIN, LIGHT)
        g.label((MARGIN, y2 + 10), "spools", 11, DARK)
        ams = d["ams"][:4]
        if ams:
            cw = (g.w - 2 * MARGIN) // len(ams)
            for i, s in enumerate(ams):
                _spool(g, MARGIN + cw * i + cw // 2, y2 + 76, 34, s)
        if d.get("filament"):
            g.text((MARGIN, y2 + 160), g.fit_text(str(d["filament"]), g.w - 2 * MARGIN, sans(12, 500)), sans(12, 500), DARK)
        if d["hms"]:
            fy = g.h - MARGIN - 18
            g.rect((MARGIN, fy + 4, MARGIN + 6, fy + 10), fill=BLACK)
            g.text((MARGIN + 14, fy), g.fit_text(str(d["hms"][0]), g.w - 2 * MARGIN - 14, sans(12, 500)), sans(12, 500), BLACK)
    return g.snap()
