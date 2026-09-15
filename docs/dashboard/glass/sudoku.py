"""
▦ sudoku — a puzzle for paper.

The most e-ink-native card there is: something you look at for twenty minutes
and solve with a pencil in your head. The grid is drawn like a printed one —
hairline cells, BLACK box lines 3 px, clues in a heavy mono face so they never
read as pencil marks — sized to the panel (portrait 432 px, landscape 420 px).
Beside it: the date, difficulty and clue count, a four-letter check code (a hash
of the solution, so two people can compare answers without spoiling), and the
three rules in one line for whoever has never met one. Deterministic per day.
Data: glass.adapters.sudoku (generator + uniqueness check, no network).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import sudoku as sd
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans

RULES = "Every row, column and 3×3 box holds 1–9 once."


def _grid(g: Glass, d: Dict[str, Any], x0: int, y0: int, size: int) -> None:
    cell = size // 9
    size = cell * 9
    g.rect((x0, y0, x0 + size, y0 + size), fill=WHITE)
    for k in range(10):
        p = k * cell
        heavy = k % 3 == 0
        w = 3 if heavy else 1
        ink = BLACK if heavy else DARK
        g.d.line([(x0 + p, y0), (x0 + p, y0 + size)], fill=ink, width=w)
        g.d.line([(x0, y0 + p), (x0 + size, y0 + p)], fill=ink, width=w)
    f = mono(int(cell * 0.62), 700)
    for i, v in enumerate(d["grid"]):
        if v:
            r, c = divmod(i, 9)
            g.text((x0 + c * cell + cell // 2 + 1, y0 + r * cell + cell // 2 + 1), str(v), f, BLACK, anchor="mm")


def _side(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, stacked: bool) -> None:
    g.label((x, y), f"sudoku · no. {d['n']}", 12, DARK)
    y += 22
    g.text((x, y), d["difficulty"].capitalize(), sans(30, 700), BLACK)
    y += 40
    g.text((x, y), f"{d['clues']} clues · {81 - d['clues']} to fill", sans(14, 500), DARK)
    y += 30
    g.label((x, y), "check code", 11, DARK)
    y += 18
    g.text((x, y), d["code"], mono(28, 700), BLACK)
    y += 38
    for ln in g.wrap("Solve it, then compare codes with whoever else has today's — same code, same answer.", w, sans(12, 500), max_lines=3):
        g.text((x, y), ln, sans(12, 500), DARK)
        y += 16
    y += 12
    g.hairline(x, y, x + w, LIGHT)
    y += 10
    for ln in g.wrap(RULES, w, sans(13, 500), max_lines=2):
        g.text((x, y), ln, sans(13, 500), BLACK)
        y += 18


@component(
    "sudoku", "Sudoku",
    "A printed-looking 9×9 with a unique solution, seeded by the date (same puzzle on every Sticky, same day); "
    "difficulty easy/medium/hard, clue count, a 4-letter check code to compare answers, and the rule in one line.",
    params={"difficulty": "easy|medium|hard", "date": "YYYY-MM-DD seed override", "seed": "any string seed"},
    fetch=sd.fetch,
    native_hint="type:'sudoku' {grid:'81 chars', difficulty, code} = 100 B — the firmware could draw it from a string",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        size = g.h - 2 * MARGIN - 6          # 426 → 47 px cells
        _grid(g, d, MARGIN, MARGIN + 3, size)
        sx = MARGIN + (size // 9) * 9 + 40
        _side(g, d, sx, MARGIN, g.w - MARGIN - sx, stacked=False)
        g.text((g.w - MARGIN, g.h - MARGIN - 8), d["date_label"], sans(12, 500), DARK, anchor="ra")
    else:
        g = Glass(PORTRAIT)
        size = g.w - 2 * MARGIN               # 432 → 48 px cells
        g.label((MARGIN, MARGIN), f"sudoku · no. {d['n']}  ·  {d['difficulty']}  ·  {d['clues']} clues", 12, DARK)
        g.text((g.w - MARGIN, MARGIN - 2), d["code"], mono(14, 700), BLACK, anchor="ra")
        _grid(g, d, MARGIN, MARGIN + 30, size)
        y = MARGIN + 30 + (size // 9) * 9 + 24
        g.text((MARGIN, y), d["date_label"], sans(16, 600), BLACK)
        y += 28
        for ln in g.wrap(RULES + " Compare the check code with whoever else has today's.", g.w - 2 * MARGIN, sans(13, 500), max_lines=4):
            g.text((MARGIN, y), ln, sans(13, 500), DARK)
            y += 18
    return g.snap()

