"""
🖨 glass.adapters.printer — a 3D printer's job, temperatures and spools.

Source: params (job/progress/…) → ~/.tiny/sticky-printer.json → fixture (a Bambu X2D at
63 % of a TPU sleeve). Contract mirrors the fields every FDM firmware exposes (Bambu MQTT
`print` report, OctoPrint/Moonraker job + heaters): {name, state: printing|paused|idle|
finished|error, job, progress 0..1, layer, layers, remaining_min, elapsed_min, nozzle_c,
nozzle_target_c, bed_c, bed_target_c, chamber_c?, speed?, filament?, ams: [{slot, material,
color, remaining 0..1, active?}], hms: [str]}. No printer protocol is wired in the dashboard —
Bambu LAN needs the access code and a TLS MQTT session; a 40-line cron writing the file is
the honest bridge.

Output: the contract plus eta_label (clock time of finish), pct, layer_label, elapsed/remaining
labels, source, demo.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, Optional

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "printer.json"
USER_FILE = Path.home() / ".tiny" / "sticky-printer.json"
KEYS = ("name", "state", "job", "progress", "layer", "layers", "remaining_min", "elapsed_min", "nozzle_c", "nozzle_target_c",
        "bed_c", "bed_target_c", "chamber_c", "speed", "filament", "ams", "hms")


def _dur(m: Optional[int]) -> str:
    if m is None:
        return "—"
    m = int(m)
    return f"{m // 60} h {m % 60:02d}" if m >= 60 else f"{m} min"


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    src: Optional[Dict[str, Any]] = None
    source = "demo"
    if params.get("job") or params.get("state"):
        src = {k: params[k] for k in KEYS if k in params}
        if isinstance(src.get("ams"), str):
            src["ams"] = json.loads(src["ams"])
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
    now = dt.datetime.now()
    prog = max(0.0, min(1.0, float(src.get("progress") or 0)))
    rem = src.get("remaining_min")
    eta = (now + dt.timedelta(minutes=int(rem))).strftime("%H:%M") if rem is not None and src.get("state") == "printing" else None
    layer, layers = src.get("layer"), src.get("layers")
    return {**{k: src.get(k) for k in KEYS}, "ams": src.get("ams") or [], "hms": src.get("hms") or [],
            "state": str(src.get("state") or "idle"), "progress": prog, "pct": round(prog * 100),
            "layer_label": f"layer {layer} of {layers}" if layer is not None and layers else "",
            "eta_label": eta, "remaining_label": _dur(rem), "elapsed_label": _dur(src.get("elapsed_min")),
            "at": now.strftime("%H:%M"), "source": source, "demo": source == "demo"}
