"""
🧩 compose — several small truths on one sheet.

A tile protocol: every tile is (fetch, draw) where draw gets a box and must stay
inside it. Presets arrange tiles on a grid; `tiles=` composes your own. Each tile
fetches independently and a failure degrades to that tile's fixture, so one dead
API never blanks the sheet — and the tile says "demo" in its corner when it did.

morning  clock · weather · next   |  air · moon · habits
desk     next (wide) · countdown  |  inbox · habits · word
evening  moon · weather · word    |  habits · countdown · inbox
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component, icons
from .adapters import air as ad_air, arm as ad_arm, calendar as cal, countdown as ad_cd, focus as ad_fo
from .adapters import goals as ad_go, habits as ad_hb, moon as ad_moon, notifications as ad_notif
from .adapters import markets as ad_mk, playing as ad_pl, poster as ad_po, tide as ad_ti, todo as ad_td, weather as ad_wx
from .adapters.now import _next_event
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans
from .arm import draw_arm
from .clock import draw_dial
from .focus import _dur
from .goals import _ring
from .moon import draw_moon
from .now import _in

Box = Tuple[int, int, int, int]
TILES: Dict[str, Tuple[Callable[[Dict[str, Any]], Dict[str, Any]], Callable[[Glass, Dict[str, Any], Box], None]]] = {}

PRESETS = {
    "morning": [["clock", "weather", "next"], ["air", "moon", "habits"]],
    "desk": [["next", "next", "countdown"], ["inbox", "habits", "word"]],
    "evening": [["moon", "weather", "word"], ["habits", "countdown", "inbox"]],
    "work": [["focus", "focus", "goals"], ["next", "arm", "inbox"]],
    "lock": [["clock", "clock", "todo"], ["next", "weather", "inbox"]],
    "weekend": [["tide", "tide", "moon"], ["playing", "markets", "word"]],
}
# the Sticky stands PORTRAIT on the desk — presets may declare a stacked layout for it;
# without one, the landscape rows are flattened into two columns (wide tiles span both).
PRESETS_PORTRAIT = {
    "lock": [["clock"], ["next"], ["todo"], ["weather", "inbox"]],
    "morning": [["clock"], ["weather", "next"], ["air", "moon"], ["habits"]],
    "work": [["focus"], ["goals", "next"], ["arm", "inbox"]],
    "weekend": [["tide"], ["playing"], ["moon", "markets"], ["word"]],
}


def tile(name: str, fetch):
    def deco(fn):
        TILES[name] = (fetch, fn)
        return fn
    return deco


def _frame(g: Glass, box: Box, label: str, demo: bool) -> Tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    g.label((x0, y0), label, 11, DARK)
    if demo:
        g.text((x1, y0 - 1), "demo", sans(11, 500), DARK, anchor="ra")
    return x0, y0 + 20, x1, y1


# ── tiles ────────────────────────────────────────────────────────────────────
def _f_clock(p):
    import datetime as dt
    now = dt.datetime.now(cal.local_tz())
    if p.get("clock"):
        hh, mm = str(p["clock"]).split(":")
        now = now.replace(hour=int(hh), minute=int(mm))
    return {"h": now.hour, "m": now.minute, "clock": now.strftime("%H:%M"), "date": now.strftime("%a %-d %b")}


@tile("clock", _f_clock)
def _t_clock(g: Glass, d: Dict[str, Any], box: Box) -> None:
    x0, y0, x1, y1 = _frame(g, box, "now", False)
    w, h = x1 - x0, y1 - y0
    r = min(w, h - 30) // 2 - 4
    draw_dial(g, x0 + w // 2, y0 + r + 4, r, d["h"], d["m"])
    g.text((x0 + w // 2, y0 + 2 * r + 12), f"{d['clock']}  ·  {d['date']}", sans(14, 600), BLACK, anchor="ma")


@tile("weather", ad_wx.fetch)
def _t_weather(g: Glass, d: Dict[str, Any], box: Box) -> None:
    x0, y0, x1, y1 = _frame(g, box, d.get("place", "weather"), d.get("demo", False))
    w = x1 - x0
    icons.draw(g, d["glyph"], x0 + 34, y0 + 40, 30)
    g.text((x0 + 78, y0 + 4), f"{d['temp']:.0f}°", mono(44, 700), BLACK)
    g.text((x0, y0 + 90), g.fit_text(d["condition"], w, sans(16, 600)), sans(16, 600), BLACK)
    g.text((x0, y0 + 112), f"H {d['hi']:.0f}°  L {d['lo']:.0f}°  ·  {d['wind']:.0f} {d['wind_unit']}", sans(13, 500), DARK)
    if y1 - y0 > 150:
        g.text((x0, y0 + 132), f"↑ {d['sunrise']}  ↓ {d['sunset']}", sans(13, 500), DARK)


def _f_next(p):
    import datetime as dt
    day = cal.fetch_day(p)
    now = dt.datetime.now(cal.local_tz())
    nm = now.hour * 60 + now.minute
    nxt = _next_event(day, nm)
    later = [e for e in day["events"] if e["dt_start"].hour * 60 + e["dt_start"].minute > nm]
    return {"next": nxt, "then": [e for e in later if nxt is None or e["title"] != nxt["title"]][:3],
            "count": len(day["events"]), "demo": bool(p.get("demo"))}


@tile("next", _f_next)
def _t_next(g: Glass, d: Dict[str, Any], box: Box) -> None:
    x0, y0, x1, y1 = _frame(g, box, "next", d.get("demo", False))
    w = x1 - x0
    n = d["next"]
    if not n:
        g.text((x0, y0 + 6), "Nothing else today.", sans(20, 500), DARK)
        return
    g.text((x0, y0 + 2), n["start"], mono(26, 700), BLACK)
    tx = x0 + g.text_size(n["start"], mono(26, 700))[0] + 12
    g.text((tx, y0 + 5), g.fit_text(n["title"], x1 - tx, sans(22, 600)), sans(22, 600), BLACK)
    when = "now" if n["ongoing"] else _in(n["in_min"])
    sub = f"{when}  ·  until {n['end']}" + (f"  ·  {n['location']}" if n.get("location") else "")
    g.text((x0, y0 + 38), g.fit_text(sub, w, sans(13, 500)), sans(13, 500), DARK)
    y = y0 + 64
    for e in d["then"]:
        if y + 20 > y1:
            break
        g.text((x0, y), e["start"], mono(13, 600), DARK)
        g.text((x0 + 50, y), g.fit_text(e["title"], w - 50, sans(14, 500)), sans(14, 500), BLACK)
        y += 22


@tile("air", ad_air.fetch)
def _t_air(g: Glass, d: Dict[str, Any], box: Box) -> None:
    x0, y0, x1, y1 = _frame(g, box, "air", d.get("demo", False))
    g.text((x0 - 2, y0), f"{d['aqi']:.0f}", mono(48, 700), BLACK)
    nw = g.text_size(f"{d['aqi']:.0f}", mono(48, 700))[0]
    g.text((x0 + nw + 8, y0 + 8), "US AQI", sans(11, 600), DARK)
    g.text((x0 + nw + 8, y0 + 24), g.fit_text(d["aqi_label"], x1 - x0 - nw - 8, sans(15, 700)), sans(15, 700), BLACK)
    g.text((x0, y0 + 62), f"UV {d['uv']:.0f} {d['uv_label']} · peak {d['uv_peak']:.0f} at {d['uv_peak_at']}", sans(13, 500), DARK)
    pm = next((p for p in d["pollutants"] if p["name"] == "PM2.5"), None)
    if pm and y1 - y0 > 100:
        g.text((x0, y0 + 84), f"PM2.5 {pm['display']} µg/m³ · {pm['ratio'] * 100:.0f} % of WHO", sans(13, 500), DARK)


@tile("moon", ad_moon.fetch)
def _t_moon(g: Glass, d: Dict[str, Any], box: Box) -> None:
    x0, y0, x1, y1 = _frame(g, box, "moon", False)
    r = min(44, (y1 - y0) // 2 - 12)
    draw_moon(g, x0 + r + 4, y0 + r + 6, r, d["fraction"], maria=False)
    tx = x0 + 2 * r + 20
    nf = sans(18, 700) if g.text_size(d["name"], sans(18, 700))[0] <= x1 - tx else sans(15, 700)
    g.text((tx, y0 + 6), g.fit_text(d["name"], x1 - tx, nf), nf, BLACK)
    g.text((tx, y0 + 32), f"{d['illumination'] * 100:.0f} % lit · day {d['age_days']:.0f}", sans(13, 500), DARK)
    up = d.get("upcoming") or []
    if up:
        g.text((tx, y0 + 52), g.fit_text(f"{up[0]['name']} {up[0]['in']}" if isinstance(up[0], dict) and "in" in up[0] else str(up[0].get("label", up[0])), x1 - tx, sans(13, 500)), sans(13, 500), DARK)


@tile("habits", ad_hb.fetch)
def _t_habits(g: Glass, d: Dict[str, Any], box: Box) -> None:
    x0, y0, x1, y1 = _frame(g, box, "habits", d.get("source") == "demo")
    g.text((x0, y0), f"{d['done_today']} of {len(d['habits'])}", mono(30, 700), BLACK)
    g.text((x0 + g.text_size(f"{d['done_today']} of {len(d['habits'])}", mono(30, 700))[0] + 10, y0 + 12), "today", sans(13, 500), DARK)
    y = y0 + 44
    for h in d["habits"]:
        if y + 18 > y1:
            break
        g.circle(x0 + 6, y + 7, 5, fill=BLACK if h["today"] else None, outline=DARK, width=1)
        g.text((x0 + 20, y), g.fit_text(h["name"], x1 - x0 - 60, sans(13, 600 if not h["today"] else 500)), sans(13, 500), BLACK if not h["today"] else DARK)
        g.text((x1, y), f"{h['streak']} d", mono(12, 500), DARK, anchor="ra")
        y += 20


@tile("countdown", ad_cd.fetch)
def _t_countdown(g: Glass, d: Dict[str, Any], box: Box) -> None:
    x0, y0, x1, y1 = _frame(g, box, "countdown", d.get("source") == "demo")
    h = d["hero"]
    if not h:
        g.text((x0, y0 + 6), "Nothing ahead.", sans(16, 500), DARK)
        return
    g.text((x0 - 2, y0 - 6), str(h["days"]), mono(56, 700), BLACK)
    nw = g.text_size(str(h["days"]), mono(56, 700))[0]
    g.text((x0 + nw + 8, y0 + 8), "days" if h["days"] != 1 else "day", sans(15, 700), BLACK)
    g.text((x0 + nw + 8, y0 + 28), h["kind"], sans(12, 500), DARK)
    g.text((x0, y0 + 64), g.fit_text(h["label"], x1 - x0, sans(16, 700)), sans(16, 700), BLACK)
    g.text((x0, y0 + 86), h["date_label"], sans(13, 500), DARK)
    y = y0 + 110
    for u in d["upcoming"][:2]:
        if y + 18 > y1:
            break
        g.text((x0, y), g.fit_text(u["label"], x1 - x0 - 44, sans(13, 500)), sans(13, 500), BLACK)
        g.text((x1, y), f"{u['days']} d", mono(12, 500), DARK, anchor="ra")
        y += 20


def _f_inbox(p):
    return ad_notif.fetch({"demo": 1} if p.get("demo") else {"live": 1})


@tile("inbox", _f_inbox)
def _t_inbox(g: Glass, d: Dict[str, Any], box: Box) -> None:
    x0, y0, x1, y1 = _frame(g, box, "inbox", d.get("source") == "demo")
    g.text((x0 - 2, y0 - 6), str(d["unread"]), mono(56, 700), BLACK)
    nw = g.text_size(str(d["unread"]), mono(56, 700))[0]
    g.text((x0 + nw + 8, y0 + 8), "new", sans(15, 700), BLACK)
    counts = d.get("counts") or {}
    if counts:
        g.text((x0 + nw + 8, y0 + 28), g.fit_text(" · ".join(f"{k} {v}" for k, v in list(counts.items())[:3]), x1 - x0 - nw - 8, sans(12, 500)), sans(12, 500), DARK)
    y = y0 + 66
    for it in (d.get("items") or [])[:4]:
        if y + 18 > y1:
            break
        g.text((x0, y), g.fit_text(f"{it.get('from', '')}  {it.get('title') or it.get('text', '')}", x1 - x0, sans(13, 500)), sans(13, 500), BLACK)
        y += 20


@tile("word", lambda p: ad_po.fetch({**p, "mode": "word"}))
def _t_word(g: Glass, d: Dict[str, Any], box: Box) -> None:
    x0, y0, x1, y1 = _frame(g, box, "word of the day", False)
    px = 34
    while g.text_size(d["text"], sans(px, 700))[0] > x1 - x0 and px > 18:
        px -= 2
    g.text((x0 - 1, y0), d["text"], sans(px, 700), BLACK)
    g.text((x0, y0 + int(px * 1.25)), f"{d['pron']} · {d['pos']}", sans(12, 500), DARK)
    y = y0 + int(px * 1.25) + 22
    for ln in g.wrap(d["sub"], x1 - x0, sans(14, 500), max_lines=max(1, (y1 - y) // 19)):
        g.text((x0, y), ln, sans(14, 500), BLACK)
        y += 19


@tile("goals", ad_go.fetch)
def _t_goals(g: Glass, d: Dict[str, Any], box: Box) -> None:
    x0, y0, x1, y1 = _frame(g, box, f"goals · {d['done']} of {len(d['goals'])}", d.get("source") == "demo")
    goals = d["goals"][:4]
    if not goals:
        return
    n = len(goals)
    cols = 2 if n > 1 else 1
    rows = (n + cols - 1) // cols
    cw, ch = (x1 - x0) // cols, (y1 - y0) // rows
    r = max(18, min(cw // 2 - 8, (ch - 46) // 2))
    for i, goal in enumerate(goals):
        cx = x0 + (i % cols) * cw + cw // 2
        cy = y0 + (i // cols) * ch + r + 2
        if r >= 44:
            _ring(g, goal, cx, cy, r, d["day_fraction"], big=False)
        else:  # small ring: percent inside, the value goes under the name
            g.ring(cx, cy, r, min(1.0, goal["fraction"]), width=max(6, r // 4), fill=BLACK, track=LIGHT)
            g.text((cx, cy), f"{goal['pct']}%", mono(max(10, r // 2), 700), BLACK, anchor="mm")
        g.text((cx, cy + r + 6), g.fit_text(goal["name"], cw - 6, sans(12, 600)), sans(12, 600), BLACK, anchor="ma")
        if r < 44:
            g.text((cx, cy + r + 22), g.fit_text(f"{goal['display']} of {goal['target_display']}", cw - 6, sans(11, 500)), sans(11, 500), DARK, anchor="ma")


@tile("focus", ad_fo.fetch)
def _t_focus(g: Glass, d: Dict[str, Any], box: Box) -> None:
    x0, y0, x1, y1 = _frame(g, box, "focus", d.get("source") == "demo")
    g.text((x0, y0), _dur(d["done_min"]), mono(28, 700), BLACK)
    tw = g.text_size(_dur(d["done_min"]), mono(28, 700))[0]
    g.text((x0 + tw + 8, y0 + 10), f"of {_dur(d['planned_min'])} · {d['sessions_done']}/{d['sessions']} sessions", sans(12, 500), DARK)
    # one-line day strip: events LIGHT above the axis, blocks below
    ay = y0 + 62
    ds, de = d["day_start"], d["day_end"]
    span = max(1, de - ds)
    X = lambda m: x0 + int((min(max(m, ds), de) - ds) / span * (x1 - x0))  # noqa: E731
    g.hairline(x0, ay, x1, DARK)
    for e in d["events"]:
        g.rect((X(e["s"]), ay - 12, max(X(e["e"]), X(e["s"]) + 2), ay - 2), fill=LIGHT)
    for b in d["blocks"]:
        xa, xb = X(b["s"]), max(X(b["e"]), X(b["s"]) + 2)
        if b["state"] == "done":
            g.rect((xa, ay + 2, xb, ay + 12), fill=BLACK)
        elif b["state"] == "now":
            g.rect((xa, ay + 2, xb, ay + 12), fill=WHITE, outline=BLACK, width=1)
            g.rect((xa, ay + 2, X(d["now_m"]), ay + 12), fill=DARK)
        else:
            g.rect((xa, ay + 2, xb, ay + 12), fill=WHITE, outline=BLACK if b["state"] == "planned" else DARK, width=1)
    if ds <= d["now_m"] <= de:
        g.vline(X(d["now_m"]), ay - 16, ay + 16, BLACK)
    for h in range(ds // 60, de // 60 + 1, 3):
        g.text((X(h * 60), ay + 16), f"{h:02d}", mono(11, 500), DARK, anchor="ma")
    y = ay + 34
    cur = d["current"] or d["next"]
    if cur and y + 20 < y1:
        lab = "now" if d["current"] else "next"
        tail = f"{cur['left']} min left" if d["current"] else f"{cur['start']} · in {cur['s'] - d['now_m']} min"
        g.text((x0, y), g.fit_text(f"{lab}  {cur['label']}", x1 - x0 - 100, sans(13, 600)), sans(13, 600), BLACK)
        g.text((x1, y), tail, sans(12, 500), DARK, anchor="ra")
        y += 20
    if d["free"] and y + 18 < y1:
        w = d["free"][0]
        g.text((x0, y), f"free  {w['start']}–{w['end']}", sans(13, 500), DARK)
        g.text((x1, y), _dur(w["min"]), sans(12, 600), BLACK, anchor="ra")


@tile("todo", ad_td.fetch)
def _t_todo(g: Glass, d: Dict[str, Any], box: Box) -> None:
    x0, y0, x1, y1 = _frame(g, box, f"to do · {d['n_open']} open", d.get("source") == "demo")
    if not d["open"]:
        g.text((x0, y0), "Nothing open.", sans(16, 700), BLACK)
        return
    top = d["top"]
    f = sans(17, 700)
    lines = g.wrap(top["text"], x1 - x0 - 24, f, max_lines=2)
    g.rect((x0, y0 + 4, x0 + 14, y0 + 18), fill=WHITE, outline=BLACK, width=1)
    y = y0
    for ln in lines:
        g.text((x0 + 24, y), ln, f, BLACK)
        y += 22
    y += 4
    for it in [x for x in d["open"] if x is not top]:
        if y + 18 > y1:
            break
        g.rect((x0 + 1, y + 3, x0 + 11, y + 13), fill=WHITE, outline=BLACK, width=1)
        tag_w = g.text_size(it["tag"], sans(11, 500))[0] + 8 if it["tag"] else 0
        g.text((x0 + 20, y), g.fit_text(it["text"], x1 - x0 - 20 - tag_w, sans(13, 500)), sans(13, 500), BLACK)
        if it["tag"]:
            g.text((x1, y + 2), it["tag"], sans(11, 500), DARK, anchor="ra")
        y += 19


@tile("arm", ad_arm.fetch)
def _t_arm(g: Glass, d: Dict[str, Any], box: Box) -> None:
    x0, y0, x1, y1 = _frame(g, box, f"{d['role']} arm", d.get("source") == "demo")
    g.text((x0, y0), d["pose"], sans(20, 700), BLACK)
    g.text((x1, y0 + 4), f"{d['volts']:.1f} V", mono(14, 700), BLACK, anchor="ra")
    g.text((x0, y0 + 26), ("can lift" if d["can_lift"] else "cannot lift") + " · torque " + ("on" if d["torque_any"] else "off"), sans(12, 500), DARK)
    draw_arm(g, d, x0, y0 + 46, x1 - x0, max(60, y1 - y0 - 50), caption=False)


# ── the component ────────────────────────────────────────────────────────────
@tile("tide", ad_ti.fetch)
def _t_tide(g: Glass, d: Dict[str, Any], box: Box) -> None:
    """The water curve small: sea filled, now-dot, next high/low as two facts."""
    x0, y0, x1, y1 = _frame(g, box, f"tide · {d['station']['name']}", d["demo"])
    pts = d["points"]
    if len(pts) < 2:
        return
    fw = 118 if x1 - x0 > 300 else 0          # facts column only when the tile is wide
    cw = x1 - x0 - fw
    lo, hi = d["vmin"] - 0.1, d["vmax"] + 0.1
    top, floor = y0 + 6, y1 - 14
    X = lambda t: round(x0 + t / 24 * cw)
    Y = lambda v: round(floor - (v - lo) / (hi - lo) * (floor - top))
    poly = [(X(p["t"]), Y(p["v"])) for p in pts]
    g.d.polygon([(poly[0][0], floor)] + poly + [(poly[-1][0], floor)], fill=LIGHT)
    g.d.line(poly, fill=BLACK, width=1)
    g.hairline(x0, floor, x0 + cw, DARK)
    nx, ny = X(d["now"]["t"]), Y(d["now"]["v"])
    g.d.line([(nx, top), (nx, floor)], fill=BLACK, width=1)
    g.circle(nx, ny, 3, fill=BLACK)
    sh = d["start_hour"]
    t = (6 - (sh % 6)) % 6
    while t <= 24:
        g.text((X(t), floor + 2), f"{int((sh + t) % 24):02d}", mono(11, 500), DARK, anchor="ma")
        t += 6
    unit = d["unit"]
    if fw:
        fx = x1 - fw + 14
        arrow = "↑" if d["trend"] == "rising" else "↓"
        g.text((fx, y0), f"{d['now']['v']:.2f} {unit}", mono(16, 700), BLACK)
        g.text((fx, y0 + 20), f"{arrow} {d['trend']}", sans(11, 500), DARK)
        yy = y0 + 40
        for key, name in (("next_high", "high"), ("next_low", "low")):
            n = d.get(key)
            if n and yy + 30 < y1:
                g.text((fx, yy), n["time_label"], mono(14, 700), BLACK)
                g.text((fx, yy + 17), f"{name} · {n['v']:.2f} {unit}", sans(11, 500), DARK)
                yy += 36
    else:
        g.text((x1, y0), f"{d['now']['v']:.2f} {unit}", mono(14, 700), BLACK, anchor="ra")


@tile("playing", ad_pl.fetch)
def _t_playing(g: Glass, d: Dict[str, Any], box: Box) -> None:
    """Track, artist, a needle line — no artwork at tile size."""
    x0, y0, x1, y1 = _frame(g, box, "playing" if d["state"] == "playing" else d["state"], d["demo"])
    tf = sans(18, 700) if g.text_size(d["track"], sans(18, 700))[0] <= x1 - x0 else sans(15, 700)
    g.text((x0, y0 + 2), g.fit_text(d["track"], x1 - x0, tf), tf, BLACK)
    g.text((x0, y0 + 28), g.fit_text(d["artist"], x1 - x0, sans(13, 500)), sans(13, 500), DARK)
    if d["album"] and d["album"] != d["track"] and y1 - y0 > 110:
        g.text((x0, y0 + 48), g.fit_text(d["album"], x1 - x0, sans(12, 500)), sans(12, 500), DARK)
    by = y1 - 10
    g.rect((x0, by - 1, x1, by + 1), fill=LIGHT)
    px = x0 + round((x1 - x0) * max(0.0, min(1.0, d["progress"])))
    g.rect((x0, by - 1, px, by + 1), fill=DARK)
    g.rect((px - 1, by - 5, px + 1, by + 5), fill=BLACK)
    pos, dur = d["position_s"], d["duration_s"]
    g.text((x0, by - 22), f"{pos // 60}:{pos % 60:02d}", mono(11, 500), DARK)
    g.text((x1, by - 22), f"−{max(0, dur - pos) // 60}:{max(0, dur - pos) % 60:02d}", mono(11, 500), DARK, anchor="ra")


@tile("markets", ad_mk.fetch)
def _t_markets(g: Glass, d: Dict[str, Any], box: Box) -> None:
    """The ticker rows without sparklines: symbol, last, change — up/down count in the label."""
    wide = box[2] - box[0] > 280
    x0, y0, x1, y1 = _frame(g, box, f"markets · {d['up']} up · {d['down']} down" if wide else "markets", d["demo"])
    yy = y0 + 2
    for r in d["rows"]:
        if yy + 16 > y1:
            break
        g.text((x0, yy), g.fit_text(r.get("label") or r["symbol"], (x1 - x0) // 2 - 6, sans(12, 600)), sans(12, 600), BLACK)
        last = r["last"]
        ls = f"{last:,.0f}" if last >= 1000 else f"{last:,.2f}" if last >= 10 else f"{last:.4f}"
        g.text((x1 - 58, yy), ls, mono(12, 500), BLACK, anchor="ra")
        chg = f"{'+' if r['change_pct'] >= 0 else '−'}{abs(r['change_pct']):.1f}%"
        g.text((x1, yy), chg, mono(12, 700), BLACK if r["change_pct"] >= 0 else DARK, anchor="ra")
        yy += 18


def _layout(params: Dict[str, Any], orientation: str = "landscape") -> List[List[str]]:
    if orientation == "portrait" and not params.get("tiles"):
        name = str(params.get("preset") or "morning")
        if name in PRESETS_PORTRAIT:
            return PRESETS_PORTRAIT[name]
    if params.get("tiles"):
        names = [t.strip() for t in str(params["tiles"]).split(",") if t.strip() in TILES]
        rows: List[List[str]] = []
        per = 3
        for i in range(0, len(names), per):
            rows.append(names[i:i + per])
        return rows or PRESETS["morning"]
    return PRESETS.get(str(params.get("preset") or "morning"), PRESETS["morning"])


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    rows = _layout(params)
    rows_p = _layout(params, "portrait")
    data: Dict[str, Any] = {}
    for name in {n for r in rows + rows_p for n in r}:
        f, _ = TILES[name]
        try:
            data[name] = f(params)
        except Exception:
            try:
                data[name] = {**f({**params, "demo": 1}), "demo": True}
            except Exception:
                data[name] = None
    import datetime as dt
    return {"rows": rows, "rows_portrait": rows_p, "data": data, "preset": str(params.get("preset") or ("custom" if params.get("tiles") else "morning")),
            "now": dt.datetime.now().strftime("%H:%M"), "date_label": dt.date.today().strftime("%A, %-d %B")}


@component(
    "compose", "Compose",
    "Several tiles on one sheet — morning / desk / evening / work / lock / weekend presets or tiles=clock,weather,next,…; "
    "each tile fetches on its own and falls back to its fixture (marked 'demo') if its source is down.",
    params={"preset": "morning|desk|evening|work|lock|weekend (lock/morning/work/weekend have a stacked portrait layout)", "tiles": "comma list: " + ",".join(sorted(TILES)), "demo": "1 → all fixtures"},
    fetch=fetch,
    native_hint="type:'composite' already exists in firmware — this maps tile boxes onto its regions; each tile's native form ≤ 120 B",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    g = Glass(LANDSCAPE if orientation == "landscape" else PORTRAIT)
    g.label((MARGIN, MARGIN), d["preset"], 12, DARK)
    g.text((g.w - MARGIN, MARGIN - 2), f"{d['date_label']}  ·  {d['now']}", sans(13, 600), BLACK, anchor="ra")
    top = MARGIN + 24
    g.hairline(MARGIN, top - 6, g.w - MARGIN, DARK)
    rows = d["rows"]
    if orientation != "landscape" and d.get("rows_portrait") and d["rows_portrait"] != rows:
        rows = d["rows_portrait"]                  # a declared stacked layout
    elif orientation != "landscape":
        # portrait without one: two columns, wide tiles span both
        flat = [n for r in rows for n in r]
        seen: List[str] = []
        for n in flat:
            if n not in seen:
                seen.append(n)
        rows = [seen[i:i + 2] for i in range(0, len(seen), 2)]
    gap = 18
    rh = (g.h - top - MARGIN - gap * (len(rows) - 1)) // max(1, len(rows))
    y = top
    for ri, row in enumerate(rows):
        # merge repeated names into spans
        spans: List[Tuple[str, int]] = []
        for n in row:
            if spans and spans[-1][0] == n:
                spans[-1] = (n, spans[-1][1] + 1)
            else:
                spans.append((n, 1))
        cols = sum(s for _, s in spans)
        cw = (g.w - 2 * MARGIN - gap * (cols - 1)) // cols
        x = MARGIN
        for ci, (name, span) in enumerate(spans):
            w = cw * span + gap * (span - 1)
            box = (x, y, x + w, y + rh)
            data = d["data"].get(name)
            if data is None:
                g.label((x, y), name, 11, DARK)
                g.text((x, y + 24), "unavailable", sans(14, 500), DARK)
            else:
                TILES[name][1](g, data, box)
            if ci < len(spans) - 1:
                g.vline(x + w + gap // 2, y + 2, y + rh - 2, LIGHT)
            x += w + gap
        if ri < len(rows) - 1:
            g.hairline(MARGIN, y + rh + gap // 2, g.w - MARGIN, LIGHT)
        y += rh + gap
    return g.snap()
