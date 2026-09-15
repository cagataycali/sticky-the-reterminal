"""
📡 glass.adapters.fleet — the owner's whole device fleet, grouped for the glass.

live (default): server._fleet() — the dashboard's 15 s-cached account device list
(EVERY device, not only Stickies) + per-Sticky fw/battery from the last parsed
status (_last_status / _last_fw). demo: glass/fixtures/fleet.json.

Groups (by platform): glass (esp32s3) · sensors (nicla-*) · computers (cli) ·
phones (ios/android) · endpoints. Each row: name, platform, age_s (None = never),
online (< online_window_s), fw, battery_pct, charging, caps (count).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "fleet.json"
GROUPS = [("glass", "Glass"), ("sensors", "Sensors"), ("computers", "Computers"),
          ("phones", "Phones"), ("endpoints", "Endpoints")]


def group_of(platform: str, kind: str) -> str:
    p, k = (platform or "").lower(), (kind or "").lower()
    if p.startswith("esp32"):
        return "glass"
    if p.startswith("nicla"):
        return "sensors"
    if k == "endpoint":
        return "endpoints"
    if p.startswith(("ios", "android")):
        return "phones"
    return "computers"


def _caps_count(v: Any) -> Optional[int]:
    if isinstance(v, int):
        return v
    if isinstance(v, list):
        return len(v)
    if isinstance(v, str):
        try:
            return len(json.loads(v))
        except ValueError:
            return None
    return None


def _live(now: float) -> tuple[List[Dict[str, Any]], float]:
    from . import dashboard
    server = dashboard()  # the RUNNING app (__main__), never a second import
    rows = server._fleet()
    window = float(getattr(server, "ONLINE_WINDOW_S", 90.0))
    status_all = server._last_status.all()
    fw_all = server._last_fw.all()
    out = []
    for r in rows:
        did = str(r.get("id"))
        seen = r.get("last_seen")
        age = (now - float(seen)) if isinstance(seen, (int, float)) and seen > 0 else None
        st = (status_all.get(did) or {}).get("status") or {}
        out.append({"name": r.get("name"), "platform": r.get("platform"), "kind": r.get("kind"),
                    "age_s": age, "fw": (fw_all.get(did) or {}).get("fw") or st.get("fw"),
                    "battery_pct": st.get("battery_pct"), "charging": st.get("charging"),
                    "caps": _caps_count(r.get("capabilities"))})
    return out, window


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    window = float(params.get("online_window_s") or 90.0)
    if params.get("demo"):
        rows = json.loads(FIXTURE.read_text())["devices"]
    else:
        try:
            rows, window = _live(now)
        except Exception:
            rows = json.loads(FIXTURE.read_text())["devices"]
    devs = []
    for r in rows:
        age = r.get("age_s")
        devs.append({**r, "age_s": None if age is None else float(age),
                     "online": age is not None and float(age) < window,
                     "group": group_of(str(r.get("platform") or ""), str(r.get("kind") or ""))})
    devs.sort(key=lambda d: (not d["online"], d["age_s"] if d["age_s"] is not None else 1e12, str(d["name"]).lower()))
    groups = []
    for key, label in GROUPS:
        members = [d for d in devs if d["group"] == key]
        if members:
            groups.append({"key": key, "label": label, "devices": members,
                           "online": sum(1 for d in members if d["online"])})
    return {"devices": devs, "groups": groups, "online": sum(1 for d in devs if d["online"]),
            "total": len(devs), "now": time.strftime("%H:%M", time.localtime(now)), "window_s": window}
