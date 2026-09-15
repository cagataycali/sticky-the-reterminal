#!/usr/bin/env python3
"""Pre-flight glass check for callers that bypass the dashboard rail.

The dashboard's glass_etiquette gate (docs/dashboard/glass_etiquette.py)
covers only invokes that flow through sticky.cagatay.my. A caller driving the
device via tiny_relay / use_device directly has no gate — two such callers
once painted over each other. This is that gate, callable from anywhere:

    python3 tools/preflight_glass.py <my-name>
    → exit 0  glass clear (nobody else's announced window is open)
    → exit 1  BUSY — prints the open window's marquee line verbatim

Contract (same as the dashboard's): announce your own
`glass: <name> — <what> window OPEN (~Nmin): …` line BEFORE painting, close
with `… CLOSED` / `… restored` after. TTL 10min. Probes (status/sensors/
screenshot) never need this; anything that claims the canvas does.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "docs" / "dashboard"))
import glass_etiquette  # noqa: E402


def check(lane: str) -> int:
    glass_etiquette.OUR_LANE = lane  # skip our own windows, honor everyone else's
    w = glass_etiquette.open_window()
    if w is None:
        print("glass clear")
        return 0
    print(f"BUSY — {w['lane']}'s window is open:\n  {w['text']}")
    return 1


def claim(lane: str, what: str) -> int:
    """Atomic claim: announce FIRST, then verify we won the race.

    Check-then-announce is two steps — on 2026-08-26 two callers opened
    windows 8 seconds apart through the plain check and nearly collided.
    The marquee file is append-ordered, so it can arbitrate: write our OPEN
    line, wait a settle beat, re-read. If someone else's still-open window
    has an OLDER timestamp than ours, they were first — we self-close with
    a 'yielded' line and report BUSY. If ours is oldest, the glass is ours.
    Both racers running this protocol cannot both win; the file order is
    the tiebreak, and a double-yield is impossible because yield decisions
    compare the SAME timestamps in both processes.
    """
    glass_etiquette.OUR_LANE = lane
    w = glass_etiquette.open_window()
    if w is not None:  # cheap pre-check: don't even announce over a live window
        print(f"BUSY — {w['lane']}'s window is open:\n  {w['text']}")
        return 1
    glass_etiquette.announce(f"glass: {lane} — {what} window OPEN (~5min): claimed via preflight")
    ours_ts = time.time()
    time.sleep(2.0)  # settle: let a racing announce land in the file
    w = glass_etiquette.open_window()
    if w is not None and w["ts"] < ours_ts:
        glass_etiquette.announce(
            f"glass: {lane} — window CLOSED (yielded pre-paint: {w['lane']} announced first)")
        print(f"BUSY — lost the race to {w['lane']}:\n  {w['text']}")
        return 1
    print(f"glass claimed by {lane} — announce is on the marquee; "
          f"close with a 'glass: {lane} — … CLOSED' line when done")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:]]
    lane = (args[0] if args else "unknown").lower()
    if len(args) >= 2 and args[1] == "claim":
        return claim(lane, " ".join(args[2:]) or "direct-relay paint")
    return check(lane)


if __name__ == "__main__":
    raise SystemExit(main())
