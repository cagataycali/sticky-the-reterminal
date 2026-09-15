"""
♞ glass.adapters.chess — lichess's daily puzzle, replayed to the position on the board.

Source: https://lichess.org/api/puzzle/daily (public, no key, 8 s timeout) → fixture
(glass/fixtures/chess.json, one real daily puzzle). The API hands back the game PGN and
`initialPly`; the puzzle position is the board AFTER initialPly + 1 plies — the opponent
has just moved, and that move is what you answer. python-chess replays it.

Output: id, url, rating, plays, date_label, to_move ("white"|"black"), goal ("Mate in 2" /
"Find the best move"), last {san, from, to, by}, board = 8 rows of 8 chars from the mover's
side (uppercase white, lowercase black, "." empty), files/ranks labels in that orientation,
solution_san list, players [{name, title, rating, color}], themes (human words), demo.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Dict, List

import chess
import requests

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "chess.json"
UA = {"User-Agent": "sticky-glass/1.0 (+https://github.com/cagataycali/sticky-the-reterminal)"}
URL = "https://lichess.org/api/puzzle/daily"

_THEME_WORDS = {"mateIn1": "mate in 1", "mateIn2": "mate in 2", "mateIn3": "mate in 3", "mateIn4": "mate in 4",
                "mateIn5": "mate in 5+", "short": "short", "long": "long", "veryLong": "very long", "oneMove": "one move",
                "middlegame": "middlegame", "endgame": "endgame", "opening": "opening", "master": "master game",
                "masterVsMaster": "masters", "superGM": "super GM", "crushing": "crushing", "advantage": "advantage",
                "equality": "equality", "fork": "fork", "pin": "pin", "skewer": "skewer", "sacrifice": "sacrifice",
                "discoveredAttack": "discovered attack", "deflection": "deflection", "attraction": "attraction",
                "quietMove": "quiet move", "zugzwang": "zugzwang", "backRankMate": "back-rank mate",
                "smotheredMate": "smothered mate", "epauletteMate": "epaulette mate", "arabianMate": "arabian mate",
                "anastasiaMate": "anastasia mate", "bodenMate": "boden mate", "doubleBishopMate": "double-bishop mate",
                "hangingPiece": "hanging piece", "trappedPiece": "trapped piece", "exposedKing": "exposed king",
                "kingsideAttack": "kingside attack", "queensideAttack": "queenside attack", "promotion": "promotion",
                "advancedPawn": "advanced pawn", "defensiveMove": "defensive move", "clearance": "clearance",
                "interference": "interference", "intermezzo": "intermezzo", "xRayAttack": "x-ray", "capturingDefender": "capturing the defender",
                "rookEndgame": "rook endgame", "pawnEndgame": "pawn endgame", "queenEndgame": "queen endgame",
                "bishopEndgame": "bishop endgame", "knightEndgame": "knight endgame", "queenRookEndgame": "queen + rook endgame"}


def _words(t: str) -> str:
    return _THEME_WORDS.get(t) or re.sub(r"([a-z])([A-Z])", r"\1 \2", t).lower()


def _parse(j: Dict[str, Any], demo: bool, when: dt.date) -> Dict[str, Any]:
    pz, game = j["puzzle"], j["game"]
    moves = game["pgn"].split()
    b = chess.Board()
    for san in moves[: pz["initialPly"] + 1]:
        b.push_san(san)
    last_uci = b.peek().uci()
    last_san = moves[pz["initialPly"]]
    to_move = "white" if b.turn else "black"
    sol_san: List[str] = []
    bb = b.copy()
    for u in pz["solution"]:
        m = chess.Move.from_uci(u)
        sol_san.append(bb.san(m))
        bb.push(m)
    mate = [t for t in pz.get("themes", []) if t.startswith("mateIn")]
    if mate:
        n = mate[0][6:]
        goal = f"Mate in {n}" if n.isdigit() else "Mate in 5+"
    else:
        goal = "Find the best move"
    # board from the mover's side
    ranks = range(7, -1, -1) if b.turn else range(0, 8)
    files = range(0, 8) if b.turn else range(7, -1, -1)
    rows = []
    for r in ranks:
        row = ""
        for f in files:
            p = b.piece_at(chess.square(f, r))
            row += p.symbol() if p else "."
        rows.append(row)
    players = []
    for p in game.get("players", []):
        players.append({"name": p.get("name", "?"), "title": p.get("title", ""), "rating": p.get("rating"), "color": p.get("color", "")})
    return {"id": pz["id"], "url": f"https://lichess.org/training/{pz['id']}", "rating": pz.get("rating"),
            "plays": pz.get("plays"), "date_label": when.strftime("%A, %-d %B"), "to_move": to_move, "goal": goal,
            "last": {"san": last_san, "from": last_uci[:2], "to": last_uci[2:4], "by": "black" if b.turn else "white"},
            "board": rows, "files": [chess.FILE_NAMES[f] for f in files], "ranks": [str(r + 1) for r in ranks],
            "solution_san": sol_san, "players": players, "themes": [_words(t) for t in pz.get("themes", [])][:6],
            "n_moves": (len(pz["solution"]) + 1) // 2, "demo": demo}


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    when = dt.date.fromisoformat(str(params["date"])) if params.get("date") else dt.date.today()
    if not params.get("demo"):
        try:
            r = requests.get(URL, headers=UA, timeout=8)
            r.raise_for_status()
            return _parse(r.json(), False, when)
        except Exception:
            pass
    return _parse(json.loads(FIXTURE.read_text()), True, when)
