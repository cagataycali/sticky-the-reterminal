"""
🅰 glass.adapters.poster — a word, a line, or the owner's own text.

mode=word  → word of the day from the fixture list (rotated deterministically by
             ordinal day, so every Sticky in the fleet shows the same word today)
mode=line  → a line of poetry (public domain, or short quotation with source)
mode=text  → params.text (+ params.sub, params.by); no network, no fixture
Optional params.index pins an entry (tests). date=YYYY-MM-DD anchors the rotation.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "poster.json"


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    mode = str(params.get("mode") or ("text" if params.get("text") else "word"))
    today = dt.date.fromisoformat(str(params["date"])) if params.get("date") else dt.date.today()
    base = {"mode": mode, "date_label": today.strftime("%A, %-d %B"), "now": dt.datetime.now().strftime("%H:%M")}
    if mode == "text":
        return {**base, "text": str(params.get("text") or ""), "sub": str(params.get("sub") or ""),
                "by": str(params.get("by") or ""), "kicker": str(params.get("kicker") or "")}
    fx = json.loads(FIXTURE.read_text())
    pool = fx["words"] if mode == "word" else fx["lines"]
    i = int(params["index"]) if params.get("index") is not None else today.toordinal()
    e = pool[i % len(pool)]
    if mode == "word":
        return {**base, "kicker": "Word of the day", "text": e["word"], "pron": e["pron"], "pos": e["pos"],
                "sub": e["def"], "by": e.get("eg", ""), "n": len(pool)}
    return {**base, "kicker": "A line for today", "text": e["text"], "sub": "", "by": e["by"], "src": e.get("src", ""), "n": len(pool)}
