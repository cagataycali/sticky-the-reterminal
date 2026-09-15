"""
♞ chess — the lichess daily puzzle, as a book prints it.

A board of two grays (white squares white, dark squares light) with the pieces
as type: white pieces hollow, black pieces solid — the one distinction that
survives four grays. The board faces the side to move. The opponent's last move
is framed (from square hairline, to square heavy) so you see what just
happened; beside it the task in one line ("Black to move · Mate in 2"), the
puzzle's rating and how many people have tried it, who played the game, and
the themes as words. The solution is printed upside down in small mono at the
bottom, the way puzzle books do it — readable only when you decide to turn the
device. Data: glass.adapters.chess (lichess public API, fixture fallback).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image, ImageFont

from . import LANDSCAPE, PORTRAIT, component
from .adapters import chess as ch
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans

_PIECE_FONTS = ("/System/Library/Fonts/Apple Symbols.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/TTF/DejaVuSans.ttf")
HOLLOW = {"K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙"}
SOLID = {"K": "♚", "Q": "♛", "R": "♜", "B": "♝", "N": "♞", "P": "♟"}


def _piece_font(px: int) -> Optional[ImageFont.FreeTypeFont]:
    for p in _PIECE_FONTS:
        if Path(p).exists():
            return ImageFont.truetype(p, px)
    return None


def _board(g: Glass, d: Dict[str, Any], x: int, y: int, size: int) -> None:
    sq = size // 8
    pf = _piece_font(int(sq * 0.86))
    last_from, last_to = d["last"]["from"], d["last"]["to"]
    for r, row in enumerate(d["board"]):
        for f, ch_ in enumerate(row):
            x0, y0 = x + f * sq, y + r * sq
            dark = (r + f) % 2 == 1
            g.rect((x0, y0, x0 + sq, y0 + sq), fill=LIGHT if dark else WHITE)
            name = d["files"][f] + d["ranks"][r]
            if name == last_to:
                g.rect((x0 + 1, y0 + 1, x0 + sq - 2, y0 + sq - 2), outline=BLACK, width=3)
            elif name == last_from:
                g.rect((x0 + 1, y0 + 1, x0 + sq - 2, y0 + sq - 2), outline=DARK, width=1)
            if ch_ == ".":
                continue
            white = ch_.isupper()
            cx, cy = x0 + sq // 2, y0 + sq // 2 + 1
            if pf is not None:
                if white:
                    # paint the solid twin white first so the hollow piece has a white body on a light square
                    g.d.text((cx, cy), SOLID[ch_.upper()], font=pf, fill=WHITE, anchor="mm")
                    g.d.text((cx, cy), HOLLOW[ch_.upper()], font=pf, fill=BLACK, anchor="mm")
                else:
                    g.d.text((cx, cy), SOLID[ch_.upper()], font=pf, fill=BLACK, anchor="mm")
            else:
                # no glyph font: a lettered disc — hollow for white, solid for black
                rr = sq // 2 - 6
                if white:
                    g.circle(cx, cy, rr, fill=WHITE, outline=BLACK, width=2)
                    g.text((cx, cy), ch_.upper(), sans(max(11, sq // 2), 700), BLACK, anchor="mm")
                else:
                    g.circle(cx, cy, rr, fill=BLACK)
                    g.text((cx, cy), ch_.upper(), sans(max(11, sq // 2), 700), WHITE, anchor="mm")
    g.rect((x - 1, y - 1, x + 8 * sq, y + 8 * sq), outline=BLACK, width=1)
    # coordinates, in the board's orientation
    for f, name in enumerate(d["files"]):
        g.text((x + f * sq + sq // 2, y + 8 * sq + 4), name, mono(11, 500), DARK, anchor="ma")
    for r, name in enumerate(d["ranks"]):
        g.text((x - 6, y + r * sq + sq // 2), name, mono(11, 500), DARK, anchor="rm")


def _solution_upside_down(g: Glass, d: Dict[str, Any], x: int, y: int, w: int) -> None:
    txt = "solution  " + "  ".join(d["solution_san"])
    f = mono(12, 500)
    tw, th = g.text_size(txt, f)
    tile = Image.new("L", (tw + 4, th + 6), 255)
    from PIL import ImageDraw
    dd = ImageDraw.Draw(tile)
    dd.fontmode = "1"
    dd.text((2, 2), txt, font=f, fill=DARK)
    tile = tile.rotate(180)
    g.im.paste(tile, (x + w - tile.width, y))


def _side(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, compact: bool) -> None:
    mover = d["to_move"].capitalize()
    g.text((x, y), f"{mover} to move", sans(22, 700), BLACK)
    g.text((x, y + 30), d["goal"], sans(17, 500), BLACK)
    y2 = y + 66
    g.hairline(x, y2, x + w, LIGHT)
    last = d["last"]
    g.text((x, y2 + 10), f"{last['by'].capitalize()} played", sans(11, 500), DARK)
    g.text((x + w, y2 + 8), last["san"], mono(15, 700), BLACK, anchor="ra")
    y3 = y2 + 40
    g.hairline(x, y3, x + w, LIGHT)
    g.text((x, y3 + 10), "rating", sans(11, 500), DARK)
    g.text((x + w, y3 + 8), f"{d['rating']}", mono(15, 700), BLACK, anchor="ra")
    g.text((x, y3 + 36), "tried by", sans(11, 500), DARK)
    g.text((x + w, y3 + 34), f"{d['plays']:,}", mono(15, 700), BLACK, anchor="ra")
    y4 = y3 + 66
    g.hairline(x, y4, x + w, LIGHT)
    yy = y4 + 10
    for p in d["players"]:
        who = (p["title"] + " " if p["title"] else "") + p["name"]
        g.text((x, yy), g.fit_text(who, w - 60, sans(13, 600)), sans(13, 600), BLACK)
        g.text((x + w, yy + 1), f"{p['rating'] or ''}", mono(12, 500), DARK, anchor="ra")
        g.text((x, yy + 17), p["color"], sans(11, 500), DARK)
        yy += 36
    if not compact:
        g.hairline(x, yy, x + w, LIGHT)
        for i, line in enumerate(g.wrap(" · ".join(d["themes"]), w, sans(12, 500), 2)):
            g.text((x, yy + 10 + i * 17), line, sans(12, 500), DARK)


@component(
    "chess", "Chess",
    "lichess's daily puzzle as a book prints it: two-gray board facing the side to move, hollow white / solid black "
    "pieces, the opponent's last move framed, the task in one line, rating, plays, players, themes — and the solution "
    "upside down in small mono at the bottom.",
    params={"date": "YYYY-MM-DD label", "demo": "1 = fixture puzzle"},
    fetch=ch.fetch,
    native_hint="type:'chess' {fen:'…', last:'h2g3', goal:'Mate in 2'} ≈ 90 B — the board is 64 squares and 12 glyphs",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        g.text((MARGIN, MARGIN - 6), "Daily puzzle", sans(24, 700), BLACK)
        g.text((MARGIN + 150, MARGIN + 4), d["date_label"] + ("  · demo" if d["demo"] else ""), sans(13, 500), DARK)
        g.text((g.w - MARGIN, MARGIN - 2), f"lichess.org/training/{d['id']}", mono(12, 500), DARK, anchor="ra")
        size = 8 * 48
        bx, by = MARGIN + 14, MARGIN + 40
        _board(g, d, bx, by, size)
        sx = bx + size + 34
        _side(g, d, sx, by - 2, g.w - MARGIN - sx, False)
        _solution_upside_down(g, d, MARGIN, g.h - MARGIN - 14, g.w - 2 * MARGIN)
    else:
        g = Glass(PORTRAIT)
        g.text((MARGIN, MARGIN - 6), "Daily puzzle", sans(24, 700), BLACK)
        g.text((g.w - MARGIN, MARGIN + 2), d["date_label"], sans(13, 500), DARK, anchor="ra")
        size = 8 * 52
        bx, by = MARGIN + 14, MARGIN + 44
        _board(g, d, bx, by, size)
        _side(g, d, MARGIN, by + size + 30, g.w - 2 * MARGIN, True)
        _solution_upside_down(g, d, MARGIN, g.h - MARGIN - 14, g.w - 2 * MARGIN)
    return g.snap()
