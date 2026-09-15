"""
≈ glass.adapters.tide — the water at a NOAA station, 24 hours of it.

Source: NOAA CO-OPS predictions (no key): a 30-minute curve and the high/low list for 48 h
from local midnight, plus the station's name from the metadata API → fixture
(glass/fixtures/tide.json, The Battery NY, real predictions saved 2026-09-07).
params: station (NOAA id, default 8518750 The Battery), at ("YYYY-MM-DD HH:MM" pin in the
station's local time), units (metric|english), demo.

Output: station {id, name, state}, at_label, window {start, end} (now−6 h .. now+18 h),
points [{t: hours from window start, v}], now {t, v}, trend ("rising"|"falling"), rate_m_h,
next_high/next_low {time_label, v, in_min}, extremes in window [{t, v, type}], range_m
(today's high−low), unit ("m"|"ft"), demo.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "tide.json"
UA = {"User-Agent": "sticky-glass/1.0"}
API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
META = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{sid}.json"


def _pull(station: str, day: dt.date, units: str) -> Dict[str, Any]:
    q = {"product": "predictions", "station": station, "datum": "MLLW", "units": units, "time_zone": "lst_ldt",
         "format": "json", "begin_date": day.strftime("%Y%m%d"), "range": "48"}
    curve = requests.get(API, params=dict(q, interval="30"), headers=UA, timeout=10).json()["predictions"]
    hilo = requests.get(API, params=dict(q, interval="hilo"), headers=UA, timeout=10).json()["predictions"]
    name, state = station, ""
    try:
        st = requests.get(META.format(sid=station), headers=UA, timeout=8).json()["stations"][0]
        name, state = st.get("name") or station, st.get("state") or ""
    except Exception:
        pass
    return {"station": station, "name": name, "state": state, "curve": curve, "hilo": hilo}


def _t(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%d %H:%M")


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    station = str(params.get("station") or "8518750")
    units = "english" if str(params.get("units", "")).lower() in ("english", "ft", "imperial") else "metric"
    now = _t(str(params["at"])) if params.get("at") else dt.datetime.now().replace(second=0, microsecond=0)
    src: Optional[Dict[str, Any]] = None
    if not params.get("demo"):
        try:
            src = _pull(station, now.date() - dt.timedelta(days=1) if now.hour < 6 else now.date(), units)
        except Exception:
            src = None
    demo = src is None
    if demo:
        src = json.loads(FIXTURE.read_text())
        units = "metric"
        if not params.get("at"):
            # pin the demo clock inside the saved day so the curve and the marks always agree
            now = _t(src["curve"][0]["t"]) + dt.timedelta(hours=15, minutes=12)
    unit = "m" if units == "metric" else "ft"
    curve = [(_t(p["t"]), float(p["v"])) for p in src["curve"]]
    hilo = [(_t(p["t"]), float(p["v"]), p["type"]) for p in src["hilo"]]
    start, end = now - dt.timedelta(hours=6), now + dt.timedelta(hours=18)
    pts = [{"t": (t - start).total_seconds() / 3600, "v": v} for t, v in curve if start <= t <= end]
    # current height: linear interpolation on the 30-min curve
    before = [(t, v) for t, v in curve if t <= now]
    after = [(t, v) for t, v in curve if t > now]
    if before and after:
        (t0, v0), (t1, v1) = before[-1], after[0]
        f = (now - t0).total_seconds() / max(1, (t1 - t0).total_seconds())
        v_now = v0 + (v1 - v0) * f
        rate = (v1 - v0) / max(1e-6, (t1 - t0).total_seconds() / 3600)
    else:
        v_now, rate = (before or after)[-1][1], 0.0
    trend = "rising" if rate >= 0 else "falling"
    ahead = [(t, v, k) for t, v, k in hilo if t > now]
    nh = next(((t, v) for t, v, k in ahead if k == "H"), None)
    nl = next(((t, v) for t, v, k in ahead if k == "L"), None)

    def nxt(x):
        if not x:
            return None
        t, v = x
        return {"time_label": t.strftime("%H:%M"), "v": v, "in_min": int((t - now).total_seconds() // 60),
                "day": "today" if t.date() == now.date() else "tomorrow"}

    extremes = [{"t": (t - start).total_seconds() / 3600, "v": v, "type": k, "time_label": t.strftime("%H:%M")}
                for t, v, k in hilo if start <= t <= end]
    today = [v for t, v, k in hilo if t.date() == now.date()]
    rng = (max(today) - min(today)) if len(today) >= 2 else (max(v for _, v in curve) - min(v for _, v in curve))
    return {"station": {"id": src["station"], "name": src.get("name") or src["station"], "state": src.get("state", "")},
            "at_label": now.strftime("%A, %-d %B · %H:%M"), "window": {"start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")},
            "start_hour": start.hour + start.minute / 60, "points": pts, "now": {"t": 6.0, "v": v_now}, "trend": trend,
            "rate_m_h": rate, "next_high": nxt(nh), "next_low": nxt(nl), "extremes": extremes, "range": rng,
            "unit": unit, "vmin": min(v for _, v in curve), "vmax": max(v for _, v in curve), "demo": demo}
