"""
🌬 glass.adapters.air — air quality, UV and pollen from Open-Meteo (no key).

current: us_aqi, european_aqi, pm10, pm2_5, ozone, nitrogen_dioxide,
sulphur_dioxide, carbon_monoxide, uv_index, six pollens (Europe only — None
elsewhere, the card says so). hourly today: us_aqi, uv_index.
Output adds: AQI category (US EPA bands), pollutants with the WHO 2021
guideline each is compared against (24-h means for PM/NO2/SO2, 8-h for O3; CO 24-h). Plain ASCII names: Inter has no subscript digits at 15 px
and the ratio; UV peak + hour, "protect" window (UV ≥ 3), UV category.
params: place | lat/lon (weather's geocoder), demo.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .weather import UA, default_place, geocode

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "air.json"
CURRENT = ("us_aqi,european_aqi,pm10,pm2_5,ozone,nitrogen_dioxide,sulphur_dioxide,carbon_monoxide,"
           "uv_index,grass_pollen,birch_pollen,ragweed_pollen,alder_pollen,mugwort_pollen,olive_pollen")
AQI_BANDS = [(50, "Good"), (100, "Moderate"), (150, "Unhealthy for sensitive groups"),
             (200, "Unhealthy"), (300, "Very unhealthy"), (500, "Hazardous")]
# WHO 2021 air quality guidelines, µg/m³ (CO in mg/m³ → Open-Meteo gives µg/m³, 4 mg = 4000)
WHO = [("pm2_5", "PM2.5", 15.0, "24 h"), ("pm10", "PM10", 45.0, "24 h"), ("nitrogen_dioxide", "NO2", 25.0, "24 h"),
       ("ozone", "O3", 100.0, "8 h"), ("sulphur_dioxide", "SO2", 40.0, "24 h"), ("carbon_monoxide", "CO", 4000.0, "24 h")]
POLLEN = [("grass_pollen", "grass"), ("birch_pollen", "birch"), ("ragweed_pollen", "ragweed"),
          ("alder_pollen", "alder"), ("mugwort_pollen", "mugwort"), ("olive_pollen", "olive")]


def aqi_category(v: float) -> str:
    for hi, name in AQI_BANDS:
        if v <= hi:
            return name
    return "Hazardous"


def uv_category(v: float) -> str:
    return "Low" if v < 3 else "Moderate" if v < 6 else "High" if v < 8 else "Very high" if v < 11 else "Extreme"


def _raw(params: Dict[str, Any]) -> Dict[str, Any]:
    if params.get("demo"):
        return json.loads(FIXTURE.read_text())
    if params.get("lat") is not None and params.get("lon") is not None:
        loc = {"name": params.get("place") or f"{float(params['lat']):.2f},{float(params['lon']):.2f}",
               "lat": float(params["lat"]), "lon": float(params["lon"])}
    else:
        loc = geocode(str(params.get("place") or default_place()))
    r = requests.get("https://air-quality-api.open-meteo.com/v1/air-quality", headers=UA, timeout=10, params={
        "latitude": loc["lat"], "longitude": loc["lon"], "current": CURRENT,
        "hourly": "us_aqi,uv_index", "timezone": "auto", "forecast_days": 1})
    r.raise_for_status()
    j = r.json()
    return {"place": loc["name"], "time": j["current"]["time"], "current": j["current"], "hourly": j["hourly"]}


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    raw = _raw(params)
    c = raw["current"]
    aqi = float(c.get("us_aqi") or 0)
    now = dt.datetime.fromisoformat(raw["time"])
    hours: List[str] = raw["hourly"]["time"]
    uvs = [float(v or 0) for v in raw["hourly"]["uv_index"]]
    aqis = [float(v or 0) for v in raw["hourly"]["us_aqi"]]
    peak_i = max(range(len(uvs)), key=lambda i: uvs[i]) if uvs else 0
    protect = [i for i, v in enumerate(uvs) if v >= 3]
    pollutants = []
    for key, name, guide, window in WHO:
        v = c.get(key)
        if v is None:
            continue
        pollutants.append({"key": key, "name": name, "value": float(v), "guideline": guide, "window": window,
                           "ratio": float(v) / guide, "unit": "mg/m3" if key == "carbon_monoxide" else "ug/m3",
                           "display": f"{float(v) / 1000:.1f}" if key == "carbon_monoxide" else f"{float(v):.0f}" if float(v) >= 10 else f"{float(v):.1f}"})
    pollen = [{"name": n, "value": float(c[k])} for k, n in POLLEN if c.get(k) is not None]
    return {
        "place": raw["place"], "now": now.strftime("%H:%M"), "date_label": now.strftime("%A, %-d %B"),
        "aqi": aqi, "aqi_label": aqi_category(aqi), "eaqi": c.get("european_aqi"),
        "aqi_hours": aqis, "aqi_range": (min(aqis), max(aqis)) if aqis else (aqi, aqi),
        "uv": float(c.get("uv_index") or 0), "uv_label": uv_category(float(c.get("uv_index") or 0)),
        "uv_hours": uvs, "uv_peak": uvs[peak_i] if uvs else 0.0, "uv_peak_at": hours[peak_i][11:16] if hours else "",
        "protect": (hours[protect[0]][11:16], hours[protect[-1]][11:16]) if protect else None,
        "hour_now": now.hour, "pollutants": pollutants, "pollen": pollen,
        "pollen_note": None if pollen else "pollen: no model for this region",
    }
