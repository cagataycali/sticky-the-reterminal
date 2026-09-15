"""
🌤 glass.adapters.weather — Open-Meteo (no key) → a flat weather dict.

params:
  place   city name (default: the city of this machine's IANA timezone —
          America/New_York → "New York"; NOT an address, NEED filed for the
          owner's real place)
  lat/lon explicit coordinates (skip geocoding)
  units   "f" (default) | "c"
  days    forecast days (default 6, max 16; `week` asks for 7)
  demo    truthy → the fixture, no network (tests, previews offline)
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Dict

import requests

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "weather.json"
UA = {"User-Agent": "sticky-glass/1.0 (+https://sticky.cagatay.my)"}

# WMO weather interpretation codes → (short label, glyph name)
WMO = {
    0: ("Clear", "sun"), 1: ("Mostly clear", "sun"), 2: ("Partly cloudy", "sun_cloud"),
    3: ("Overcast", "cloud"), 45: ("Fog", "fog"), 48: ("Rime fog", "fog"),
    51: ("Light drizzle", "drizzle"), 53: ("Drizzle", "drizzle"), 55: ("Heavy drizzle", "rain"),
    56: ("Freezing drizzle", "sleet"), 57: ("Freezing drizzle", "sleet"),
    61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "sleet"), 67: ("Freezing rain", "sleet"),
    71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"), 77: ("Snow grains", "snow"),
    80: ("Showers", "rain"), 81: ("Showers", "rain"), 82: ("Violent showers", "rain"),
    85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "storm"), 96: ("Thunderstorm, hail", "storm"), 99: ("Thunderstorm, hail", "storm"),
}


def describe(code: int, is_day: bool = True) -> tuple[str, str]:
    label, glyph = WMO.get(int(code), ("—", "cloud"))
    if not is_day and glyph == "sun":
        glyph = "moon"
    elif not is_day and glyph == "sun_cloud":
        glyph = "moon_cloud"
    return label, glyph


def default_place() -> str:
    tz = os.getenv("TZ") or ""
    if not tz:
        try:
            tz = os.readlink("/etc/localtime").split("zoneinfo/")[-1]
        except OSError:
            tz = ""
    city = tz.split("/")[-1].replace("_", " ") if "/" in tz else ""
    return city or "London"


def geocode(place: str) -> Dict[str, Any]:
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": place, "count": 1, "format": "json"}, headers=UA, timeout=8)
    r.raise_for_status()
    hits = r.json().get("results") or []
    if not hits:
        raise ValueError(f"no place matched '{place}'")
    h = hits[0]
    return {"name": h["name"], "admin": h.get("admin1", ""), "country": h.get("country_code", ""),
            "lat": h["latitude"], "lon": h["longitude"], "tz": h.get("timezone", "auto")}


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    if params.get("demo"):
        return json.loads(FIXTURE.read_text())
    units = str(params.get("units", "f")).lower()
    unit_word = "fahrenheit" if units == "f" else "celsius"
    if params.get("lat") is not None and params.get("lon") is not None:
        loc = {"name": params.get("place") or f"{float(params['lat']):.2f},{float(params['lon']):.2f}",
               "lat": float(params["lat"]), "lon": float(params["lon"]), "tz": "auto", "admin": "", "country": ""}
    else:
        loc = geocode(str(params.get("place") or default_place()))
    r = requests.get("https://api.open-meteo.com/v1/forecast", headers=UA, timeout=10, params={
        "latitude": loc["lat"], "longitude": loc["lon"],
        "current": "temperature_2m,apparent_temperature,weather_code,relative_humidity_2m,wind_speed_10m,is_day",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
        "temperature_unit": unit_word, "wind_speed_unit": "mph" if units == "f" else "kmh",
        "timezone": "auto", "forecast_days": max(1, min(16, int(params.get("days") or 6))),
    })
    r.raise_for_status()
    return normalize(r.json(), loc, units)


def normalize(j: Dict[str, Any], loc: Dict[str, Any], units: str) -> Dict[str, Any]:
    cur = j["current"]
    now = dt.datetime.fromisoformat(cur["time"])
    hrs = j["hourly"]
    # next 12 hours from the current hour
    idx = next((i for i, t in enumerate(hrs["time"]) if dt.datetime.fromisoformat(t) >= now.replace(minute=0)), 0)
    hours = []
    for i in range(idx, min(idx + 12, len(hrs["time"]))):
        hours.append({"t": hrs["time"][i][11:16], "temp": hrs["temperature_2m"][i],
                      "precip": hrs["precipitation_probability"][i], "code": hrs["weather_code"][i]})
    days = []
    dly = j["daily"]
    for i in range(len(dly["time"])):
        d = dt.date.fromisoformat(dly["time"][i])
        days.append({"date": dly["time"][i], "dow": "Today" if i == 0 else d.strftime("%a"),
                     "hi": dly["temperature_2m_max"][i], "lo": dly["temperature_2m_min"][i],
                     "precip": dly["precipitation_probability_max"][i], "code": dly["weather_code"][i]})
    label, glyph = describe(cur["weather_code"], bool(cur.get("is_day", 1)))
    return {
        "place": loc["name"], "admin": loc.get("admin", ""), "country": loc.get("country", ""),
        "tz": j.get("timezone", loc.get("tz")), "time": cur["time"], "units": units,
        "temp": cur["temperature_2m"], "feels": cur.get("apparent_temperature"),
        "humidity": cur.get("relative_humidity_2m"), "wind": cur.get("wind_speed_10m"),
        "wind_unit": "mph" if units == "f" else "km/h",
        "code": cur["weather_code"], "is_day": bool(cur.get("is_day", 1)),
        "condition": label, "glyph": glyph,
        "hi": days[0]["hi"], "lo": days[0]["lo"], "precip": days[0]["precip"],
        "sunrise": dly["sunrise"][0][11:16], "sunset": dly["sunset"][0][11:16],
        "hours": hours, "days": days,
    }
