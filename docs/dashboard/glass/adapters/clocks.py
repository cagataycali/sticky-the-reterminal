"""
🕰 glass.adapters.clocks — the people you work with, in their hour.

Source: params.zones ("Label=Area/City,Label=Area/City" or bare IANA names) →
~/.tiny/sticky-clocks.json ([{label, zone, home?: true}]) → fixture. The first entry
(or the one flagged home) is the reference. Output per place: label, zone abbreviation,
local HH:MM, weekday (only when it differs from home), offset in hours from home
("+9", "−3½"), awake 0/1 (07–22 local), hour-of-day float for the 24-h strip. Plus the
overlap: for each hour of HOME's day, how many places are awake (07–22); the call window
is the run of hours where that count peaks, as [start, end) pairs, plus the next one. params.at="YYYY-MM-DD HH:MM" pins the instant.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "clocks.json"
USER_FILE = Path.home() / ".tiny" / "sticky-clocks.json"
AWAKE = (7, 22)


def _places(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    if params.get("zones"):
        out = []
        for tok in str(params["zones"]).split(","):
            tok = tok.strip()
            if not tok:
                continue
            label, _, zone = tok.rpartition("=")
            zone = zone or tok
            out.append({"label": label or zone.rsplit("/", 1)[-1].replace("_", " "), "zone": zone})
        return out
    if USER_FILE.exists() and not params.get("demo"):
        try:
            data = json.loads(USER_FILE.read_text())
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    return json.loads(FIXTURE.read_text())


def _fmt_off(h: float) -> str:
    sign = "+" if h >= 0 else "−"
    a = abs(h)
    whole, frac = int(a), a - int(a)
    s = f"{sign}{whole}"
    if frac:
        s += "½" if abs(frac - 0.5) < 0.01 else f".{int(frac * 100):02d}".rstrip("0")
    return s if a else "0"


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    places = _places(params)
    home_i = next((i for i, p in enumerate(places) if p.get("home")), 0)
    home = places[home_i]
    hz = ZoneInfo(home["zone"])
    if params.get("at"):
        now = dt.datetime.fromisoformat(str(params["at"])).replace(tzinfo=hz)
    else:
        now = dt.datetime.now(hz)
    utc = now.astimezone(dt.timezone.utc)
    home_off = now.utcoffset().total_seconds() / 3600
    rows = []
    for i, p in enumerate(places):
        z = ZoneInfo(p["zone"])
        loc = utc.astimezone(z)
        off = loc.utcoffset().total_seconds() / 3600 - home_off
        hod = loc.hour + loc.minute / 60
        rows.append({
            "label": p["label"], "zone": p["zone"], "abbr": loc.tzname() or "",
            "time": loc.strftime("%H:%M"), "weekday": loc.strftime("%a") if loc.date() != now.date() else "",
            "offset": off, "offset_label": "home" if i == home_i else _fmt_off(off),
            "awake": AWAKE[0] <= hod < AWAKE[1], "hod": hod, "home": i == home_i,
            "date_label": loc.strftime("%a %-d %b"),
        })
    # overlap in HOME hours: how many places are awake at each hour; the "call window" is the
    # run of hours where that count is at its maximum (six zones across 13 h rarely all overlap)
    count = []
    for h in range(24):
        n = 0
        for r in rows:
            lh = (h + r["offset"]) % 24
            n += AWAKE[0] <= lh < AWAKE[1]
        count.append(n)
    best = max(count) if count else 0
    good = [c == best for c in count]
    windows: List[List[int]] = []
    for h, ok in enumerate(good):
        if ok and windows and windows[-1][1] == h:
            windows[-1][1] = h + 1
        elif ok:
            windows.append([h, h + 1])
    hnow = now.hour + now.minute / 60
    open_now = next((w for w in windows if w[0] <= hnow < w[1]), None)
    nxt = next((w for w in windows if w[0] > hnow), windows[0] if windows else None)
    return {"rows": rows, "home": rows[home_i], "now": now.strftime("%H:%M"), "hnow": hnow,
            "good": good, "count": count, "best": best, "n": len(rows), "windows": windows, "open_now": open_now, "next_window": nxt,
            "date_label": now.strftime("%A, %-d %B"), "demo": bool(params.get("demo")) or not USER_FILE.exists()}
