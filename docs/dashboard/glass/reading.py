"""
📖 reading — the book on the desk.

A book spine's worth of information: the title set large with the author under
it, then the page as a thick bar — the read part BLACK, the rest a LIGHT rule,
a hairline tick every 50 pages so the bar has a scale — with "p. 246 of 368 ·
67 %" and "122 to go · finishes Mon 14 Sep at this pace". Below, the last 14
days as page bars (a day with no entry is an honest gap, not a smoothed line),
pace in pages per day, and the last note taken. Right (landscape) / bottom
(portrait): "up next" and "finished this year · 4 books · 1 272 pages" as a
short shelf. Nothing on it is a clock: it goes stale gracefully.
Data: glass.adapters.reading (~/.tiny/sticky-reading.json or fixture).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import reading as rd
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _progress(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, h: int = 30) -> int:
    g.rect((x, y, x + w, y + h), fill=WHITE, outline=BLACK, width=1)
    fx = x + int(w * min(1.0, d["pct"]))
    if fx > x:
        g.rect((x, y, fx, y + h), fill=BLACK)
    if d["pages"]:
        for p in range(50, d["pages"], 50):
            tx = x + int(w * p / d["pages"])
            g.vline(tx, y + h + 3, y + h + 7, DARK)
    g.text((x, y + h + 10), f"p. {d['page']} of {d['pages']}", mono(15, 600), BLACK)
    g.text((x + w, y + h + 10), f"{round(d['pct'] * 100)} %", mono(15, 600), BLACK, anchor="ra")
    return y + h + 34


def _pace(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, bar_h: int = 44) -> int:
    g.label((x, y), f"last {d['window']} days", 11, DARK)
    g.text((x + w, y - 2), f"{d['pace']:.0f} pages a day · read on {d['reading_days']} of {d['window']}", sans(13, 500), DARK, anchor="ra")
    y += 20
    g.bars((x, y, x + w, y + bar_h), d["per_day"], fill=BLACK, gap=4)
    g.hairline(x, y + bar_h + 1, x + w, DARK)
    n = len(d["per_day"])
    bw = (w - 4 * (n - 1)) / n
    for i, a in enumerate(d["axis"]):
        g.text((x + int(i * (bw + 4) + bw / 2), y + bar_h + 5), a, mono(11, 500), DARK, anchor="ma")
    return y + bar_h + 20


def _finish_line(d: Dict[str, Any]) -> str:
    if d["left"] == 0:
        return "finished"
    if d["finish"]:
        return f"{d['left']} to go · finishes {d['finish']} at this pace"
    return f"{d['left']} to go"


def _shelf(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, y_max: int) -> None:
    if d["next"]:
        g.label((x, y), "up next", 11, DARK)
        y += 18
        for t in d["next"]:
            if y + 18 > y_max:
                break
            g.text((x, y), g.fit_text(t, w, sans(14, 500)), sans(14, 500), BLACK)
            y += 21
        y += 14
    g.label((x, y), f"finished this year · {d['finished_year']}", 11, DARK)
    y += 18
    g.text((x, y), f"{d['finished_pages']:,} pages".replace(",", " "), sans(14, 600), BLACK)
    y += 24
    for b in d["finished"]:
        if y + 20 > y_max:
            break
        g.text((x, y), b["date"], mono(12, 500), DARK)
        g.text((x + 36, y), g.fit_text(b["title"], w - 36, sans(14, 500)), sans(14, 500), BLACK)
        g.text((x + 36, y + 17), g.fit_text(b["author"], w - 36, sans(11, 500)), sans(11, 500), DARK)
        y += 36


@component(
    "reading", "Reading",
    "The book on the desk: title, a page bar with 50-page ticks, pages to go and the finish date at the "
    "measured pace, the last 14 days as honest page bars (gaps stay gaps), the last note, up next, and this "
    "year's shelf. No clock — it goes stale gracefully.",
    params={"book": "JSON {current:{title,author,pages,started,log:[{date,page}],note}, next:[], finished:[]}", "date": "YYYY-MM-DD override", "demo": "1 → fixture"},
    fetch=rd.fetch,
    native_hint="type:'reading' {title, author, page, pages, pace, finish, per_day:[14], note} ≈ 220 B",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        cw = 500
        g.label((MARGIN, MARGIN), "reading" + ("  ·  demo" if d["demo"] else ""), 12, DARK)
        g.text((g.w - MARGIN, MARGIN - 2), f"day {d['days']} · since {d['started']}", sans(12, 500), DARK, anchor="ra")
        y = MARGIN + 26
        f = sans(38, 700)
        for ln in g.wrap(d["title"], cw, f, max_lines=2):
            g.text((MARGIN, y), ln, f, BLACK)
            y += 46
        g.text((MARGIN, y), d["author"], sans(17, 500), DARK)
        y += 38
        y = _progress(g, d, MARGIN, y, cw)
        g.text((MARGIN, y), _finish_line(d), sans(16, 600), BLACK)
        y += 36
        y = _pace(g, d, MARGIN, y, cw, bar_h=64)
        if d["note"]:
            y += 8
            for ln in g.wrap("“" + d["note"] + "”", cw, sans(14, 500), max_lines=3):
                if y + 20 > g.h - MARGIN:
                    break
                g.text((MARGIN, y), ln, sans(14, 500), DARK)
                y += 20
        sx = MARGIN + cw + 30
        g.vline(sx - 15, MARGIN + 24, g.h - MARGIN, LIGHT)
        _shelf(g, d, sx, MARGIN + 24, g.w - MARGIN - sx, g.h - MARGIN)
    else:
        g = Glass(PORTRAIT)
        w = g.w - 2 * MARGIN
        g.label((MARGIN, MARGIN), "reading" + ("  ·  demo" if d["demo"] else ""), 12, DARK)
        g.text((g.w - MARGIN, MARGIN - 2), f"day {d['days']}", sans(12, 500), DARK, anchor="ra")
        y = MARGIN + 30
        f = sans(36, 700)
        for ln in g.wrap(d["title"], w, f, max_lines=3):
            g.text((MARGIN, y), ln, f, BLACK)
            y += 44
        g.text((MARGIN, y), d["author"], sans(17, 500), DARK)
        y += 40
        y = _progress(g, d, MARGIN, y, w)
        for ln in g.wrap(_finish_line(d), w, sans(16, 600), max_lines=2):
            g.text((MARGIN, y), ln, sans(16, 600), BLACK)
            y += 22
        y += 18
        y = _pace(g, d, MARGIN, y, w, bar_h=80)
        if d["note"]:
            y += 10
            for ln in g.wrap("“" + d["note"] + "”", w, sans(14, 500), max_lines=4):
                g.text((MARGIN, y), ln, sans(14, 500), DARK)
                y += 20
        y += 18
        g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
        _shelf(g, d, MARGIN, y + 12, w, g.h - MARGIN)
    return g.snap()
