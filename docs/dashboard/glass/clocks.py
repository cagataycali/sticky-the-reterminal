"""
🕰 clocks — the people you work with, in their hour.

Six places, one glance: the local time large in mono, the offset from home
("+9½"), the weekday only when it differs, and a filled disc when that place is
awake (07–22) versus an outlined one when it sleeps. The right half is the part a
phone's world clock never shows: a 24-hour strip in HOME hours, one band per
place shaded where it is awake, a NOW line through all of them, and under it the
count of awake places per hour with the peak run set black — "best to call
everyone: 10–13, 5 of 6 awake". Data: glass.adapters.clocks (zoneinfo, no network).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import clocks as ck
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans

AWAKE = ck.AWAKE


def _rows(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, h: int) -> None:
    rows = d["rows"]
    rh = h / len(rows)
    for i, r in enumerate(rows):
        ry = round(y + i * rh)
        if i:
            g.hairline(x, ry, x + w, LIGHT)
        cy = ry + round(rh / 2)
        # awake disc
        if r["awake"]:
            g.circle(x + 7, cy, 6, fill=BLACK)
        else:
            g.circle(x + 7, cy, 6, outline=DARK, width=2)
        tf = mono(34, 700 if r["awake"] else 500)
        g.text((x + 24, cy), r["time"], tf, BLACK if r["awake"] else DARK, anchor="lm")
        lx = x + 24 + g.text_size("00:00", tf)[0] + 16
        name = g.fit_text(r["label"], w - (lx - x) - 64, sans(17, 700 if r["home"] else 600))
        g.text((lx, cy - 11), name, sans(17, 700 if r["home"] else 600), BLACK)
        sub = r["abbr"] + (f" · {r['weekday']}" if r["weekday"] else "")
        g.text((lx, cy + 9), sub, sans(12, 500), DARK)
        g.text((x + w, cy), r["offset_label"], mono(16, 700 if not r["home"] else 500), BLACK if not r["home"] else DARK, anchor="rm")


def _strip(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, h: int) -> None:
    rows = d["rows"]
    n = len(rows)
    g.label((x, y), "24 h in home time · shaded = awake 07–22", 11, DARK)
    y += 20
    axis_h = 16
    count_h = 44
    band_h = (h - 20 - axis_h - count_h - 24) / n
    hw = w / 24
    for i, r in enumerate(rows):
        by0 = round(y + i * band_h)
        by1 = round(y + (i + 1) * band_h) - 3
        g.rect((x, by0, x + w, by1), outline=LIGHT, width=1)
        for hh in range(24):
            lh = (hh + r["offset"]) % 24
            if AWAKE[0] <= lh < AWAKE[1]:
                g.rect((round(x + hh * hw), by0, round(x + (hh + 1) * hw), by1), fill=DARK if not r["home"] else BLACK)
        # label in a white knockout so it reads on band or gap alike
        lf = sans(11, 600)
        tw_, th_ = g.text_size(r["label"], lf)
        cy = (by0 + by1) // 2
        g.rect((x + 3, cy - th_ // 2 - 3, x + 3 + tw_ + 8, cy + th_ // 2 + 4), fill=WHITE, outline=LIGHT, width=1)
        g.text((x + 7, cy), r["label"], lf, BLACK, anchor="lm")
    ys = y + round(band_h * n)
    # hour axis
    for hh in (0, 6, 12, 18, 24):
        ax = round(x + hh * hw)
        g.vline(ax, ys, ys + 5, DARK)
        g.text((ax, ys + 6), f"{hh % 24:02d}", mono(11, 500), DARK, anchor="ma" if 0 < hh < 24 else ("la" if hh == 0 else "ra"))
    yc = ys + axis_h + 10
    # awake-count bars with the peak run black
    top = yc
    bot = yc + count_h - 14
    for hh, c in enumerate(d["count"]):
        bh = round((bot - top) * c / max(1, d["n"]))
        g.rect((round(x + hh * hw) + 1, bot - bh, round(x + (hh + 1) * hw) - 1, bot), fill=BLACK if d["good"][hh] else LIGHT)
    g.text((x, bot + 3), f"awake places per hour · peak {d['best']} of {d['n']}", sans(11, 500), DARK)
    # NOW line through everything
    nx = round(x + d["hnow"] * hw)
    g.d.line([(nx, y - 4), (nx, bot)], fill=BLACK, width=2)
    g.text((min(max(nx, x + 14), x + w - 14), y - 16), d["now"], mono(11, 700), BLACK, anchor="md")


def _call_line(d: Dict[str, Any]) -> str:
    def span(w):
        return f"{w[0]:02d}–{w[1] % 24:02d}"
    if d["open_now"]:
        return f"Best hour to reach everyone is NOW — {span(d['open_now'])}, {d['best']} of {d['n']} awake."
    if d["next_window"]:
        w = d["next_window"]
        dh = (w[0] - d["hnow"]) % 24
        hrs, mins = int(dh), round((dh - int(dh)) * 60)
        when = f"in {hrs} h {mins:02d}" if hrs else f"in {mins} min"
        return f"Best window {span(w)} ({d['best']} of {d['n']} awake) · {when}."
    return "No shared waking hour today."


@component(
    "clocks", "World clocks",
    "The people you work with, in their hour: six places with local time, offset from home, awake/asleep disc; "
    "a 24-h strip in home time with each place's waking band, a NOW line, and the awake-count per hour with the "
    "peak run marked — the best time to reach everyone.",
    params={"zones": "Label=Area/City,… (or ~/.tiny/sticky-clocks.json)", "at": "YYYY-MM-DD HH:MM pin"},
    fetch=ck.fetch,
    native_hint="type:'clocks' {rows:[{label,time,offset,awake}], count:[24], now} ≈ 300 B",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        g.label((MARGIN, MARGIN), f"clocks · home {d['home']['label']}", 12, DARK)
        g.text((g.w - MARGIN, MARGIN - 2), d["date_label"], sans(13, 500), DARK, anchor="ra")
        top = MARGIN + 26
        lw = 372
        _rows(g, d, MARGIN, top, lw, g.h - top - MARGIN - 30)
        sx = MARGIN + lw + 28
        g.vline(sx - 14, top, g.h - MARGIN, LIGHT)
        _strip(g, d, sx, top + 16, g.w - MARGIN - sx, g.h - top - 16 - MARGIN - 30)
        g.hairline(MARGIN, g.h - MARGIN - 24, g.w - MARGIN, LIGHT)
        g.text((MARGIN, g.h - MARGIN - 16), g.fit_text(_call_line(d), g.w - 2 * MARGIN, sans(14, 600)), sans(14, 600), BLACK)
    else:
        g = Glass(PORTRAIT)
        g.label((MARGIN, MARGIN), f"clocks · home {d['home']['label']}", 12, DARK)
        g.text((g.w - MARGIN, MARGIN - 2), d["date_label"], sans(13, 500), DARK, anchor="ra")
        top = MARGIN + 26
        _rows(g, d, MARGIN, top, g.w - 2 * MARGIN, 372)
        y = top + 372 + 14
        g.hairline(MARGIN, y, g.w - MARGIN, DARK)
        _strip(g, d, MARGIN, y + 30, g.w - 2 * MARGIN, g.h - (y + 30) - MARGIN - 56)
        g.hairline(MARGIN, g.h - MARGIN - 46, g.w - MARGIN, LIGHT)
        yy = g.h - MARGIN - 38
        for ln in g.wrap(_call_line(d), g.w - 2 * MARGIN, sans(14, 600), max_lines=2):
            g.text((MARGIN, yy), ln, sans(14, 600), BLACK)
            yy += 19
    return g.snap()
