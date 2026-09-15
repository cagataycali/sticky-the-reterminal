"""
🕰 now — the lock screen. What a glance should answer, in reading order:
what time is it, what day, what's outside, what's next, is anything waiting.

Landscape: 150 px mono clock (JetBrains Mono 700) with the date under it,
weather column on the right (glyph, temperature, condition, hi/lo), a hairline,
then the NEXT event (start, title, location, "in 2 h 10 min" / "now") and a
bottom strip (sunrise · sunset · N events today · N new).
Portrait: the same, stacked and centred.

Data: glass.adapters.now (weather + calendar + inbox; each with fixtures).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component, icons
from .adapters import now as nowd
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _in(m: int) -> str:
    if m < 1:
        return "now"
    if m < 60:
        return f"in {m} min"
    h, r = divmod(m, 60)
    return f"in {h} h" + (f" {r:02d}" if r else "")


def _weather(g: Glass, w: Dict[str, Any], x: int, y: int, right: int, r: int = 40) -> int:
    icons.draw(g, w["glyph"], x + r, y + r, r)
    tx = x + 2 * r + 18
    t = f"{round(w['temp'])}°"
    big = mono(56, 700)
    g.text((tx, y - 4), t, big, BLACK)
    g.text((tx + 2, y + 54), g.fit_text(w["condition"], right - tx - 2, sans(20, 600)), sans(20, 600), BLACK)
    f = mono(14, 500)
    g.text((tx + 2, y + 78), g.fit_text(f"H {round(w['hi'])}°  L {round(w['lo'])}°  ·  {w['place']}", right - tx - 2, f), f, DARK)
    bits = []
    if w.get("feels") is not None:
        bits.append(f"feels {round(w['feels'])}°")
    if w.get("wind") is not None:
        bits.append(f"wind {round(w['wind'])} {w.get('wind_unit', '')}".rstrip())
    if w.get("humidity") is not None:
        bits.append(f"{round(w['humidity'])} % rh")
    if w.get("precip") is not None:
        bits.append(f"rain {round(w['precip'])} %")
    if bits:
        g.text((tx + 2, y + 98), g.fit_text("  ·  ".join(bits), right - tx - 2, f), f, DARK)
    return y + 2 * r + 8


def _next(g: Glass, d: Dict[str, Any], x: int, y: int, right: int) -> int:
    n = d["next"]
    g.label((x, y), "next", 13, DARK)
    if n is None:
        g.text((x, y + 22), "Nothing else today.", sans(24, 500), DARK)
        return y + 56
    when = "now" if n["ongoing"] else _in(n["in_min"])
    g.text((right, y - 2), when, mono(16, 700), BLACK, anchor="ra")
    g.text((x, y + 22), n["start"], mono(30, 700), BLACK)
    tx = x + g.text_size(n["start"], mono(30, 700))[0] + 16
    g.text((tx, y + 24), g.fit_text(n["title"], right - tx, sans(28, 600)), sans(28, 600), BLACK)
    sub = f"until {n['end']}" + (f"  ·  {n['location']}" if n.get("location") else "")
    g.text((x, y + 62), g.fit_text(sub, right - x, sans(17, 400)), sans(17, 400), DARK)
    return y + 90


def _then(g: Glass, d: Dict[str, Any], x: int, y: int, right: int, rows: int) -> None:
    """The events after NEXT — one line each, mono time + title."""
    items = d.get("then") or []
    if not items:
        return
    g.label((x, y), "then", 13, DARK)
    y += 20
    tf, sf = mono(16, 700), sans(17, 500)
    for e in items[:rows]:
        g.text((x, y + 1), e["start"], tf, DARK)
        tx = x + 58
        line = e["title"] + (f"  ·  {e['location']}" if e.get("location") else "")
        g.text((tx, y), g.fit_text(line, right - tx, sf), sf, BLACK)
        y += 26


def _strip(g: Glass, d: Dict[str, Any], y: int) -> None:
    w = d["weather"]
    parts = [f"↑ {w['sunrise']}", f"↓ {w['sunset']}",
             f"{d['events_today']} event{'s' if d['events_today'] != 1 else ''} today"]
    if d["all_day"]:
        parts.append(d["all_day"][0])
    g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
    f = mono(15, 500)
    s = "   ·   ".join(parts)
    g.text((MARGIN, y + 12), g.fit_text(s, g.w - 2 * MARGIN - 120, f), f, DARK)
    if d["unread"]:
        lab = f"{d['unread']} new"
        wpx = g.text_size(lab, sans(14, 700))[0] + 18
        g.rect((g.w - MARGIN - wpx, y + 9, g.w - MARGIN, y + 31), fill=BLACK, radius=11)
        g.text((g.w - MARGIN - wpx // 2, y + 20), lab, sans(14, 700), WHITE, anchor="mm")


@component(
    "now", "Now",
    "Lock screen: big clock, date, weather, the next event with a countdown, sunrise/sunset "
    "and unread count.",
    params={"place": "city for weather", "units": "f|c", "ics_url": "calendar feed", "tz": "IANA zone",
            "clock": "HH:MM override", "demo": "1 → all fixtures"},
    fetch=nowd.fetch,
    native_hint="type:'now' {clock, date, temp, cond:u8, hi, lo, next:{t,title,loc,in_min}, sunrise, sunset, n_events, n_new} ≈ 200 B; "
                "the clock is the ONE field that wants a per-minute partial refresh — a native card wins here",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    w = d["weather"]
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        clock = mono(150, 700)                      # 5 glyphs ≈ 450 px; 200 px ate the weather column
        bb = g.text((MARGIN - 6, MARGIN - 14), d["clock"], clock, BLACK)
        g.text((MARGIN, bb[3] + 18), d["date"], sans(30, 500), BLACK)
        col_x = 524
        g.vline(col_x - 26, MARGIN + 10, 236, LIGHT)
        _weather(g, w, col_x, MARGIN + 16, g.w - MARGIN)
        y = 252
        g.hairline(MARGIN, y, g.w - MARGIN, DARK)
        _next(g, d, MARGIN, y + 16, col_x - 40)
        _then(g, d, col_x - 8, y + 16, g.w - MARGIN, rows=4)
        _strip(g, d, g.h - MARGIN - 34)
    else:
        g = Glass(PORTRAIT)
        clock = mono(136, 700)
        bb = g.text((g.w // 2, MARGIN + 6), d["clock"], clock, BLACK, anchor="ma")
        g.text((g.w // 2, bb[3] + 16), d["date"], sans(26, 500), BLACK, anchor="ma")
        y = bb[3] + 72
        g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
        _weather(g, w, MARGIN + 10, y + 26, g.w - MARGIN, r=44)
        y2 = y + 26 + 88 + 30
        g.hairline(MARGIN, y2, g.w - MARGIN, DARK)
        y3 = _next(g, d, MARGIN, y2 + 16, g.w - MARGIN)
        _then(g, d, MARGIN, y3 + 14, g.w - MARGIN, rows=4)
        _strip(g, d, g.h - MARGIN - 34)
    return g.snap()
