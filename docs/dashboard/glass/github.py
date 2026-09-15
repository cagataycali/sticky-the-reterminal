"""
🐙 github — the contribution graph, finally on a medium that suits it.

GitHub's heatmap is four greens; this glass has four grays — a 1:1 fit. Twenty
weeks of days as a 7×20 grid (Sunday rows first, like the site), quantile
levels: empty · LIGHT · DARK · BLACK, month labels above, M/W/F on the left.
Beside it the four numbers that matter: today, this week, streak, best day;
open PRs and issues as a footer. Right column: the latest activity, one line
each — age, repo, what happened (pushed 3 · message / opened PR #12 · title).
Portrait: grid on top, numbers, then the list.

Data: glass.adapters.github (GraphQL calendar + REST events; fixture demo).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import github as ghd
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _level(v, levels) -> int:
    if v is None:
        return -1
    if v == 0:
        return 0
    q1, q2, q3 = levels
    return 1 if v <= q1 else 2 if v <= q3 else 3


def _grid(g: Glass, d: Dict[str, Any], x: int, y: int, cell: int, gap: int) -> tuple[int, int]:
    """Draws the heatmap with its labels; returns (right, bottom)."""
    step = cell + gap
    lx = x + 26                     # room for day labels
    mf = sans(11, 600)
    for w, m in enumerate(d["months"]):
        if m:
            g.text((lx + w * step, y), m, mf, DARK)
    gy = y + 16
    for i, lab in ((1, "M"), (3, "W"), (5, "F")):
        g.text((x + 10, gy + i * step + cell // 2), lab, sans(11, 600), DARK, anchor="mm")
    for w, col in enumerate(d["weeks"]):
        for i, v in enumerate(col):
            lv = _level(v, d["levels"])
            if lv < 0:
                continue
            x0, y0 = lx + w * step, gy + i * step
            box = (x0, y0, x0 + cell, y0 + cell)
            if lv == 0:
                g.rect(box, outline=LIGHT, width=1, radius=2)
            else:
                g.rect(box, fill=(LIGHT, DARK, BLACK)[lv - 1], radius=2)
    # today's cell ringed
    tw = len(d["weeks"]) - 1
    ti = max(i for i, v in enumerate(d["weeks"][-1]) if v is not None)
    x0, y0 = lx + tw * step, gy + ti * step
    g.rect((x0 - 2, y0 - 2, x0 + cell + 2, y0 + cell + 2), outline=BLACK, width=1, radius=3)
    return lx + len(d["weeks"]) * step, gy + 7 * step


def _stat(g: Glass, x: int, y: int, value: str, label: str, big: int = 30) -> int:
    g.text((x, y), value, mono(big, 700), BLACK)
    g.label((x, y + big + 6), label, 11, DARK)
    return y + big + 26


def _events(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, y_max: int, rh: int = 40) -> None:
    g.label((x, y), "Latest", 13, DARK)
    g.hairline(x, y + 18, x + w, DARK)
    y += 26
    af, rf, wf = mono(12, 500), sans(13, 700), sans(13, 500)
    for i, e in enumerate(d["events"]):
        if y + rh > y_max:
            break
        if i:
            g.hairline(x, y - 4, x + w, LIGHT)
        ago = e["ago"]
        aw = g.text_size(ago, af)[0]
        g.text((x + w, y + 1), ago, af, DARK, anchor="ra")
        repo = e["repo"].split("/")[-1]
        g.text((x, y), g.fit_text(repo, w - aw - 10, rf), rf, BLACK)
        g.text((x, y + 18), g.fit_text(e["what"], w, wf), wf, DARK)
        y += rh


def _header(g: Glass, d: Dict[str, Any], y: int) -> int:
    g.text((MARGIN, y), "GitHub", sans(34, 700), BLACK)
    x = MARGIN + g.text_size("GitHub", sans(34, 700))[0] + 14
    g.text((x, y + 12), g.fit_text(f"@{d['login']} · {d['today_label']}", g.w // 2 - x + 40, sans(16, 500)), sans(16, 500), DARK)
    g.text((g.w - MARGIN, y + 4), d["now"], mono(18, 500), BLACK, anchor="ra")
    g.text((g.w - MARGIN, y + 28), f"{d['total_year']:,} contributions this year", sans(14, 600), DARK, anchor="ra")
    return y + 52


@component(
    "github", "GitHub",
    "Twenty weeks of the contribution graph in four grays, today · week · streak · best, open PRs/"
    "issues, and the latest activity one line each.",
    params={"weeks": "columns (default 20)", "demo": "1 → fixture"},
    fetch=ghd.fetch,
    native_hint="type:'github' {login, year, today, week, streak, grid: 140 × u2 (35 B packed), events:[{ago, repo, what}]} ≈ 500 B; refresh every 15 min",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        y = _header(g, d, MARGIN)
        right, bottom = _grid(g, d, MARGIN, y + 4, cell=17, gap=4)
        # numbers under the grid, four across
        sy = bottom + 18
        cw = (right - MARGIN) // 4
        for i, (v, lab) in enumerate(((str(d["today"]), "today"), (str(d["this_week"]), "this week"),
                                      (f"{d['streak']} d", "streak"), (str(d["best"]["count"]), "best day"))):
            _stat(g, MARGIN + i * cw, sy, v, lab, big=28)
        fy = sy + 62
        g.hairline(MARGIN, fy, right, LIGHT)
        g.text((MARGIN, fy + 10), f"{d['open_prs']} open PRs · {d['open_issues']} open issues", sans(14, 500), DARK)
        ex = right + 28
        g.vline(ex - 14, y + 4, g.h - MARGIN, LIGHT)
        _events(g, d, ex, y + 4, g.w - MARGIN - ex, g.h - MARGIN)
    else:
        g = Glass(PORTRAIT)
        y = _header(g, d, MARGIN)
        right, bottom = _grid(g, d, MARGIN, y + 4, cell=16, gap=4)
        sy = bottom + 16
        cw = (g.w - 2 * MARGIN) // 4
        for i, (v, lab) in enumerate(((str(d["today"]), "today"), (str(d["this_week"]), "week"),
                                      (f"{d['streak']} d", "streak"), (str(d["best"]["count"]), "best"))):
            _stat(g, MARGIN + i * cw, sy, v, lab, big=26)
        fy = sy + 58
        g.text((MARGIN, fy), f"{d['open_prs']} open PRs · {d['open_issues']} open issues", sans(13, 500), DARK)
        g.hairline(MARGIN, fy + 24, g.w - MARGIN, LIGHT)
        _events(g, d, MARGIN, fy + 34, g.w - 2 * MARGIN, g.h - MARGIN, rh=40)
    return g.snap()
