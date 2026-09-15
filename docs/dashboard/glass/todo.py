"""
☐ todo — the list, with one thing on top.

The first open (or `top`) item is set large — that is the card's argument: not
ten things, one. Below it the rest in due buckets (today · tomorrow · this week ·
later) as checkbox rows with a small tag, then the done items struck through in
DARK so the day shows its work. Counter top-right: "3 today · 7 open · 3 done".
Landscape is two columns (open | done + later), portrait one long column.
Data: glass.adapters.todo (params.items / ~/.tiny/sticky-todo.json / fixture).
"""
from __future__ import annotations

from typing import Any, Dict, List

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import todo as td
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans

BUCKET_LABEL = {"today": "today", "tomorrow": "tomorrow", "week": "this week", "later": "later"}


def _box(g: Glass, x: int, y: int, done: bool, size: int = 12) -> None:
    g.rect((x, y, x + size, y + size), fill=WHITE, outline=DARK if done else BLACK, width=1)
    if done:  # a tick, two strokes
        g.d.line([(x + 2, y + size // 2), (x + size // 2 - 1, y + size - 3), (x + size - 2, y + 2)], fill=DARK, width=2)


def _row(g: Glass, it: Dict[str, Any], x: int, y: int, w: int, px: int = 17) -> int:
    f = sans(px, 500)
    _box(g, x, y + 3, it["done"], px - 3)
    tag_w = g.text_size(it["tag"], sans(11, 500))[0] + 8 if it["tag"] else 0
    t = g.fit_text(it["text"], w - (px + 6) - tag_w, f)
    col = DARK if it["done"] else BLACK
    g.text((x + px + 6, y), t, f, col)
    if it["done"]:
        tw = g.text_size(t, f)[0]
        g.hairline(x + px + 6, y + px // 2 + 1, x + px + 6 + tw, DARK)
    if it["tag"]:
        g.text((x + w, y + 2), it["tag"], sans(11, 500), DARK, anchor="ra")
    return y + px + 11


def _top(g: Glass, it: Dict[str, Any], x: int, y: int, w: int) -> int:
    g.label((x, y), "first", 11, DARK)
    y += 18
    f = sans(26, 700)
    lines = g.wrap(it["text"], w - 34, f, max_lines=3)
    _box(g, x, y + 7, False, 20)
    for ln in lines:
        g.text((x + 34, y), ln, f, BLACK)
        y += 32
    meta = " · ".join(s for s in (BUCKET_LABEL.get(it["due"], ""), it["tag"]) if s)
    if meta:
        g.text((x + 34, y), meta, sans(13, 500), DARK)
        y += 20
    return y + 8


def _buckets(g: Glass, buckets: List[Dict[str, Any]], skip: Dict[str, Any], x: int, y: int, w: int, y_max: int) -> int:
    for b in buckets:
        items = [it for it in b["items"] if it is not skip]
        if not items:
            continue
        if y + 40 > y_max:
            break
        g.label((x, y), BUCKET_LABEL[b["name"]], 11, DARK)
        y += 20
        for it in items:
            if y + 22 > y_max:
                g.text((x + 20, y), f"+{len(items) - items.index(it)} more", sans(11, 500), DARK)
                return y_max
            y = _row(g, it, x, y, w)
        y += 10
    return y


def _done(g: Glass, done: List[Dict[str, Any]], x: int, y: int, w: int, y_max: int) -> int:
    if not done:
        return y
    g.label((x, y), f"done · {len(done)}", 11, DARK)
    y += 20
    for it in done:
        if y + 24 > y_max:
            break
        y = _row(g, it, x, y, w, 15)
    return y


@component(
    "todo", "To do",
    "One thing set large, then the open list in due buckets (today · tomorrow · this week · later) with tags, "
    "and the done items struck through. Counter: n today · n open · n done.",
    params={"items": "JSON [{text,done,due:today|tomorrow|week|later,tag,top}] (or ~/.tiny/sticky-todo.json)", "title": "header", "demo": "1 → fixture"},
    fetch=td.fetch,
    native_hint="type:'todo' {title, top, groups:[{name, items:[{t,done,tag}]}]} ≈ 600 B; text-only — the firmware list card is one step away",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    counter = f"{d['n_today']} today  ·  {d['n_open']} open  ·  {d['n_done']} done"
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        g.text((MARGIN, MARGIN - 6), d["title"], sans(26, 700), BLACK)
        g.text((g.w - MARGIN, MARGIN + 2), counter, sans(13, 500), DARK, anchor="ra")
        if d["source"] == "demo":
            g.text((MARGIN + g.text_size(d["title"], sans(26, 700))[0] + 10, MARGIN + 4), "demo", sans(11, 500), DARK)
        g.hairline(MARGIN, MARGIN + 34, g.w - MARGIN, BLACK)
        cw = (g.w - 2 * MARGIN - 40) // 2
        x2 = MARGIN + cw + 40
        y = MARGIN + 48
        if not d["open"]:
            g.text((MARGIN, y + 10), "Nothing open.", sans(24, 700), BLACK)
            g.text((MARGIN, y + 44), "The list is clear — add items to ~/.tiny/sticky-todo.json.", sans(13, 500), DARK)
        else:
            y = _top(g, d["top"], MARGIN, y, cw)
            # left column: today + tomorrow; right: week + later, then done
            left = [b for b in d["buckets"] if b["name"] in ("today", "tomorrow")]
            right = [b for b in d["buckets"] if b["name"] in ("week", "later")]
            _buckets(g, left, d["top"], MARGIN, y + 4, cw, g.h - MARGIN)
            g.vline(x2 - 20, MARGIN + 48, g.h - MARGIN, LIGHT)
            yr = _buckets(g, right, d["top"], x2, MARGIN + 48, cw, g.h - MARGIN - 100)
            _done(g, d["done"], x2, max(yr + 8, MARGIN + 48), cw, g.h - MARGIN)
    else:
        g = Glass(PORTRAIT)
        g.text((MARGIN, MARGIN - 6), d["title"], sans(26, 700), BLACK)
        g.text((g.w - MARGIN, MARGIN + 2), f"{d['n_open']} open · {d['n_done']} done", sans(12, 500), DARK, anchor="ra")
        g.hairline(MARGIN, MARGIN + 34, g.w - MARGIN, BLACK)
        y = MARGIN + 48
        w = g.w - 2 * MARGIN
        if not d["open"]:
            g.text((MARGIN, y + 10), "Nothing open.", sans(24, 700), BLACK)
        else:
            y = _top(g, d["top"], MARGIN, y, w)
            y = _buckets(g, d["buckets"], d["top"], MARGIN, y + 4, w, g.h - MARGIN - 80)
            g.hairline(MARGIN, y + 2, g.w - MARGIN, LIGHT)
            _done(g, d["done"], MARGIN, y + 14, w, g.h - MARGIN)
    return g.snap()
