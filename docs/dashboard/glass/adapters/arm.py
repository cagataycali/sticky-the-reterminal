"""
🦾 glass.adapters.arm — the SO-101 leader ("Fomo") as the glass sees it. READ-ONLY.

Source: params.url → STICKY_ARM_URL → http://127.0.0.1:8090/api/state (the
strands-arm dash on this machine) → fixture (a real capture, port redacted).
Output: joints (name, deg, home, q = deg − home, torque, v), pose name, bus volts,
head pan/tilt relative to home, Nicla presence (transport, rssi, ToF, roll/pitch),
guard step/speed, plus a `folded` verdict (all four arm joints within 3° of home)
and `can_lift` (bus ≥ 6.5 V — the LEADER supply is 5.5 V and cannot raise the arm).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Dict, List

import requests

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "arm_state.json"
LIFT_MIN_V = 6.5


def _load(params: Dict[str, Any]) -> Dict[str, Any]:
    if params.get("demo"):
        return {**json.loads(FIXTURE.read_text()), "_source": "demo"}
    url = str(params.get("url") or os.getenv("STICKY_ARM_URL") or "http://127.0.0.1:8090/api/state")
    try:
        r = requests.get(url, timeout=3, headers={"User-Agent": "sticky-glass/1.0"})
        r.raise_for_status()
        return {**r.json(), "_source": "live"}
    except Exception:
        return {**json.loads(FIXTURE.read_text()), "_source": "demo"}


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    s = _load(params)
    arm = s.get("arm") or {}
    joints_raw: List[Dict[str, Any]] = arm.get("joints") or []
    joints = []
    for j in joints_raw:
        home = float(j.get("home") or 0)
        deg = float(j.get("deg") or 0)
        q = deg - home
        if q > 180:
            q -= 360
        if q < -180:
            q += 360
        joints.append({"id": int(j["id"]), "name": str(j["name"]), "deg": deg, "home": home, "q": q,
                       "torque": bool(j.get("torque")), "v": float(j.get("v") or 0), "load": float(j.get("load") or 0),
                       "min": j.get("min"), "max": j.get("max")})
    by = {j["name"]: j for j in joints}
    volts = max((j["v"] for j in joints), default=0.0)
    arm_js = [by.get(n) for n in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex")]
    folded = all(j is not None and abs(j["q"]) <= 3 for j in arm_js)
    nicla = s.get("nicla") or {}
    imu = nicla.get("imu") or {}
    now = dt.datetime.now()
    return {
        "ok": bool(arm.get("ok")), "pose": str(arm.get("pose") or ("folded" if folded else "—")),
        "folded": folded, "volts": volts, "can_lift": volts >= LIFT_MIN_V, "lift_min_v": LIFT_MIN_V,
        "torque_any": any(j["torque"] for j in joints), "joints": joints,
        "q": {"pan": by.get("shoulder_pan", {}).get("q", 0.0), "lift": by.get("shoulder_lift", {}).get("q", 0.0),
              "elbow": by.get("elbow_flex", {}).get("q", 0.0), "wrist": by.get("wrist_flex", {}).get("q", 0.0),
              "hpan": by.get("wrist_roll", {}).get("q", 0.0), "tilt": by.get("tilt", {}).get("q", 0.0)},
        "head": {"ok": bool(nicla.get("ok")), "transport": str(nicla.get("transport") or "—"), "rssi": nicla.get("rssi"),
                 "tof_mm": nicla.get("tof_mm"), "roll": imu.get("roll"), "pitch": imu.get("pitch")},
        "guard": {"step": (s.get("guard") or {}).get("step_deg"), "speed": (s.get("guard") or {}).get("speed_dps")},
        "role": str((s.get("robot") or {}).get("arm_role") or "leader"), "error": arm.get("error"),
        "source": s["_source"], "now": now.strftime("%H:%M"), "date_label": now.strftime("%A, %-d %B"),
    }
