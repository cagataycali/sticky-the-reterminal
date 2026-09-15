"""
🪟 glass_routes — /api/glass/*: server-rendered e-ink components on the glass.

  GET  /api/glass/components                 the registry (name, params, preview URL)
  GET  /api/glass/preview/<name>.png?…       render now → PNG (params as query;
                                             orientation=landscape|portrait)
  POST /api/glass/show {component, device?, params?, orientation?}
       → renders, publishes ONE frame through the frames rail
         (frames.py store, capability id = sha256(bytes)[:16]),
         sends {"type":"image","url":"https://<public>/api/frames/<id>/0.raw"}
         to the device via the dashboard's own invoke path, returns a receipt.

The device fetches the .raw itself over https (tiny_display.cpp
tiny_display_fetch_raw: https only, Content-Length must be exactly 96000 or
48000) — so the public origin (STICKY_PUBLIC_URL, default
https://sticky.cagatay.my) is what goes in the card, never localhost.

Everything glass-related lives here; server.py carries exactly one include line.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

import frames as sticky_frames
from dither import _pack_4gray, _preview_4gray, BYTES_4GRAY

import glass

glass_router = APIRouter(prefix="/api/glass", tags=["glass"])

PUBLIC_URL = os.getenv("STICKY_PUBLIC_URL", "https://sticky.cagatay.my").rstrip("/")


def _coerce(params: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in params.items():
        if isinstance(v, str):
            if v.lower() in ("1", "true", "yes", "on"):
                out[k] = True if k in ("demo",) else v
                continue
            try:
                out[k] = float(v) if "." in v else int(v)
                continue
            except ValueError:
                pass
        out[k] = v
    return out


def publish_frame(im) -> Dict[str, Any]:
    """One rendered image → frames-rail sequence with a single frame. Portrait
    compositions are rotated 90° CW into the panel-native raster exactly like
    dither.py does for image uploads (same convention GALLERY verified)."""
    composed = im.convert("L")
    if composed.size == glass.PORTRAIT:
        composed = composed.rotate(-90, expand=True)
    assert composed.size == glass.LANDSCAPE
    raw = _pack_4gray(composed)
    assert len(raw) == BYTES_4GRAY
    seq_id = hashlib.sha256(raw).hexdigest()[:16]
    out_dir: Path = sticky_frames.ANIM_DIR / seq_id
    out_dir.mkdir(exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.is_file():
        (out_dir / "0.raw").write_bytes(raw)
        _preview_4gray(raw).save(out_dir / "0.png")
        manifest = {
            "seq_id": seq_id, "anim_id": "glass", "format": "gray4", "frame_bytes": BYTES_4GRAY,
            "count": 1, "interval_ms": 1000, "duration_s": 1.0, "closed_loop": False,
            "frames": [f"/api/frames/{seq_id}/0.raw"],
            "previews": [f"/api/frames/{seq_id}/0.png"],
            "source": "glass component", "ts": time.time(),
        }
        manifest_path.write_text(json.dumps(manifest, indent=1))
    return {"seq_id": seq_id, "raw": f"/api/frames/{seq_id}/0.raw",
            "preview": f"/api/frames/{seq_id}/0.png", "bytes": len(raw),
            "url": f"{PUBLIC_URL}/api/frames/{seq_id}/0.raw"}


@glass_router.get("/components")
async def api_glass_components():
    return {"components": glass.components(),
            "show": "POST /api/glass/show {component, device?, params?, orientation?}",
            "preview": "GET /api/glass/preview/<name>.png?orientation=portrait&<params>"}


@glass_router.get("/preview/{name}.png")
async def api_glass_preview(name: str, request: Request):
    q = dict(request.query_params)
    orientation = q.pop("orientation", "landscape")
    try:
        im = await run_in_threadpool(glass.render, name, _coerce(q), orientation)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # adapter/network failure — say so, do not 500 blind
        raise HTTPException(502, f"{name}: {type(e).__name__}: {e}")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@glass_router.post("/show")
async def api_glass_show(request: Request):
    from glass.adapters import dashboard
    dash = dashboard()  # the RUNNING app (__main__ under launch_dashboard.sh), not a 2nd import
    dash._guard(request)
    body = await request.json()
    name = str(body.get("component", "")).strip()
    orientation = str(body.get("orientation", "landscape"))
    params = body.get("params") or {}
    if body.get("device") and not request.query_params.get("device"):
        dash._current_selector.set(str(body["device"]).strip())
    try:
        im = await run_in_threadpool(glass.render, name, _coerce(params), orientation)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"{name}: {type(e).__name__}: {e}")
    pub = await run_in_threadpool(publish_frame, im)
    card = {"type": "image", "card_id": f"glass-{name}", "url": pub["url"]}
    if not body.get("force"):
        w = dash.glass_etiquette.open_window()
        if w:
            raise HTTPException(409, f"glass busy — another client announced an open window: "
                                     f"\"{w['text'][:160]}\". Pass force:true to paint anyway.")
    wait_s = min(float(body.get("wait_s", 60)), 120.0)
    try:
        out = await run_in_threadpool(dash._do_invoke, "render_ui", card, wait_s)
    except dash.RelayError as e:
        raise HTTPException(e.status or 424, str(e))
    dash._log_activity("glass_show", prompt=f"{name} ({orientation})", seq_id=pub["seq_id"],
                       result=str(out.get("result", ""))[:200])
    return {"component": name, "orientation": orientation, "frame": pub, "card": card,
            "device": dash._device_name(), "invoke": out}
