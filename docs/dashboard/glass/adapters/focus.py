"""
⏱ glass.adapters.focus — the day's focus blocks against the calendar.

Blocks: params.blocks (JSON list of {start,end,label,done}) → ~/.tiny/sticky-focus.json
→ fixture. Calendar: glass.adapters.calendar.fetch_day (ICS or fixture). Output:
the day window, blocks with minutes + state (done / now / planned / missed —
planned but the end has passed and not done), events, focus minutes done/planned,
the current block with minutes left, the next block, and the FREE WINDOWS — gaps
between events and blocks ≥ 30 min still ahead of now, biggest first. No network
of its own.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import calendar as cal

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "focus.json"
USER_FILE = Path.home() / ".tiny" / "sticky-focus.json"


def _m(hhmm: str) -> int:
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _load(params: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    if params.get("blocks"):
        b = params["blocks"]
        if isinstance(b, str):
            b = json.loads(b)
        base = json.loads(FIXTURE.read_text())
        return {**base, "blocks": list(b)}, "params"
    if not params.get("demo") and USER_FILE.exists():
        return json.loads(USER_FILE.read_text()), "file"
    return json.loads(FIXTURE.read_text()), "demo"


def free_windows(busy: List[Tuple[int, int]], start: int, end: int, min_len: int = 30) -> List[Dict[str, Any]]:
    cur = start
    out = []
    for s, e in sorted(busy):
        if s > cur and s - cur >= min_len:
            out.append({"start": _hm(cur), "end": _hm(s), "min": s - cur, "s": cur, "e": s})
        cur = max(cur, e)
    if end - cur >= min_len:
        out.append({"start": _hm(cur), "end": _hm(end), "min": end - cur, "s": cur, "e": end})
    return sorted(out, key=lambda w: -w["min"])


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    cfg, source = _load(params)
    day = cal.fetch_day(params)
    now_dt = dt.datetime.now()
    now = params.get("clock")
    now_m = _m(now) if now else now_dt.hour * 60 + now_dt.minute
    ds, de = _m(cfg.get("day_start", "07:00")), _m(cfg.get("day_end", "20:00"))
    blocks = []
    for b in cfg.get("blocks", []):
        s, e = _m(b["start"]), _m(b["end"])
        done = bool(b.get("done"))
        state = "done" if done else ("now" if s <= now_m < e else ("missed" if e <= now_m else "planned"))
        blocks.append({"start": b["start"], "end": b["end"], "s": s, "e": e, "min": e - s,
                       "label": str(b.get("label") or "focus"), "state": state})
    events = [{"title": e["title"], "start": e["start"], "end": e["end"],
               "s": e["dt_start"].hour * 60 + e["dt_start"].minute, "e": e["dt_end"].hour * 60 + e["dt_end"].minute}
              for e in day["events"]]
    current = next((b for b in blocks if b["state"] == "now"), None)
    nxt = next((b for b in blocks if b["state"] == "planned"), None)
    done_min = sum(b["min"] for b in blocks if b["state"] == "done")
    planned_min = sum(b["min"] for b in blocks)
    busy = [(b["s"], b["e"]) for b in blocks] + [(e["s"], e["e"]) for e in events]
    free = [w for w in free_windows(busy, max(ds, now_m), de)]
    meeting_min = sum(max(0, min(e["e"], de) - max(e["s"], ds)) for e in events)
    return {
        "day_start": ds, "day_end": de, "now_m": now_m, "now": _hm(now_m), "blocks": blocks, "events": events,
        "current": {**current, "left": current["e"] - now_m} if current else None, "next": nxt,
        "done_min": done_min, "planned_min": planned_min, "sessions_done": sum(1 for b in blocks if b["state"] == "done"),
        "sessions": len(blocks), "missed": sum(1 for b in blocks if b["state"] == "missed"),
        "meeting_min": meeting_min, "free": free[:3], "source": source, "calendar_demo": day["calendar"].lower().startswith("demo") or bool(params.get("demo")),
        "date_label": day["day_label"],
    }
