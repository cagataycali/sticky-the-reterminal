"""
🌬 air — what you are breathing, against the numbers that matter.

Left: the US AQI as a big numeral with its category, on a six-band scale bar
(0–300, bands alternate LIGHT/DARK, the marker sits on today's value) and the
day's AQI range. Right: the UV curve for the day as an area sparkline with the
peak labelled and the "protect" window (UV ≥ 3) drawn as a bracket. Below: six
pollutants, each a bar against its WHO 2021 guideline — the bar fills to the
guideline at 100 %, anything past it turns black. Pollen when the model covers
the region, an honest one-liner when it does not.

Data: glass.adapters.air (Open-Meteo air-quality; fixture demo).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import air as ad
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans

SCALE_MAX = 300.0
BANDS = [50, 100, 150, 200, 300]


def _header(g: Glass, d: Dict[str, Any], y: int) -> int:
    g.text((MARGIN, y), "Air", sans(34, 700), BLACK)
    x = MARGIN + g.text_size("Air", sans(34, 700))[0] + 14
    g.text((x, y + 12), g.fit_text(f"{d['place']} · {d['date_label']}", g.w // 2 - x + 60, sans(16, 500)), sans(16, 500), DARK)
    g.text((g.w - MARGIN, y + 4), d["now"], mono(18, 500), BLACK, anchor="ra")
    g.text((g.w - MARGIN, y + 28), f"UV {d['uv']:.1f} · {d['uv_label']}", sans(15, 600), DARK, anchor="ra")
    return y + 52


def _aqi(g: Glass, d: Dict[str, Any], x: int, y: int, w: int) -> int:
    v = d["aqi"]
    g.text((x - 4, y), f"{v:.0f}", mono(72, 700), BLACK)
    nw = g.text_size(f"{v:.0f}", mono(72, 700))[0]
    g.label((x + nw + 10, y + 14), "US AQI", 12, DARK)
    g.text((x + nw + 10, y + 34), g.fit_text(d["aqi_label"], w - nw - 14, sans(22, 700)), sans(22, 700), BLACK)
    if d.get("eaqi") is not None:
        g.text((x + nw + 10, y + 64), f"EU index {d['eaqi']}", sans(13, 500), DARK)
    # the scale bar
    by = y + 100
    prev = 0
    for i, hi in enumerate(BANDS):
        x0 = x + int(prev / SCALE_MAX * w)
        x1 = x + int(hi / SCALE_MAX * w)
        g.rect((x0, by, x1 - 2, by + 10), fill=LIGHT if i % 2 == 0 else DARK)
        prev = hi
    mx = x + int(min(v, SCALE_MAX) / SCALE_MAX * w)
    g.d.polygon([(mx, by - 3), (mx - 7, by - 13), (mx + 7, by - 13)], fill=BLACK)
    for lab, val in (("0", 0), ("50", 50), ("100", 100), ("150", 150), ("200", 200), ("300", 300)):
        g.text((x + int(val / SCALE_MAX * w), by + 14), lab, mono(11, 500), DARK,
               anchor="la" if val == 0 else "ra" if val == 300 else "ma")
    lo, hi = d["aqi_range"]
    g.text((x, by + 34), f"today {lo:.0f}–{hi:.0f} · good to 50, moderate to 100", sans(13, 500), DARK)
    return by + 54


def _uv(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, h: int) -> None:
    g.label((x, y), "UV today", 12, DARK)
    g.text((x + w, y - 2), f"peak {d['uv_peak']:.1f} at {d['uv_peak_at']}", sans(13, 600), BLACK, anchor="ra")
    top, bot = y + 22, y + h - 30
    vals = d["uv_hours"] or [0] * 24
    vmax = max(12.0, max(vals))
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        px = x + int(i / max(1, n - 1) * w)
        py = bot - int(v / vmax * (bot - top))
        pts.append((px, py))
    g.d.polygon([(x, bot)] + pts + [(x + w, bot)], fill=LIGHT)
    g.d.line(pts, fill=BLACK, width=2)
    g.d.line((x, bot, x + w, bot), fill=DARK, width=1)
    # UV 3 threshold hairline
    ty = bot - int(3 / vmax * (bot - top))
    g.dots(x, ty, x + w, 4, DARK)
    g.text((x + w, ty - 12), "3", mono(11, 500), DARK, anchor="ra")
    # now marker
    hn = min(n - 1, d["hour_now"])
    g.d.line((pts[hn][0], top, pts[hn][0], bot), fill=DARK, width=1)
    g.circle(pts[hn][0], pts[hn][1], 5, fill=WHITE, outline=BLACK, width=2)
    for hh in (0, 6, 12, 18, 23):
        if hh < n:
            g.text((pts[hh][0], bot + 4), f"{hh:02d}", mono(11, 500), DARK, anchor="la" if hh == 0 else "ra" if hh == 23 else "ma")
    if d["protect"]:
        a, b = d["protect"]
        g.text((x, bot + 18), f"protect {a}–{b}", sans(13, 600), BLACK)
    else:
        g.text((x, bot + 18), "no protection needed today", sans(13, 500), DARK)


def _pollutants(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, cols: int, rh: int = 34) -> int:
    g.label((x, y), "vs WHO 2021 guideline", 12, DARK)
    g.hairline(x, y + 16, x + w, DARK)
    y += 26
    cw = w // cols
    nf, vf, uf = sans(15, 700), mono(16, 700), sans(11, 500)
    for i, p in enumerate(d["pollutants"]):
        c, r = i % cols, i // cols
        px, py = x + c * cw, y + r * rh
        bw = cw - 24
        g.text((px, py), p["name"], nf, BLACK)
        val = f"{p['display']}"
        g.text((px + bw, py), val, vf, BLACK, anchor="ra")
        vw = g.text_size(val, vf)[0]
        g.text((px + bw - vw - 6, py + 4), p["unit"], uf, DARK, anchor="ra")
        # bar: guideline = full width; over → black
        by = py + 21
        g.rect((px, by, px + bw, by + 5), outline=LIGHT, width=1)
        fill_w = int(min(1.0, p["ratio"]) * bw)
        if fill_w > 0:
            g.rect((px, by, px + fill_w, by + 5), fill=BLACK if p["ratio"] > 1 else DARK)
        g.text((px, by + 8), f"WHO {p['guideline'] / (1000 if p['key'] == 'carbon_monoxide' else 1):g} · {p['window']}", sans(11, 500), DARK)
        if p["ratio"] > 1:
            g.text((px + bw, by + 8), f"x{p['ratio']:.1f} over", sans(11, 700), BLACK, anchor="ra")
    rows = (len(d["pollutants"]) + cols - 1) // cols
    y += rows * rh
    if d["pollen"]:
        g.text((x, y + 2), "pollen  " + " · ".join(f"{p['name']} {p['value']:.0f}" for p in d["pollen"][:4]), sans(13, 500), DARK)
    elif d["pollen_note"]:
        g.text((x, y + 2), d["pollen_note"], sans(13, 500), DARK)
    return y + 20


@component(
    "air", "Air",
    "US AQI numeral + category on a six-band scale, the day's UV curve with peak and protect window, "
    "six pollutants as bars against their WHO 2021 guideline, pollen where modelled.",
    params={"place": "city", "lat": "", "lon": "", "demo": "1 → fixture"},
    fetch=ad.fetch,
    native_hint="type:'air' {aqi:u16, eaqi:u8, uv:u8×24 (×10), pm25, pm10, no2, o3, so2, co: u16} ≈ 44 B; refresh hourly",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        y = _header(g, d, MARGIN)
        lw = 380
        _aqi(g, d, MARGIN, y, lw)
        ux = MARGIN + lw + 40
        g.vline(ux - 20, y, y + 170, LIGHT)
        _uv(g, d, ux, y + 4, g.w - MARGIN - ux, 172)
        py = y + 186
        g.hairline(MARGIN, py - 8, g.w - MARGIN, LIGHT)
        _pollutants(g, d, MARGIN, py, g.w - 2 * MARGIN, cols=3, rh=50)
    else:
        g = Glass(PORTRAIT)
        y = _header(g, d, MARGIN)
        y = _aqi(g, d, MARGIN, y, g.w - 2 * MARGIN) + 10
        g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
        _uv(g, d, MARGIN, y + 12, g.w - 2 * MARGIN, 190)
        y += 216
        g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
        _pollutants(g, d, MARGIN, y + 12, g.w - 2 * MARGIN, cols=2, rh=50)
    return g.snap()
