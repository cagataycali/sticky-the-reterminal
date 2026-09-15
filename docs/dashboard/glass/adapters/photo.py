"""
📷 glass.adapters.photo — one picture, made honest for four grays.

Source: params.url (https only, ≤ 8 MB, 10 s) → params.path (a file under docs/
or the dashboard's frames dir only — never an arbitrary path) → fixture
(Ansel Adams, "The Tetons and the Snake River", 1942, public domain — US
National Archives via Wikimedia Commons).
Processing: EXIF-transpose, grayscale, 1 % autocontrast, then the renderer
crops/fits and Floyd–Steinberg dithers to the panel's four levels.
params: caption, by, mode (cover|frame), focus (0..1, 0..1 crop anchor).
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict

import requests
from PIL import Image, ImageOps

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "photo.jpg"
DOCS_ROOT = Path(__file__).resolve().parents[3]          # docs/
FRAMES_ROOT = Path(__file__).resolve().parents[2] / ".frames"
FIXTURE_META = {"caption": "The Tetons and the Snake River", "by": "Ansel Adams, 1942 · public domain"}
UA = {"User-Agent": "sticky-glass/1.0 (https://github.com/cagataycali/sticky-the-reterminal)"}
MAX_BYTES = 8 * 1024 * 1024


def _load(params: Dict[str, Any]) -> Image.Image:
    if params.get("url"):
        url = str(params["url"])
        if not url.startswith("https://"):
            raise ValueError("photo: url must be https")
        r = requests.get(url, headers=UA, timeout=10, stream=True)
        r.raise_for_status()
        buf = io.BytesIO()
        for chunk in r.iter_content(64 * 1024):
            buf.write(chunk)
            if buf.tell() > MAX_BYTES:
                raise ValueError("photo: file over 8 MB")
        return Image.open(io.BytesIO(buf.getvalue()))
    if params.get("path"):
        p = Path(str(params["path"]))
        cands = [p] if p.is_absolute() else [DOCS_ROOT / p, FRAMES_ROOT / p]
        for c in cands:
            rc = c.resolve()
            if rc.exists() and (str(rc).startswith(str(DOCS_ROOT)) or str(rc).startswith(str(FRAMES_ROOT))):
                return Image.open(rc)
        raise ValueError("photo: path must live under docs/ or the frames dir")
    return Image.open(FIXTURE)


def fetch(params: Dict[str, Any]) -> Dict[str, Any]:
    im = _load(params)
    im.load()
    im = ImageOps.exif_transpose(im) or im
    if im.mode in ("RGBA", "LA", "PA"):
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im.convert("RGBA"))
        im = bg
    gray = ImageOps.autocontrast(im.convert("L"), cutoff=1)
    fixture = not (params.get("url") or params.get("path"))
    return {"image": gray, "w": gray.width, "h": gray.height,
            "caption": str(params.get("caption") or (FIXTURE_META["caption"] if fixture else "")),
            "by": str(params.get("by") or (FIXTURE_META["by"] if fixture else "")),
            "mode": str(params.get("mode") or "cover"),
            "focus": tuple(float(v) for v in str(params.get("focus") or "0.5,0.5").split(","))[:2],
            "source": "url" if params.get("url") else "path" if params.get("path") else "fixture"}
