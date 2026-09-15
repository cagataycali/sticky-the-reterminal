"""
💸 budget — the month's money, as a pace.

Not a pie chart. The question a budget answers on the 7th is "am I ahead of
the month or behind it?", so the picture is a pace chart: the even burn as a
light diagonal from zero to the month's discretionary budget, the real
cumulative spend as a black stepped line up to today, ending in a dot. Above
or below the diagonal is the whole story; the headline numeral says it in
money — spent so far, and over/under pace. Bills marked fixed are taken off the
top first (rent on the 1st should not make every month start "over"). Beside
it: categories as bars ranked by size, what is left per remaining day, and the
projection to month end. Data: glass.adapters.budget (pushed · ~/.tiny/sticky-budget.json · fixture).
"""
from __future__ import annotations

from typing import Any, Dict

from PIL import Image

from . import LANDSCAPE, PORTRAIT, component
from .adapters import budget as bd
from .canvas import BLACK, DARK, LIGHT, MARGIN, WHITE, Glass, mono, sans


def _money(v: float, cur: str, cents: bool = False) -> str:
    return f"{cur}{v:,.2f}" if cents else f"{cur}{v:,.0f}"


def _pace(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, h: int) -> None:
    dim = d["days_in_month"]
    top, floor = y + 8, y + h - 18
    vb = max(d["var_budget"], d["variable"], 1.0) * 1.05

    def X(day: float) -> int:
        return round(x + (day - 1) / (dim - 1) * w)

    def Y(v: float) -> int:
        return round(floor - v / vb * (floor - top))

    # ceiling and even-burn diagonal
    g.hairline(x, Y(d["var_budget"]), x + w, LIGHT)
    g.text((x + w, Y(d["var_budget"]) - 14), _money(d["var_budget"], d["currency"]), mono(11, 500), DARK, anchor="ra")
    g.d.line([(X(1), Y(0)), (X(dim), Y(d["var_budget"]))], fill=LIGHT, width=4)
    g.hairline(x, floor, x + w, DARK)
    for day in (1, 8, 15, 22, dim):
        g.d.line([(X(day), floor), (X(day), floor + 4)], fill=DARK, width=1)
        g.text((X(day), floor + 6), str(day), mono(11, 500), DARK, anchor="ma")
    # actual: stepped
    cum = d["cumulative"]
    pts = [(X(1), Y(0))]
    run = 0.0
    for c in cum:
        pts.append((X(c["day"]), Y(run)))
        run = c["total"]
        pts.append((X(c["day"]), Y(run)))
    if len(pts) > 1:
        g.d.line(pts, fill=BLACK, width=3)
    nx, ny = X(d["day"]), Y(d["variable"])
    g.circle(nx, ny, 5, fill=BLACK)
    g.d.line([(nx, ny), (nx, floor)], fill=DARK, width=1)
    # pace point today, hollow
    px, py = X(d["day"]), Y(d["pace"])
    g.circle(px, py, 4, fill=WHITE, outline=DARK, width=1)


def _cats(g: Glass, d: Dict[str, Any], x: int, y: int, w: int, max_h: int) -> int:
    cats = d["categories"]
    if not cats:
        return y
    top = cats[0]["total"] or 1.0
    yy = y
    for c in cats:
        if yy + 26 > y + max_h:
            break
        g.text((x, yy), g.fit_text(c["name"], w - 90, sans(12, 600)), sans(12, 600), BLACK)
        g.text((x + w, yy), _money(c["total"], d["currency"]), mono(12, 500), BLACK, anchor="ra")
        bw = round((w - 2) * c["total"] / top)
        g.rect((x, yy + 17, x + w, yy + 21), fill=LIGHT)
        g.rect((x, yy + 17, x + bw, yy + 21), fill=BLACK)
        yy += 30
    return yy


