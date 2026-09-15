"""
☐ glass.adapters.todo — a plain list of things to do.

Source: params.items (JSON list of {text, done, due?, tag?, top?}) → ~/.tiny/sticky-todo.json
→ fixture. Output: open items grouped by due bucket (today / tomorrow / week / later, in
that order, keeping file order inside a bucket), the one `top` item (or the first open
one), done items (most recent last in file = shown last), and counts. No network.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "todo.json"
USER_FILE = Path.home() / ".tiny" / "sticky-todo.json"
BUCKETS = ("today", "tomorrow", "week", "later")


def _load(params: Dict[str, Any]):
    if params.get("items"):
        it = params["items"]
        if isinstance(it, str):
            it = json.loads(it)
        return {"title": params.get("title") or "To do", "items": list(it)}, "params"
    if not params.get("demo") and USER_FILE.exists():
        return json.loads(USER_FILE.read_text()), "file"
    return json.loads(FIXTURE.read_text()), "demo"


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    cfg, source = _load(params)
    items: List[Dict[str, Any]] = []
    for i, it in enumerate(cfg.get("items", [])):
        if isinstance(it, str):
            it = {"text": it}
        due = str(it.get("due") or "later").lower()
        items.append({"text": str(it.get("text") or "").strip(), "done": bool(it.get("done")),
                      "due": due if due in BUCKETS else "later", "tag": str(it.get("tag") or ""),
                      "top": bool(it.get("top")), "i": i})
    open_ = [x for x in items if not x["done"] and x["text"]]
    done = [x for x in items if x["done"] and x["text"]]
    top = next((x for x in open_ if x["top"]), open_[0] if open_ else None)
    buckets = [{"name": b, "items": [x for x in open_ if x["due"] == b]} for b in BUCKETS]
    buckets = [b for b in buckets if b["items"]]
    return {"title": str(cfg.get("title") or "To do"), "open": open_, "done": done, "top": top,
            "buckets": buckets, "n_open": len(open_), "n_done": len(done),
            "n_today": sum(1 for x in open_ if x["due"] == "today"), "source": source}
