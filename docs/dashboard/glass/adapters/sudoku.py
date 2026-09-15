"""
▦ glass.adapters.sudoku — a puzzle for paper.

E-ink IS paper: a puzzle you look at for twenty minutes is the most native content
there is. Deterministic: seed = the date (params.seed overrides), so every Sticky in a
house shows the same puzzle and the answer is reproducible. Generator: fill a solved
grid by backtracking with a seeded shuffle, then remove cells while the puzzle keeps a
UNIQUE solution (counted with a bounded solver). Difficulty = how many clues stay:
easy 40, medium 32, hard 26. Pure Python, < 100 ms. Output: grid (0 = blank), solution,
clues, difficulty, seed label, a 4-char check code (sha1 of the solution) to compare
answers without printing them.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
from typing import Any, Dict, List, Optional

CLUES = {"easy": 40, "medium": 32, "hard": 26}


def _candidates(g: List[int], i: int) -> List[int]:
    r, c = divmod(i, 9)
    used = set(g[r * 9:(r + 1) * 9]) | {g[c + 9 * k] for k in range(9)}
    br, bc = 3 * (r // 3), 3 * (c // 3)
    used |= {g[(br + dr) * 9 + bc + dc] for dr in range(3) for dc in range(3)}
    return [v for v in range(1, 10) if v not in used]


def _solve(g: List[int], rng: Optional[random.Random] = None, limit: int = 1) -> int:
    """Count solutions up to `limit`, filling g in place with the first found."""
    try:
        i = g.index(0)
    except ValueError:
        return 1
    cands = _candidates(g, i)
    if rng:
        rng.shuffle(cands)
    n = 0
    for v in cands:
        g[i] = v
        n += _solve(g, rng, limit - n)
        if n >= limit:
            return n
        g[i] = 0
    return n


def generate(seed: str, difficulty: str = "medium") -> Dict[str, Any]:
    rng = random.Random(seed)
    sol = [0] * 81
    _solve(sol, rng)
    grid = sol[:]
    order = list(range(81))
    rng.shuffle(order)
    target = CLUES.get(difficulty, CLUES["medium"])
    clues = 81
    for i in order:
        if clues <= target:
            break
        keep = grid[i]
        grid[i] = 0
        probe = grid[:]
        if _solve(probe, None, 2) == 1:
            clues -= 1
        else:
            grid[i] = keep
    return {"grid": grid, "solution": sol, "clues": clues}


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    today = dt.date.fromisoformat(str(params["date"])) if params.get("date") else dt.date.today()
    difficulty = str(params.get("difficulty") or "medium").lower()
    if difficulty not in CLUES:
        difficulty = "medium"
    seed = str(params.get("seed") or f"sticky-sudoku-{today.isoformat()}-{difficulty}")
    p = generate(seed, difficulty)
    code = hashlib.sha1("".join(map(str, p["solution"])).encode()).hexdigest()[:4].upper()
    return {**p, "difficulty": difficulty, "date_label": today.strftime("%A, %-d %B"), "seed": seed,
            "code": code, "n": today.toordinal() % 1000, "demo": False}
