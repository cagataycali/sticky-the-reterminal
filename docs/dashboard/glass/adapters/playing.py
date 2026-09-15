"""
♪ glass.adapters.playing — what the desk is listening to.

Source: the Spotify desktop app on the machine running the dashboard (AppleScript,
read-only: state, track, artist, album, artwork url, duration, position, shuffle,
repeat, volume) → params.track/artist/album/... (anything can push a track) → fixture.
Artwork is fetched once per url into ~/.cache/sticky-glass/art/ (8 s timeout); the
fixture uses glass/fixtures/photo.jpg as its sleeve so no copyrighted cover ships in git.

Output: state (playing|paused|stopped|demo), track, artist, album, art (path or None),
duration_s, position_s, progress 0..1, shuffle, repeat ("off"|"context"|"track"),
volume 0..100, source ("Spotify on <host>" | "pushed" | "demo"), at (HH:MM).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import platform
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

import requests

FIXTURE_ART = Path(__file__).resolve().parent.parent / "fixtures" / "photo.jpg"
CACHE = Path.home() / ".cache" / "sticky-glass" / "art"
UA = {"User-Agent": "sticky-glass/1.0"}

_SCRIPT = '''
tell application "Spotify"
  if it is running then
    set ps to (player state as string)
    set t to current track
    return ps & tab & (name of t) & tab & (artist of t) & tab & (album of t) & tab & (artwork url of t) & tab & ((duration of t) as string) & tab & ((player position) as string) & tab & ((shuffling) as string) & tab & ((repeating) as string) & tab & ((sound volume) as string)
  end if
  return ""
end tell
'''

FIXTURE = {"state": "playing", "track": "Petrichor", "artist": "The Quiet Desk", "album": "Rain on the Roof, Vol. 2",
           "duration_s": 248, "position_s": 107, "shuffle": False, "repeat": "off", "volume": 62}


def _spotify() -> Optional[Dict[str, Any]]:
    if platform.system() != "Darwin":
        return None
    try:
        out = subprocess.run(["osascript", "-e", _SCRIPT], capture_output=True, text=True, timeout=4).stdout.strip()
    except Exception:
        return None
    if not out:
        return None
    parts = out.split("\t")
    if len(parts) < 10:
        return None
    st, name, artist, album, art_url, dur_ms, pos, shuf, rep, vol = parts[:10]
    return {"state": st, "track": name, "artist": artist, "album": album, "art_url": art_url,
            "duration_s": int(float(dur_ms) / 1000), "position_s": int(float(pos)), "shuffle": shuf == "true",
            "repeat": "context" if rep == "true" else "off", "volume": int(float(vol)),
            "source": f"Spotify on {platform.node().split('.')[0]}"}


def _art(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".jpg")
    if p.exists():
        return str(p)
    try:
        r = requests.get(url, headers=UA, timeout=8)
        r.raise_for_status()
        p.write_bytes(r.content)
        return str(p)
    except Exception:
        return None


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    now = dt.datetime.now()
    d: Optional[Dict[str, Any]] = None
    if params.get("track"):
        d = {"state": str(params.get("state", "playing")), "track": str(params["track"]), "artist": str(params.get("artist", "")),
             "album": str(params.get("album", "")), "art_url": params.get("art_url"), "duration_s": int(params.get("duration_s", 0) or 0),
             "position_s": int(params.get("position_s", 0) or 0), "shuffle": bool(params.get("shuffle")), "repeat": str(params.get("repeat", "off")),
             "volume": int(params.get("volume", 0) or 0), "source": "pushed"}
    elif not params.get("demo"):
        d = _spotify()
    if d is None:
        d = dict(FIXTURE, source="demo", art_url=None)
        art: Optional[str] = str(FIXTURE_ART)
    else:
        art = _art(d.get("art_url")) or (str(params["art_path"]) if params.get("art_path") else None)
    dur, pos = d["duration_s"], min(d["position_s"], d["duration_s"] or d["position_s"])
    d.update({"art": art, "progress": (pos / dur) if dur else 0.0, "position_s": pos, "at": now.strftime("%H:%M"),
              "demo": d["source"] == "demo"})
    d.pop("art_url", None)
    return d
