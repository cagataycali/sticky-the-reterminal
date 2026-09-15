"""
☀ glass.adapters.sun — sunrise/sunset geometry for today, from Open-Meteo daily
(sunrise, sunset, daylight_duration; past_days=1 for the delta, 3 days ahead).

params: place | lat/lon (same as weather), demo, clock="HH:MM" (tests).
Output: today's times, solar noon, day length + delta vs yesterday, golden hours
(first/last hour of daylight — the photographer's rule of thumb, not an
ephemeris), civil twilight ≈ ±30 min (approximation, labelled so), the sun's
progress 0..1 across the day arc (or the night arc when it is dark), tomorrow.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict

import requests

from .weather import UA, default_place, geocode

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sun.json"
GOLDEN_S = 3600
TWILIGHT_S = 30 * 60


def _hm(t: dt.datetime) -> str:
    return t.strftime("%H:%M")


def _dur(s: float) -> str:
    h, m = divmod(int(round(s / 60)), 60)
    return f"{h} h {m:02d} min"


def _raw(params: Dict[str, Any]) -> Dict[str, Any]:
    if params.get("demo"):
        return json.loads(FIXTURE.read_text())
    if params.get("lat") is not None and params.get("lon") is not None:
        loc = {"name": params.get("place") or f"{float(params['lat']):.2f},{float(params['lon']):.2f}",
               "lat": float(params["lat"]), "lon": float(params["lon"])}
    else:
        loc = geocode(str(params.get("place") or default_place()))
    r = requests.get("https://api.open-meteo.com/v1/forecast", headers=UA, timeout=10, params={
        "latitude": loc["lat"], "longitude": loc["lon"], "daily": "sunrise,sunset,daylight_duration",
        "timezone": "auto", "past_days": 1, "forecast_days": 3})
    r.raise_for_status()
    j = r.json()
    d = j["daily"]
    return {"place": loc["name"], "tz": j.get("timezone"),
            "days": [{"date": d["time"][i], "sunrise": d["sunrise"][i], "sunset": d["sunset"][i],
                      "daylight_s": d["daylight_duration"][i]} for i in range(len(d["time"]))]}


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    raw = _raw(params)
    days = raw["days"]
    # "today" = the second entry when past_days=1 gave us yesterday first
    today_i = 1 if len(days) > 1 else 0
    if params.get("demo"):
        now = dt.datetime.fromisoformat(days[today_i]["date"] + "T12:00")
    else:
        try:
            from zoneinfo import ZoneInfo
            now = dt.datetime.now(ZoneInfo(raw["tz"])).replace(tzinfo=None) if raw.get("tz") else dt.datetime.now()
        except Exception:
            now = dt.datetime.now()
        today_i = next((i for i, d in enumerate(days) if d["date"] == now.date().isoformat()), today_i)
    if params.get("clock"):
        hh, mm = str(params["clock"]).split(":")
        now = now.replace(hour=int(hh), minute=int(mm))
    t, y, tm = days[today_i], days[today_i - 1] if today_i else None, days[today_i + 1] if today_i + 1 < len(days) else None
    rise, set_ = dt.datetime.fromisoformat(t["sunrise"]), dt.datetime.fromisoformat(t["sunset"])
    noon = rise + (set_ - rise) / 2
    day_len = float(t["daylight_s"])
    delta = (day_len - float(y["daylight_s"])) if y else None
    is_day = rise <= now < set_
    if is_day:
        progress = (now - rise).total_seconds() / max(1.0, (set_ - rise).total_seconds())
        phase = "day"
    elif now < rise:
        prev_set = dt.datetime.fromisoformat(y["sunset"]) if y else set_ - dt.timedelta(days=1)
        progress = (now - prev_set).total_seconds() / max(1.0, (rise - prev_set).total_seconds())
        phase = "night"
    else:
        next_rise = dt.datetime.fromisoformat(tm["sunrise"]) if tm else rise + dt.timedelta(days=1)
        progress = (now - set_).total_seconds() / max(1.0, (next_rise - set_).total_seconds())
        phase = "night"
    if is_day:
        nxt = ("sunset", set_ - now)
    elif now < rise:
        nxt = ("sunrise", rise - now)
    else:
        nxt = ("sunrise", (dt.datetime.fromisoformat(tm["sunrise"]) if tm else rise + dt.timedelta(days=1)) - now)
    return {
        "place": raw.get("place") or "", "date": t["date"], "now": _hm(now), "phase": phase,
        "progress": max(0.0, min(1.0, progress)),
        "sunrise": _hm(rise), "sunset": _hm(set_), "noon": _hm(noon),
        "day_len_s": day_len, "day_len": _dur(day_len),
        "delta_s": delta, "delta": (f"{'+' if delta >= 0 else '−'}{abs(int(round(delta / 60)))} min vs yesterday" if delta is not None else ""),
        "golden_am": (_hm(rise), _hm(rise + dt.timedelta(seconds=GOLDEN_S))),
        "golden_pm": (_hm(set_ - dt.timedelta(seconds=GOLDEN_S)), _hm(set_)),
        "twilight": (_hm(rise - dt.timedelta(seconds=TWILIGHT_S)), _hm(set_ + dt.timedelta(seconds=TWILIGHT_S))),
        "next": {"what": nxt[0], "in_s": max(0, int(nxt[1].total_seconds()))},
        "tomorrow": ({"sunrise": tm["sunrise"][11:16], "sunset": tm["sunset"][11:16], "daylight_s": tm["daylight_s"]} if tm else None),
        "rise_frac": rise.hour / 24 + rise.minute / 1440, "set_frac": set_.hour / 24 + set_.minute / 1440,
        "now_frac": now.hour / 24 + now.minute / 1440,
    }
