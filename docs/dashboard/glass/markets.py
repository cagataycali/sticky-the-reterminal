"""
📈 markets — a handful of tickers, the way a paper prints them.

Six rows, each a sentence: the name, the last price in heavy mono, the day's
change with a filled ▼ for down and an outlined ▲ for up (colour does the job on
a phone; shape does it here), a three-month line with the area under it in the
light gray, and a 52-week range bar with today's mark on it — the one number a
sparkline cannot show. Header: up/down count, the day's strongest and weakest,
quote time. Stale quotes (network down) are flagged per row, never hidden.
Data: glass.adapters.markets (Yahoo chart endpoint, no key; fixture fallback).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import markets as mk
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def fmt_price(v: float) -> str:
    a = abs(v)
    if a >= 10000:
        return f"{v:,.0f}"
    if a >= 1000:
        return f"{v:,.1f}"
    if a >= 100:
        return f"{v:.2f}"
    if a >= 10:
        return f"{v:.2f}"
    return f"{v:.4f}"


def fmt_change(r: Dict[str, Any]) -> str:
    c, p = r["change"], r["change_pct"]
    sign = "+" if c >= 0 else "−"
    a = abs(c)
    cs = f"{a:,.0f}" if a >= 1000 else (f"{a:.2f}" if a >= 0.01 else f"{a:.4f}")
    return f"{sign}{cs}  {sign}{abs(p):.2f} %"


def _arrow(g: Glass, x: int, cy: int, up: bool) -> None:
    if up:
        g.d.polygon([(x, cy + 5), (x + 10, cy + 5), (x + 5, cy - 5)], outline=BLACK, fill=WHITE)
        g.d.polygon([(x, cy + 5), (x + 10, cy + 5), (x + 5, cy - 5)], outline=BLACK)
    else:
        g.d.polygon([(x, cy - 5), (x + 10, cy - 5), (x + 5, cy + 5)], fill=BLACK)


def _row(g: Glass, r: Dict[str, Any], x: int, y: int, w: int, h: int, spark_w: int, wide: bool) -> None:
    cy = y + h // 2
    name_w = 112 if wide else 124
    # name + symbol (two lines, top-aligned with the price)
    g.text((x, y + 9), g.fit_text(r["label"], name_w, sans(17, 700)), sans(17, 700), BLACK)
    sub = r["symbol"] + ("  · stale" if r.get("stale") else "")
    g.text((x, y + 34), g.fit_text(sub, name_w, mono(11, 500)), mono(11, 500), DARK)
    # price (line 1) and change with its arrow (line 2), right-aligned in one column
    px = x + (262 if wide else 270)
    g.text((px, y + 6), fmt_price(r["last"]), mono(26, 700), BLACK, anchor="ra")
    up = r["change"] >= 0
    ch = fmt_change(r)
    cf = mono(13, 700 if not up else 500)
    cw = g.text_size(ch, cf)[0]
    g.text((px, y + 39), ch, cf, BLACK, anchor="ra")
    _arrow(g, px - cw - 18, y + 46, up)
    # 3-month sparkline
    sx = px + 24
    g.sparkline((sx, y + 8, sx + spark_w, y + h - 16), r["closes"], fill=BLACK, width=2, area=LIGHT, mark_last=True)
    # 52-week range bar
    bx0 = sx + spark_w + 22
    bx1 = x + w
    if bx1 - bx0 > 60:
        by = cy + 2
        g.rect((bx0, by - 2, bx1, by + 2), fill=LIGHT)
        mx = bx0 + round((bx1 - bx0) * max(0.0, min(1.0, r["pos52"])))
        g.rect((mx - 2, by - 7, mx + 2, by + 7), fill=BLACK)
        g.text((bx0, by + 10), fmt_price(r["lo52"]), mono(11, 500), DARK)
        g.text((bx1, by + 10), fmt_price(r["hi52"]), mono(11, 500), DARK, anchor="ra")
        g.text(((bx0 + bx1) // 2, by - 10), "52 w", sans(11, 500), DARK, anchor="md")


def _header(g: Glass, d: Dict[str, Any], wide: bool) -> None:
    g.text((MARGIN, MARGIN - 6), "Markets", sans(24, 700), BLACK)
    parts = [f"{d['up']} up · {d['down']} down"]
    if d["best"] and d["worst"] and d["best"] is not d["worst"]:
        parts.append(f"{d['best']['label']} {d['best']['change_pct']:+.1f} %")
        parts.append(f"{d['worst']['label']} {d['worst']['change_pct']:+.1f} %")
    g.text((MARGIN + 116, MARGIN + 4), g.fit_text("  ·  ".join(parts), (g.w - 2 * MARGIN) - 116 - 150, sans(13, 500)), sans(13, 500), DARK)
    when = f"{d['date_label']} {d['time']}" + ("  · demo" if d["demo"] else ("  · some stale" if d["any_stale"] else ""))
    g.text((g.w - MARGIN, MARGIN - 2), when, mono(12, 500), DARK, anchor="ra")


@component(
    "markets", "Markets",
    "Six tickers as a paper prints them: last price in heavy mono, day change with ▲/▼ by shape, a 3-month line "
    "with light area, and a 52-week range bar with today's mark. Header counts up/down and names the day's best and worst.",
    params={"symbols": "AAPL,MSFT,BTC-USD or Label=SYM,… (or ~/.tiny/sticky-markets.json)", "at": "YYYY-MM-DD HH:MM pin"},
    fetch=mk.fetch,
    native_hint="type:'markets' {rows:[{label,last,change_pct,spark:[24],pos52}]} ≈ 400 B",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    rows = d["rows"]
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        _header(g, d, True)
        top = MARGIN + 36
        h = (g.h - top - MARGIN) / max(1, len(rows))
        for i, r in enumerate(rows):
            y = round(top + i * h)
            g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
            _row(g, r, MARGIN, y, g.w - 2 * MARGIN, round(h), 250, True)
    else:
        g = Glass(PORTRAIT)
        _header(g, d, False)
        top = MARGIN + 36
        h = (g.h - top - MARGIN) / max(1, len(rows))
        for i, r in enumerate(rows):
            y = round(top + i * h)
            g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
            _row(g, r, MARGIN, y, g.w - 2 * MARGIN, round(h), 146, False)
    return g.snap()
