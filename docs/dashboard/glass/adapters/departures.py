"""
🚇 glass.adapters.departures — the next trains from a fixed stop.

Source: params.lines (JSON) → ~/.tiny/sticky-departures.json → fixture (Bedford Av L).
No transit API is wired on purpose: every city's feed is different (GTFS-RT protobuf +
static stop tables), so the contract is a plain list an agent or cron fills in:
  {stop, agency, walk_min, lines: [{line, dest, times: ["+3","+9"] | ["14:07", …],
   kind: "train"|"bus"|"tram"|"ferry", note}], alerts: [str]}
Times may be relative ("+3" minutes) or absolute clock times; both become minutes-to.

Output: stop, agency, walk_min, at (HH:MM), lines [{line, dest, kind, note, mins:[int],
leave_in (mins - walk, first catchable)}], alerts, demo.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "departures.json"
USER_FILE = Path.home() / ".tiny" / "sticky-departures.json"


def _mins(t: Any, now: dt.datetime) -> Optional[int]:
    s = str(t).strip()
    try:
        if s.startswith("+"):
            return int(float(s[1:]))
        if ":" in s:
            hh, mm = s.split(":")[:2]
            when = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            if when < now - dt.timedelta(minutes=1):
                when += dt.timedelta(days=1)
            return int((when - now).total_seconds() // 60)
        return int(float(s))
    except ValueError:
        return None


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    now = dt.datetime.now().replace(second=0, microsecond=0)
    src: Optional[Dict[str, Any]] = None
    if params.get("lines"):
        raw = params["lines"]
        src = {"stop": params.get("stop", ""), "agency": params.get("agency", ""), "walk_min": params.get("walk_min", 0),
               "lines": json.loads(raw) if isinstance(raw, str) else raw, "alerts": params.get("alerts") or []}
        source = "pushed"
    elif not params.get("demo") and USER_FILE.exists():
        try:
            src = json.loads(USER_FILE.read_text())
            source = "file"
        except (OSError, ValueError):
            src = None
    if src is None:
        src = json.loads(FIXTURE.read_text())
        source = "demo"
    walk = int(src.get("walk_min") or 0)
    lines: List[Dict[str, Any]] = []
    for ln in src.get("lines", []):
        mins = sorted(m for m in (_mins(t, now) for t in ln.get("times", [])) if m is not None and m >= 0)
        catch = [m for m in mins if m >= walk]
        lines.append({"line": str(ln.get("line", "?")), "dest": str(ln.get("dest", "")), "kind": str(ln.get("kind", "train")),
                      "note": ln.get("note"), "mins": mins[:4], "leave_in": (catch[0] - walk) if catch else None})
    return {"stop": str(src.get("stop", "")), "agency": str(src.get("agency", "")), "walk_min": walk,
            "at": now.strftime("%H:%M"), "lines": lines, "alerts": [str(a) for a in src.get("alerts", [])][:3],
            "source": source, "demo": source == "demo"}
