"""
⏳ countdown — one big number, and the rest of the calendar's promises.

Hero: the nearest date as a numeral the height of the card (mono 700, auto-fit),
"days" beside it, the label, the full date, and — when the event has a start —
a progress rule from `from` to the day. Right: the upcoming list (label · date ·
days) and, in DARK, the "since" counters (days since the last incident, since the
repo started). When nothing is ahead the hero counts up. Data: params or
~/.tiny/sticky-countdowns.json.
"""
from __future__ import annotations

from typing import Any, Dict, List

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import countdown as cd
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _header(g: Glass, d: Dict[str, Any], y: int) -> int:
    g.label((MARGIN, y), "Countdown", 13, DARK)
    g.text((g.w - MARGIN, y - 2), d["now"], mono(16, 500), DARK, anchor="ra")
    return y + 22


def _hero(g: Glass, h: Dict[str, Any], x: int, y: int, w: int, h_px: int) -> int:
    num = str(h["days"])
    # numeral fits the width with room for the unit word
    px = 200 if g.w > g.h else 170
    while g.text_size(num, mono(px, 700))[0] > w - 130 and px > 60:
        px -= 8
    nf = mono(px, 700)
    nw, nh = g.text_size(num, nf)
    g.text((x - 8, y - int(px * 0.12)), num, nf, BLACK)
    ux = x + nw + 6
    unit = "day" if h["days"] == 1 else "days"
    g.text((ux, y + int(px * 0.62) - 40), unit, sans(30, 700), BLACK)
    g.text((ux, y + int(px * 0.62)), "to" if h["kind"] == "to" else "since", sans(20, 500), DARK)
    ly = y + int(px * 0.92)
    g.text((x, ly), g.fit_text(h["label"], w, sans(30, 700)), sans(30, 700), BLACK)
    ly += 40
    extra = f" · {h['weeks']} wk {h['rem_days']} d" if h["days"] >= 14 else ""
    g.text((x, ly), h["long_label"] + extra, sans(16, 500), DARK)
    ly += 30
    if h.get("progress") is not None:
        g.rect((x, ly, x + w, ly + 6), outline=LIGHT, width=1)
        g.rect((x, ly, x + int(h["progress"] * w), ly + 6), fill=BLACK)
        g.text((x, ly + 12), h["from_label"], sans(12, 500), DARK)
        g.text((x + w, ly + 12), f"{h['progress'] * 100:.0f} % of the way", sans(12, 500), DARK, anchor="ra")
        ly += 30
    return ly


def _list(g: Glass, title: str, items: List[Dict[str, Any]], x: int, y: int, w: int, y_max: int, dark: bool = False) -> int:
    if not items:
        return y
    g.label((x, y), title, 12, DARK)
    g.hairline(x, y + 16, x + w, DARK)
    y += 26
    lf, df, nf = sans(16, 600), sans(13, 500), mono(22, 700)
    for it in items:
        if y + 40 > y_max:
            break
        num = str(it["days"])
        nw = g.text_size(num, nf)[0]
        g.text((x + w - 14, y - 2), num, nf, DARK if dark else BLACK, anchor="ra")
        g.text((x + w, y + 8), "d", sans(12, 500), DARK, anchor="ra")
        g.text((x, y), g.fit_text(it["label"], w - nw - 30, lf), lf, DARK if dark else BLACK)
        g.text((x, y + 20), it["date_label"] + (" · yearly" if it["recurs"] else ""), df, DARK)
        y += 42
    return y + 6


@component(
    "countdown", "Countdown",
    "Days to the nearest date as a card-height numeral with label, date and progress; the rest of "
    "the upcoming list; days-since counters. Fed by params or ~/.tiny/sticky-countdowns.json.",
    params={"to": "YYYY-MM-DD one-off", "label": "", "from": "progress start", "events": "[{label, date, from?, every?, since?}]", "date": "anchor", "demo": "1 → fixture"},
    fetch=cd.fetch,
    native_hint="type:'countdown' {hero:{days:u16, label, date}, list:[{label, days:u16}]} ≈ 160 B; refresh daily at 00:00",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        y = _header(g, d, MARGIN)
        if not d["hero"]:
            g.text((MARGIN, g.h // 2 - 20), "Nothing on the horizon.", sans(30, 700), BLACK)
            return g.snap()
        lw = 470
        _hero(g, d["hero"], MARGIN, y + 6, lw, g.h - y - 2 * MARGIN)
        rx = MARGIN + lw + 40
        g.vline(rx - 20, y, g.h - MARGIN, LIGHT)
        ry = _list(g, "Also ahead", d["upcoming"][:4], rx, y + 4, g.w - MARGIN - rx, g.h - MARGIN - 100)
        _list(g, "Since", d["since"][:3], rx, ry + 8, g.w - MARGIN - rx, g.h - MARGIN, dark=True)
        g.text((MARGIN, g.h - MARGIN - 14), d["today_label"], sans(13, 500), DARK)
    else:
        g = Glass(PORTRAIT)
        y = _header(g, d, MARGIN)
        if not d["hero"]:
            g.text((MARGIN, g.h // 2 - 20), "Nothing ahead.", sans(30, 700), BLACK)
            return g.snap()
        ly = _hero(g, d["hero"], MARGIN, y + 6, g.w - 2 * MARGIN, 300)
        ly += 16
        g.hairline(MARGIN, ly, g.w - MARGIN, LIGHT)
        ly = _list(g, "Also ahead", d["upcoming"][:4], MARGIN, ly + 14, g.w - 2 * MARGIN, g.h - MARGIN - 120)
        _list(g, "Since", d["since"][:2], MARGIN, ly + 8, g.w - 2 * MARGIN, g.h - MARGIN - 20, dark=True)
        g.text((g.w - MARGIN, g.h - MARGIN - 14), d["today_label"], sans(13, 500), DARK, anchor="ra")
    return g.snap()