@component(
    "budget", "Budget",
    "The month's money as a pace chart: even burn as a light diagonal to the discretionary budget, real cumulative spend "
    "as a black stepped line to today (above or below the diagonal is the story), bills taken off the top first; headline "
    "spent + over/under pace, categories as ranked bars, left per day, projection to month end, recent entries.",
    params={"entries": "JSON [{date, amount, category, note, fixed?}]", "month_budget": "number", "currency": "$ € ₺", "at": "YYYY-MM-DD pin", "demo": "1 = fixture"},
    fetch=bd.fetch,
    native_hint="type:'budget' {spent, pace, budget, cum:[u16×31]} ≈ 70 B — a stepped polyline + one diagonal",
)
def render(d: Dict[str, Any], orientation: str) -> Image.Image:
    cur = d["currency"]
    over = d["delta"] >= 0
    verdict = f"{_money(abs(d['delta']), cur)} {'over' if over else 'under'} pace"
    sub = f"of {_money(d['var_budget'], cur)} to spend" + (f" after {_money(d['fixed'], cur)} in bills" if d["fixed"] else "")
    if orientation == "landscape":
        g = Glass(LANDSCAPE)
        g.text((MARGIN, MARGIN - 6), d["month_label"], sans(24, 700), BLACK)
        g.text((g.w - MARGIN, MARGIN - 2), f"day {d['day']} of {d['days_in_month']} · budget {_money(d['budget'], cur)}" + ("  · demo" if d["demo"] else ""), mono(12, 500), DARK, anchor="ra")
        lx, lw = MARGIN, 470
        g.text((lx, MARGIN + 34), _money(d["variable"], cur), mono(48, 700), BLACK)
        vx = lx + g.text_size(_money(d["variable"], cur), mono(48, 700))[0] + 16
        g.text((vx, MARGIN + 44), verdict, sans(15, 700), BLACK)
        g.text((vx, MARGIN + 66), g.fit_text(sub, lx + lw - vx, sans(12, 500)), sans(12, 500), DARK)
        _pace(g, d, lx, MARGIN + 100, lw, 250)
        g.text((lx, g.h - MARGIN - 16), "light diagonal = even burn · black = spent · hollow dot = where pace says you would be", sans(11, 500), DARK)
        rx = MARGIN + lw + 34
        rw = g.w - MARGIN - rx
        g.d.line([(rx - 16, MARGIN + 34), (rx - 16, g.h - MARGIN)], fill=LIGHT, width=1)
        g.label((rx, MARGIN + 34), "where it went", 11, DARK)
        yy = _cats(g, d, rx, MARGIN + 54, rw, 190)
        yy += 6
        g.hairline(rx, yy, rx + rw, LIGHT)
        yy += 10
        g.text((rx, yy), _money(d["daily_left"], cur), mono(22, 700), BLACK)
        g.text((rx + g.text_size(_money(d["daily_left"], cur), mono(22, 700))[0] + 8, yy + 8), f"a day for {d['days_in_month'] - d['day']} days", sans(12, 500), DARK)
        yy += 34
        proj = d["projected"]
        g.text((rx, yy), _money(proj, cur), mono(22, 700), BLACK)
        pd = proj - d["budget"]
        g.text((rx + g.text_size(_money(proj, cur), mono(22, 700))[0] + 8, yy + 8), f"projected · {_money(abs(pd), cur)} {'over' if pd >= 0 else 'under'}", sans(12, 500), DARK)
        yy += 40
        g.label((rx, yy - 2), "recent", 11, DARK)
        yy += 18
        for r in d["recent"][:5]:
            if yy + 16 > g.h - MARGIN:
                break
            g.text((rx, yy), g.fit_text(f"{r['date_label']} · {r['note'] or r['category']}", rw - 70, sans(12, 500)), sans(12, 500), DARK)
            g.text((rx + rw, yy), _money(r["amount"], cur, cents=True), mono(12, 500), BLACK, anchor="ra")
            yy += 18
    else:
        g = Glass(PORTRAIT)
        g.text((MARGIN, MARGIN - 6), d["month_label"], sans(24, 700), BLACK)
        g.text((MARGIN, MARGIN + 26), f"day {d['day']} of {d['days_in_month']} · budget {_money(d['budget'], cur)}" + ("  · demo" if d["demo"] else ""), mono(12, 500), DARK)
        g.text((MARGIN, MARGIN + 54), _money(d["variable"], cur), mono(48, 700), BLACK)
        g.text((MARGIN, MARGIN + 112), verdict, sans(15, 700), BLACK)
        g.text((MARGIN, MARGIN + 134), g.fit_text(sub, g.w - 2 * MARGIN, sans(12, 500)), sans(12, 500), DARK)
        _pace(g, d, MARGIN, MARGIN + 160, g.w - 2 * MARGIN, 210)
        y = MARGIN + 390
        g.hairline(MARGIN, y, g.w - MARGIN, LIGHT)
        g.label((MARGIN, y + 10), "where it went", 11, DARK)
        yy = _cats(g, d, MARGIN, y + 30, g.w - 2 * MARGIN, 160)
        yy += 8
        g.hairline(MARGIN, yy, g.w - MARGIN, LIGHT)
        yy += 12
        g.text((MARGIN, yy), _money(d["daily_left"], cur), mono(22, 700), BLACK)
        g.text((MARGIN + g.text_size(_money(d["daily_left"], cur), mono(22, 700))[0] + 8, yy + 8), f"a day for {d['days_in_month'] - d['day']} days", sans(12, 500), DARK)
        proj = d["projected"]
        pd = proj - d["budget"]
        g.text((g.w - MARGIN, yy + 8), f"projected {_money(proj, cur)} · {_money(abs(pd), cur)} {'over' if pd >= 0 else 'under'}", sans(12, 500), DARK, anchor="ra")
    return g.snap()
