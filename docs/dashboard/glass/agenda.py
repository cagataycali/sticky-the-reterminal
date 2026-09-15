"""
▦ agenda — where the time is, seven days out.

A heatmap the eye reads in one pass: rows are days (today first, bold), columns
are hours 06–22, each cell's gray is how much of that hour is booked (LIGHT ≤ 30
min, DARK ≤ 50, BLACK full). Left of each row: the weekday and date; right: the
count, first–last, and the longest free window inside 09–18 — the number that
matters when you're deciding when to do the real work. The header carries the
week's totals and names the busiest and freest days. Landscape puts the grid
across the page; portrait stacks the same rows taller with the titles under.
Data: glass.adapters.agenda (calendar fixture in demo).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import agenda as ag
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _dur(m: int) -> str:
    if m <= 0:
        return "—"
    h, mm = divmod(int(m), 60)
    return f"{h} h" if h and not mm else (f"{h} h {mm:02d}" if h else f"{mm} min")


def _shade(v: int) -> int:
    if v <= 0:
        return WHITE
    if v <= 30:
        return LIGHT
    if v <= 50:
        return DARK
    return BLACK


def _grid(g: Glass, d: Dict[str, Any], x0: int, y0: int, w: int, row_h: int, left: int, right: int, titles: bool = False) -> int:
    nh = d["h1"] - d["h0"]
    gx0, gx1 = x0 + left, x0 + w - right
    cw = (gx1 - gx0) / nh
    # hour axis
    for h in range(d["h0"], d["h1"] + 1, 2):
        x = gx0 + int((h - d["h0"]) * cw)
        g.text((x, y0), f"{h:02d}", mono(11, 500), DARK, anchor="ma")
    y = y0 + 16
    for day in d["days"]:
        bold = day["today"]
        f = sans(14, 700 if bold else 500)
        g.text((x0, y + 2), day["dow"], f, BLACK if not day["weekend"] or bold else DARK)
        g.text((x0 + 36, y + 2), str(day["dom"]), mono(13, 700 if bold else 500), BLACK)
        if bold:
            g.rect((x0 - 6, y + 2, x0 - 4, y + row_h - 8), fill=BLACK)
        ch = row_h - 10 if not titles else 16
        for i, v in enumerate(day["busy"]):
            xa = gx0 + int(i * cw)
            xb = gx0 + int((i + 1) * cw) - 1
            g.rect((xa, y, xb, y + ch), fill=_shade(v), outline=None)
        g.rect((gx0, y, gx1 - 1, y + ch), outline=LIGHT, width=1)
        if day["all_day"]:
            g.rect((gx0, y + ch + 2, gx1 - 1, y + ch + 3), fill=DARK)
        # right column: n · first–last · free
        rx = x0 + w
        fr = day["free"]
        if right >= 150:
            g.text((rx - right + 8, y), f"{day['n']}", mono(14, 700), BLACK)
            g.text((rx - right + 30, y + 1), f"{day['first']}–{day['last']}" if day["n"] else "free day", sans(12, 500), DARK)
            free_s = f"{_dur(fr['min'])} free" + (f" {fr['start']}" if fr["min"] else "")
            g.text((rx, y + 1), free_s, sans(12, 600 if day["date"] == d["freest"] else 500), BLACK if day["date"] == d["freest"] else DARK, anchor="ra")
        else:
            g.text((rx, y + 1), f"{day['n']} · {_dur(fr['min'])} free", sans(12, 500), BLACK if day["date"] == d["freest"] else DARK, anchor="ra")
        if titles:
            ty = y + ch + 6
            g.text((gx0, ty), g.fit_text("  ·  ".join(day["titles"]), gx1 - gx0, sans(11, 500)), sans(11, 500), DARK)
        y += row_h
    # legend
    lx = gx0
    for lab, v in (("up to 30 min", 20), ("up to 50", 45), ("the hour", 60)):
        g.rect((lx, y + 2, lx + 12, y + 12), fill=_shade(v), outline=LIGHT if v == 60 else None)
        g.text((lx + 16, y), lab, sans(11, 500), DARK)
        lx += g.text_size(lab, sans(11, 500))[0] + 34
    return y + 16


@component(
    "agenda", "Agenda",
    "Seven days as a busy-hours heatmap (06–22; gray = minutes booked in that hour), each row with its count, "
    "first–last and the longest free window inside 09–18. Header totals name the busiest and the freest day.",
    params={"ics_url": "calendar feed", "tz": "IANA zone", "date": "YYYY-MM-DD start override", "demo": "1 → fixture"},
    fetch=ag.fetch,
    native_hint="type:'agenda' {days:[{dow,dom,busy:'16 hex nibbles',n,first,last,free}]} ≈ 7×40 B; the firmware chart card could draw the strips",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    busiest = next(x for x in d["days"] if x["date"] == d["busiest"])
    freest = next(x for x in d["days"] if x["date"] == d["freest"])
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        g.text((MARGIN, MARGIN - 6), "Next 7 days", sans(26, 700), BLACK)
        g.text((MARGIN + g.text_size("Next 7 days", sans(26, 700))[0] + 12, MARGIN + 4), d["range_label"] + ("  ·  demo" if d["demo"] else ""), sans(13, 500), DARK)
        g.text((g.w - MARGIN, MARGIN - 2), f"{d['week_n']} events · {_dur(d['week_min'])} booked", sans(13, 600), BLACK, anchor="ra")
        g.text((g.w - MARGIN, MARGIN + 18), f"busiest {busiest['dow']}  ·  freest {freest['dow']} ({_dur(freest['free']['min'])} from {freest['free']['start']})", sans(12, 500), DARK, anchor="ra")
        g.hairline(MARGIN, MARGIN + 40, g.w - MARGIN, BLACK)
        _grid(g, d, MARGIN + 6, MARGIN + 52, g.w - 2 * MARGIN - 6, 50, 70, 230)
    else:
        g = Glass(PORTRAIT)
        g.text((MARGIN, MARGIN - 6), "Next 7 days", sans(26, 700), BLACK)
        g.text((g.w - MARGIN, MARGIN + 2), d["range_label"] + ("  ·  demo" if d["demo"] else ""), sans(12, 500), DARK, anchor="ra")
        g.text((MARGIN, MARGIN + 32), f"{d['week_n']} events · {_dur(d['week_min'])} booked  ·  busiest {busiest['dow']}, freest {freest['dow']}", sans(12, 500), DARK)
        g.hairline(MARGIN, MARGIN + 52, g.w - MARGIN, BLACK)
        _grid(g, d, MARGIN + 6, MARGIN + 64, g.w - 2 * MARGIN - 6, 88, 66, 120, titles=True)
    return g.snap()
