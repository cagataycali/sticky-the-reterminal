"""
🪟 glass — information-dense e-ink components for the reTerminal Sticky.

A component is a pure Python renderer: (params, orientation) → an 800×480 or
480×800 Pillow "L" image whose pixels are ONLY the panel's four palette values
(0 / 85 / 170 / 255). It travels to the glass through the frames rail
(frames.py store, `/api/frames/<id>/0.raw`) and the firmware's `image` card
(tiny_display.cpp render_image_card — a 96000-byte memcpy onto the canvas,
no text pipeline, no status strip painted over it). No firmware flash needed.

Registry: every renderer module registers itself via `@component(...)`;
`glass_routes.py` exposes them at /api/glass/*.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from PIL import Image

LANDSCAPE = (800, 480)
PORTRAIT = (480, 800)
ORIENTATIONS = ("landscape", "portrait")

Renderer = Callable[[Dict[str, Any], str], Image.Image]


@dataclass
class Component:
    name: str
    title: str
    description: str
    render: Renderer
    params: Dict[str, str] = field(default_factory=dict)   # name → doc (incl. default)
    fetch: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None  # adapter: params → data
    native_hint: str = ""                                    # one line for NATIVE.md

    def data(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Adapter output — and the library's one promise about the network: a dead
        source never blanks the glass. If the adapter raises (offline, 5xx, bad
        JSON) we re-fetch with demo=1 (every adapter has a fixture) and mark the
        result so render() can stamp it 'offline · demo' — an honest card beats
        an HTTP 500 that leaves yesterday's frame on the desk."""
        if not self.fetch:
            return dict(params)
        try:
            return self.fetch(params)
        except Exception as e:  # noqa: BLE001 — any adapter failure degrades the same way
            if params.get("demo"):
                raise
            d = self.fetch({**params, "demo": 1})
            d["_fallback"] = f"{type(e).__name__}"
            return d


REGISTRY: Dict[str, Component] = {}


def component(name: str, title: str, description: str, params: Dict[str, str],
              fetch=None, native_hint: str = ""):
    def deco(fn: Renderer) -> Renderer:
        REGISTRY[name] = Component(name, title, description, fn, params, fetch, native_hint)
        return fn
    return deco


# renderer modules — one per component, imported for their side effect
_MODULES = ("weather_bar", "calendar_day", "calendar_month", "notifications", "now", "fleet", "sun", "week", "github", "moon", "air", "habits", "poster", "countdown", "clock", "photo", "compose", "arm", "goals", "focus", "todo", "overnight", "agenda", "reading", "sudoku", "clocks", "markets", "year", "chess", "playing", "tide", "almanac", "departures", "budget", "printer")


def load_all() -> Dict[str, Component]:
    for m in _MODULES:
        importlib.import_module(f"{__name__}.{m}")
    return REGISTRY


def components() -> List[Dict[str, Any]]:
    load_all()
    return [{"name": c.name, "title": c.title, "description": c.description,
             "params": c.params, "orientations": list(ORIENTATIONS),
             "preview": f"/api/glass/preview/{c.name}.png"}
            for c in REGISTRY.values()]


def render(name: str, params: Optional[Dict[str, Any]] = None,
           orientation: str = "landscape") -> Image.Image:
    load_all()
    if name not in REGISTRY:
        raise KeyError(f"unknown component '{name}' — have {sorted(REGISTRY)}")
    if orientation not in ORIENTATIONS:
        raise ValueError("orientation must be landscape|portrait")
    comp = REGISTRY[name]
    params = dict(params or {})
    d = comp.data(params)
    im = comp.render(d, orientation)
    want = LANDSCAPE if orientation == "landscape" else PORTRAIT
    assert im.size == want, f"{name}: rendered {im.size}, want {want}"
    if isinstance(d, dict) and d.get("_fallback"):
        _stamp_fallback(im, str(d["_fallback"]))
    return im


def _stamp_fallback(im: Image.Image, reason: str) -> None:
    """Bottom-right 'offline · demo' tag on a white pad — the degraded-state mark."""
    from PIL import ImageDraw
    from .canvas import DARK, WHITE, sans
    d = ImageDraw.Draw(im)
    d.fontmode = "1"
    f = sans(11, 600)
    label = f"offline · demo ({reason})"
    tw = int(d.textlength(label, font=f))
    x1, y1 = im.width - 10, im.height - 8
    d.rectangle((x1 - tw - 8, y1 - 16, x1, y1), fill=WHITE)
    d.text((x1 - 4, y1 - 2), label, font=f, fill=DARK, anchor="rs")
