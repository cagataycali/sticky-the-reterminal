"""
🌙 glass.adapters.moon — the moon's phase from arithmetic, no network.

Mean synodic month (29.530588853 d) from the 2000-01-06 18:14 UTC new moon;
accurate to ~±0.5 day against an ephemeris (the true moon runs early/late by up
to 14 h) — plenty for a glass at arm's length, and honest about it in the
header. params: date (YYYY-MM-DD, tests), time (HH:MM), tz.
Output: fraction (0 new → 0.5 full → 1 new), illumination 0..1, age days, phase
name, waxing, next four principal phases with dates + days away, and a 7-day
strip of fractions for the mini-moons.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, List

SYNODIC = 29.530588853
EPOCH = dt.datetime(2000, 1, 6, 18, 14, tzinfo=dt.timezone.utc)
NAMES = ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous",
         "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"]
PRINCIPAL = [(0.0, "New moon"), (0.25, "First quarter"), (0.5, "Full moon"), (0.75, "Last quarter")]


def fraction_at(t: dt.datetime) -> float:
    days = (t - EPOCH).total_seconds() / 86400
    return (days / SYNODIC) % 1.0


def phase_name(f: float) -> str:
    # principal phases own a ±1.5-day band, the rest are the four intermediates
    band = 1.5 / SYNODIC
    for p, n in PRINCIPAL:
        if abs(((f - p + 0.5) % 1.0) - 0.5) <= band:
            return n
    return NAMES[int(f * 8 + 0.5) % 8]


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    if params.get("date"):
        hh, mm = (str(params.get("time") or "21:00").split(":") + ["0"])[:2]
        now = dt.datetime.fromisoformat(str(params["date"])).replace(hour=int(hh), minute=int(mm), tzinfo=dt.timezone.utc)
    else:
        now = dt.datetime.now(dt.timezone.utc)
    f = fraction_at(now)
    illum = (1 - math.cos(2 * math.pi * f)) / 2
    age = f * SYNODIC
    upcoming: List[Dict[str, Any]] = []
    for p, n in PRINCIPAL:
        ahead = ((p - f) % 1.0) * SYNODIC
        if ahead < 0.25:            # it is happening now → the next one is a month out
            ahead += SYNODIC
        when = now + dt.timedelta(days=ahead)
        upcoming.append({"name": n, "date": when.date().isoformat(), "label": when.strftime("%a %-d %b"),
                         "in_days": ahead, "in": ("today" if ahead < 1 else f"in {int(round(ahead))} d")})
    upcoming.sort(key=lambda u: u["in_days"])
    strip = []
    for k in range(1, 8):
        d = now + dt.timedelta(days=k)
        strip.append({"dow": d.strftime("%a")[:2], "fraction": fraction_at(d)})
    local = dt.datetime.now()
    return {"fraction": f, "illumination": illum, "age_days": age, "name": phase_name(f),
            "waxing": f < 0.5, "upcoming": upcoming, "strip": strip,
            "date_label": now.strftime("%A, %-d %B"), "now": (params.get("time") or local.strftime("%H:%M")),
            "note": "mean-cycle arithmetic, ±½ day"}
