"""
Generate glass/README.md (the library index) and glass/NATIVE.md (what each
component would cost as a firmware-native card) from the live registry — so the
docs cannot drift from the code. Run:  python -m glass.index_md
test_glass.py asserts both files are current.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List

from . import REGISTRY, load_all

HERE = Path(__file__).resolve().parent
PREVIEWS = HERE / "previews"
RECEIPTS = HERE / "receipts"

INTRO = """# glass — e-ink components for the Sticky

Server-rendered cards for an 800×480 four-gray panel. A component is a Python
renderer (`@component`) fed by an adapter with a fixture fallback; the dashboard
renders it to a frame and the firmware's `image` card blits the 96 000-byte raw
(`POST /api/glass/show {component, params, device, orientation}`). No firmware
change, no flash: what you see below is what the glass showed — every
`receipts/*.png` is a real framebuffer capture that diffed 0 px against the frame.

Grammar the whole library obeys: white paper, black ink, two grays for hierarchy
(DARK for secondary text, LIGHT for rules); Inter for words, JetBrains Mono for
numbers; text drawn without anti-aliasing so every pixel is a palette value;
hairlines instead of boxes; one hero per card; nothing animates, nothing pretends
to be live (no second hands, no spinners). Read-only data, fixtures for tests.

```
GET  /api/glass/components            registry (name, params, preview url)
GET  /api/glass/preview/<name>.png    render with fixtures, landscape
POST /api/glass/show                  render + ship to a device (WebAuthn)
```

![every component, landscape, fixtures](previews/CONTACT.png)

"""

NATIVE_INTRO = """# NATIVE.md — what each component would cost inside the firmware

The server-rendered path ships 96 000 bytes per card. A native card ships its
*data* and lets the ESP32-S3 draw — these are the one-line estimates recorded
with each component (`native_hint`), for a future native firmware path. Sizes are
payload guesses, not measurements.

| component | native form |
|---|---|
"""


def _img(name: str) -> str:
    parts: List[str] = []
    if (PREVIEWS / f"{name}.png").exists():
        parts.append(f'<img src="previews/{name}.png" width="400" alt="{name} landscape">')
    if (PREVIEWS / f"{name}_portrait.png").exists():
        parts.append(f'<img src="previews/{name}_portrait.png" width="120" alt="{name} portrait">')
    for extra in sorted(PREVIEWS.glob(f"{name}_*.png")):
        if extra.stem != f"{name}_portrait" and extra.stem.upper() != extra.stem:
            parts.append(f'<img src="previews/{extra.name}" width="200" alt="{extra.stem}">')
    return "\n".join(parts)


def build_readme() -> str:
    load_all()
    out = [INTRO, "## Components\n", "| component | what it answers | params |", "|---|---|---|"]
    for c in REGISTRY.values():
        keys = ", ".join(f"`{k}`" for k in c.params)
        out.append(f"| [`{c.name}`](#{c.name}) — {c.title} | {c.description} | {keys} |")
    out.append("")
    for c in REGISTRY.values():
        out.append(f"### {c.name}\n")
        out.append(f"**{c.title}.** {c.description}\n")
        if c.params:
            out.append("| param | meaning |\n|---|---|")
            for k, v in c.params.items():
                out.append(f"| `{k}` | {v or '—'} |")
            out.append("")
        out.append(_img(c.name) + "\n")
        if (RECEIPTS / f"{c.name}.png").exists():
            out.append(f"Real glass: [`receipts/{c.name}.png`](receipts/{c.name}.png) (0 px diff against the frame).\n")
    out.append("## Layout\n")
    out.append("`__init__.py` registry · `canvas.py` palette-only drawing (text, hairline, rect, circle, "
               "sparkline, bars, ring, wrap, fit_text) · `icons.py` weather glyphs · `adapters/` one file per "
               "data source, each with a fixture · `fixtures/` · `previews/` rendered at build · `receipts/` "
               "real captures · `../glass_routes.py` the three routes · `../test_glass.py`.\n")
    out.append("## Three structural rules, enforced\n")
    out.append("Every component, both orientations, on every test run — and at commit time once you run "
               "`git config core.hooksPath tools/hooks` (the hook fires only when a `glass/*.py` is staged, ~4 s; "
               "`GLASS_SKIP_HOOK=1` overrides).\n")
    out.append("| rule | why | test |\n|---|---|---|\n"
               "| nothing under **11 px** | 800×480 on a 7-inch panel at arm's length | `test_every_glyph_is_legible_at_arms_length` |\n"
               "| text two grays from what is under it | LIGHT on WHITE or DARK on LIGHT reads as nothing on e-ink | `test_every_glyph_has_contrast` |\n"
               "| ≤ **40 % BLACK** (photo and playing excepted) | heavy black ghosts on refresh | `test_ink_budget` |\n")
    out.append("Regenerate this file: `python -m glass.index_md` (a test fails if it is stale).\n")
    return "\n".join(out)


def build_native() -> str:
    load_all()
    rows = [NATIVE_INTRO.rstrip("\n")]
    for c in REGISTRY.values():
        rows.append(f"| `{c.name}` | {c.native_hint or '—'} |")
    rows.append("")
    return "\n".join(rows)


def write() -> bool:
    changed = False
    for path, text in ((HERE / "README.md", build_readme()), (HERE / "NATIVE.md", build_native())):
        if not path.exists() or path.read_text() != text:
            path.write_text(text)
            changed = True
    return changed


if __name__ == "__main__":
    ch = write()
    print("written" if ch else "up to date")
    sys.exit(0)
