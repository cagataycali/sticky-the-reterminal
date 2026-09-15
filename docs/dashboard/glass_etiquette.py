"""Glass etiquette — the marquee is machine-readable, so READ it.

Born from a glass collision: one client's announced verdict window was
painted over by another client's duck premiere.
One 800x480 panel, many writers — the convention already in use on the
marquee is:

    glass: <client> — <what> window OPEN (~5min): ...
    glass: <client> — ... CLOSED / home restored ...

This module parses that convention. A window is BUSY when a `glass:` line
containing "window OPEN" (case-insensitive) from another client has no later
line from the same client containing CLOSED/restored, and is younger than
WINDOW_TTL (announcements without a close are not leases forever — the TTL
is the honesty bound).

The dashboard both OBEYS (glass-painting invokes get a 409 naming the open
window; {"force":true} overrides, logged) and ANNOUNCES (frames/play writes
its own glass: line so other clients can see US coming).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

MARQUEE = Path.home() / ".tiny" / "marquee.jsonl"
WINDOW_TTL_S = 10 * 60
TAIL_LINES = 60
OUR_LANE = "dashboard"

# verbs that paint the panel (probes like status/sensors/screenshot pass)
# Input verbs (tap/scroll/swipe) count too: a synthetic finger mid-premiere
# navigates the panel away just as surely as a paint does.
GLASS_VERBS = {"play", "say", "render_ui", "page", "anim", "image", "rotate",
               "glance", "lock", "unlock", "sleep", "tap", "scroll", "swipe"}

# client name = EVERYTHING before the em-dash, normalized — "MAIN SESSION
# (owner's proxy)" is a client too; the gate was once blind to multi-word
# names, which would have fired the premiere queue over the OWNER's
# acceptance window.
_OPEN_RE = re.compile(r"^glass:\s*(?P<lane>[^—]+?)\s*—.*window\s+open", re.I)
_CLOSE_RE = re.compile(r"CLOSED|restored|restore[d]?\s+home", re.I)


def open_window(now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The youngest still-open announced window from ANOTHER client, or None."""
    if not MARQUEE.is_file():
        return None
    now = now or time.time()
    entries = []
    try:
        for line in MARQUEE.read_text().splitlines()[-TAIL_LINES:]:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return None
    windows: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        text = str(e.get("text", ""))
        m = _OPEN_RE.match(text)
        if m:
            lane = re.sub(r"\s+", " ", m.group("lane")).lower()
            windows[lane] = {"lane": lane, "text": text,
                             "ts": float(e.get("ts", 0)) / 1000.0}
            continue
        if text.lower().startswith("glass:") and _CLOSE_RE.search(text):
            # a close line names its client the same way — glass: <client> —
            lm = re.match(r"^glass:\s*([^—]+?)\s*—", text, re.I)
            if lm:
                windows.pop(re.sub(r"\s+", " ", lm.group(1)).lower(), None)
    for lane, w in sorted(windows.items(), key=lambda kv: -kv[1]["ts"]):
        if lane == OUR_LANE:
            continue
        if now - w["ts"] <= WINDOW_TTL_S:
            return w
    return None


def announce(text: str) -> None:
    """Append our own glass: line — best-effort, never raises."""
    try:
        MARQUEE.parent.mkdir(parents=True, exist_ok=True)
        prefix = ""
        if MARQUEE.is_file():
            with MARQUEE.open("rb") as rf:
                rf.seek(0, 2)
                if rf.tell() > 0:
                    rf.seek(-1, 2)
                    if rf.read(1) != b"\n":
                        # a writer that omits the trailing newline must not
                        # make us FUSE two JSON objects onto one line
                        prefix = "\n"
        with MARQUEE.open("a") as f:
            f.write(prefix + json.dumps({
                "id": f"mqdash{int(time.time()*1000):x}",
                "ts": int(time.time() * 1000),
                "author": "sticky-dashboard",
                "text": text,
            }) + "\n")
    except OSError:
        pass
