"""
⏱ focus — the day as two tracks.

One horizontal axis from day start to day end. Above it, calendar events as
LIGHT bars with titles (the day you were given); below it, focus blocks as
BLACK bars — done solid, planned outlined, missed hatched, the current one
with its minutes left printed inside (the day you are taking). A now-line
crosses both tracks. The right/lower column is the reckoning: focus done of
planned, sessions, meeting load, and the biggest FREE WINDOWS still ahead —
the one number that tells you where the next deep-work block can go.
Data: glass.adapters.focus (blocks) + calendar.
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import focus as fd
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _dur(m: int) -> str:
    h, mm = divmod(m, 60)
    return f"{h} h {mm:02d}" if h and mm else (f"{h} h" if h else f"{mm} min")


def _tracks(g: Glass, d: Dict[str, Any], x0: int, x1: int, y_axis: int, ev_h: int, fb_h: int) -> None:
    ds, de = d["day_start"], d["day_end"]
    span = max(1, de - ds)

    def X(m: int) -> int:
        return x0 + int((min(max(m, ds), de) - ds) / span * (x1 - x0))

    # hour ticks + labels
    g.hairline(x0, y_axis, x1, DARK)
    for h in range(ds // 60 + (1 if ds % 60 else 0), de // 60 + 1):
        x = X(h * 60)
        g.vline(x, y_axis - 3, y_axis + 3, DARK)
        if (h - ds // 60) % 2 == 0 or (x1 - x0) > 600:
            g.text((x, y_axis + 6), f"{h:02d}", mono(11, 500), DARK, anchor="ma")
    # events above
    ey1 = y_axis - 8
    for e in d["events"]:
        xa, xb = X(e["s"]), X(e["e"])
        if xb - xa < 3:
            xb = xa + 3
        g.rect((xa, ey1 - ev_h, xb, ey1), fill=LIGHT)
        if xb - xa > 30:
            g.text((xa + 4, ey1 - ev_h + 3), g.fit_text(e["title"], xb - xa - 8, sans(11, 600)), sans(11, 600), BLACK)
    g.label((x0, ey1 - ev_h - 18), "calendar", 11, DARK)
    # focus below
    fy0 = y_axis + 22
    for b in d["blocks"]:
        xa, xb = X(b["s"]), X(b["e"])
        if b["state"] == "done":
            g.rect((xa, fy0, xb, fy0 + fb_h), fill=BLACK)
            if xb - xa > 34:
                g.text((xa + 4, fy0 + 3), g.fit_text(b["label"], xb - xa - 8, sans(11, 600)), sans(11, 600), WHITE)
        elif b["state"] == "now":
            g.rect((xa, fy0, xb, fy0 + fb_h), fill=WHITE, outline=BLACK, width=2)
            done_x = X(d["now_m"])
            g.rect((xa, fy0, done_x, fy0 + fb_h), fill=DARK)
            g.text((xb - 4, fy0 + 3), f"{d['current']['left']}′", mono(12, 700), BLACK, anchor="ra")
        elif b["state"] == "missed":
            g.rect((xa, fy0, xb, fy0 + fb_h), fill=WHITE, outline=DARK, width=1)
            for xx in range(xa + 4, xb, 6):
                g.d.line([(xx, fy0 + fb_h - 2), (min(xb - 1, xx + fb_h - 4), fy0 + 2)], fill=LIGHT, width=1)
        else:
            g.rect((xa, fy0, xb, fy0 + fb_h), fill=WHITE, outline=BLACK, width=1)
            if xb - xa > 34:
                g.text((xa + 4, fy0 + 3), g.fit_text(b["label"], xb - xa - 8, sans(11, 500)), sans(11, 500), BLACK)
    g.label((x0, fy0 + fb_h + 6), "focus", 11, DARK)
    # free windows as hairline brackets under the focus track
    by = fy0 + fb_h + 26
    for w in d["free"]:
        xa, xb = X(w["s"]), X(w["e"])
        g.hairline(xa, by, xb, DARK)
        g.vline(xa, by - 3, by + 3, DARK)
        g.vline(xb, by - 3, by + 3, DARK)
    # now-line across both tracks
    if ds <= d["now_m"] <= de:
        xn = X(d["now_m"])
        g.vline(xn, ey1 - ev_h - 4, fy0 + fb_h + 4, BLACK)
        g.circle(xn, y_axis, 4, fill=BLACK)


def _reckoning(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, compact: bool = False) -> int:
    g.text((x, y), _dur(d["done_min"]), mono(34, 700), BLACK)
    tw = g.text_size(_dur(d["done_min"]), mono(34, 700))[0]
    g.text((x + tw + 10, y + 14), f"of {_dur(d['planned_min'])} focus", sans(14, 500), DARK)
    y += 46
    g.text((x, y), f"{d['sessions_done']}/{d['sessions']} sessions" + (f"  ·  {d['missed']} missed" if d["missed"] else "")
           + f"  ·  {_dur(d['meeting_min'])} in meetings", sans(13, 500), DARK)
    y += 26
    if d["current"]:
        g.label((x, y), "now", 11, DARK)
        g.text((x, y + 16), g.fit_text(d["current"]["label"], w, sans(18, 700)), sans(18, 700), BLACK)
        g.text((x, y + 40), f"{d['current']['left']} min left · until {d['current']['end']}", sans(13, 500), DARK)
        y += 66
    elif d["next"]:
        g.label((x, y), "next", 11, DARK)
        g.text((x, y + 16), g.fit_text(d["next"]["label"], w, sans(18, 700)), sans(18, 700), BLACK)
        g.text((x, y + 40), f"{d['next']['start']}–{d['next']['end']}  ·  in {d['next']['s'] - d['now_m']} min", sans(13, 500), DARK)
        y += 66
    g.label((x, y), "free ahead", 11, DARK)
    y += 18
    if not d["free"]:
        g.text((x, y), "No gap ≥ 30 min left today.", sans(14, 500), DARK)
        return y + 20
    for i, fw in enumerate(d["free"][: (2 if compact else 3)]):
        g.text((x, y), f"{fw['start']}–{fw['end']}", mono(15, 700 if i == 0 else 500), BLACK if i == 0 else DARK)
        g.text((x + w, y), _dur(fw["min"]), sans(13, 600 if i == 0 else 500), BLACK if i == 0 else DARK, anchor="ra")
        y += 22
    return y


@component(
    "focus", "Focus",
    "The day as two tracks on one axis: calendar events above, focus blocks below (done/now/planned/missed), "
    "a now-line, focus done of planned, sessions, meeting load, and the biggest free windows still ahead.",
    params={"blocks": "JSON [{start,end,label,done}] (or ~/.tiny/sticky-focus.json)", "ics_url": "calendar", "clock": "HH:MM override", "demo": "1 → fixtures"},
    fetch=fd.fetch,
    native_hint="type:'focus' {ds,de,now, ev:[{s,e,title}], fb:[{s,e,state:u8,label}], free:[{s,e}]} ≈ 400 B; refresh each minute for the now-line (partial)",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        g.label((MARGIN, MARGIN), "focus", 13, DARK)
        g.text((g.w - MARGIN, MARGIN - 2), f"{d['date_label']}  ·  {d['now']}", sans(13, 600), DARK, anchor="ra")
        if d["calendar_demo"] or d["source"] == "demo":
            g.text((MARGIN + 66, MARGIN - 2), "demo", sans(11, 500), DARK)
        _tracks(g, d, MARGIN, g.w - MARGIN, MARGIN + 104, 38, 32)
        g.hairline(MARGIN, MARGIN + 206, g.w - MARGIN, LIGHT)
        cw = (g.w - 2 * MARGIN - 30) // 2
        y = MARGIN + 222
        _reckoning(g, d, MARGIN, y, cw)
        # right column: block list
        x = MARGIN + cw + 30
        g.vline(x - 15, y, g.h - MARGIN, LIGHT)
        g.label((x, y), "blocks", 11, DARK)
        yy = y + 18
        for b in d["blocks"]:
            if yy + 20 > g.h - MARGIN:
                break
            # state glyph drawn, not typed (the fonts have no geometric shapes)
            if b["state"] == "done":
                g.rect((x, yy + 3, x + 9, yy + 12), fill=BLACK)
            elif b["state"] == "now":
                g.rect((x, yy + 3, x + 9, yy + 12), fill=WHITE, outline=BLACK, width=1)
                g.rect((x, yy + 3, x + 5, yy + 12), fill=BLACK)
            else:
                g.rect((x, yy + 3, x + 9, yy + 12), fill=WHITE, outline=BLACK if b["state"] == "planned" else DARK, width=1)
            g.text((x + 18, yy), f"{b['start']}–{b['end']}", mono(13, 500), BLACK if b["state"] != "missed" else DARK)
            g.text((x + 120, yy), g.fit_text(b["label"], cw - 120, sans(13, 600 if b["state"] == "now" else 500)),
                   sans(13, 600 if b["state"] == "now" else 500), BLACK if b["state"] != "missed" else DARK)
            yy += 21
    else:
        g = Glass(PORTRAIT)
        g.label((MARGIN, MARGIN), "focus", 13, DARK)
        g.text((g.w - MARGIN, MARGIN - 2), d["now"], mono(15, 500), DARK, anchor="ra")
        y = _reckoning(g, d, MARGIN, MARGIN + 30, g.w - 2 * MARGIN, compact=True)
        g.hairline(MARGIN, y + 14, g.w - MARGIN, LIGHT)
        _tracks(g, d, MARGIN, g.w - MARGIN, y + 100, 30, 26)
        yy = y + 210
        g.hairline(MARGIN, yy, g.w - MARGIN, LIGHT)
        g.label((MARGIN, yy + 12), "blocks", 11, DARK)
        yy += 30
        for b in d["blocks"]:
            if yy + 22 > g.h - MARGIN:
                break
            g.text((MARGIN, yy), f"{b['start']}–{b['end']}", mono(13, 500), BLACK if b["state"] != "missed" else DARK)
            g.text((MARGIN + 104, yy), g.fit_text(b["label"], g.w - 2 * MARGIN - 160, sans(13, 500)), sans(13, 500), BLACK if b["state"] != "missed" else DARK)
            g.text((g.w - MARGIN, yy), b["state"], sans(11, 500), DARK, anchor="ra")
            yy += 22
    return g.snap()
