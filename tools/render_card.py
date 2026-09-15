#!/usr/bin/env python3
"""Push a card-spec JSON to the Sticky's serial card console (M2 bench harness).

Usage:
  tools/render_card.py '{"type":"text","title":"hi","body":"hello"}'
  tools/render_card.py --file card.json
  echo '{...}' | tools/render_card.py

Reads back the CARD_RESULT line as pass/fail. 115200 only — the hub chain
corrupts higher baud (observed on the first bench day).
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

PORT_SH = Path(__file__).resolve().parent / "port.sh"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("card", nargs="?", help="card JSON as one argument")
    ap.add_argument("--file", help="read card JSON from file")
    ap.add_argument("--port", help="serial port (default: tools/port.sh)")
    ap.add_argument("--timeout", type=float, default=12.0,
                    help="seconds to wait for CARD_RESULT (default 12)")
    args = ap.parse_args()

    if args.file:
        raw = Path(args.file).read_text()
    elif args.card:
        raw = args.card
    else:
        raw = sys.stdin.read()
    card = json.dumps(json.loads(raw), separators=(",", ":"))  # validate + minify

    port = args.port or subprocess.check_output(["bash", str(PORT_SH)]).decode().strip()

    try:
        import serial  # pyserial ships with esp-idf's python env
    except ImportError:
        print("pyserial missing — run inside `source ~/esp/esp-idf-v5.4/export.sh`",
              file=sys.stderr)
        return 2

    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 115200, 1
    s.dtr = False   # CH343 auto-reset circuit: keep DTR/RTS low so opening
    s.rts = False   # the port does NOT reboot the chip mid-demo
    s.open()
    with s:
        # Opening the port usually pulses the CH343 auto-reset -> chip reboots.
        # Wait for the console-ready banner (splash hold is 8s), or 6s of
        # silence (= no reset happened, console already live), before sending.
        ready_deadline = time.time() + 30
        saw_any = False
        silent_since = time.time()
        while time.time() < ready_deadline:
            ln = s.readline().decode(errors="replace")
            if ln:
                saw_any = True
                if "card console ready" in ln:
                    break
            elif not saw_any and time.time() - silent_since > 4:
                break  # port open did NOT reset the chip; console already live
            # if we saw boot output, wait for the banner (splash hold is 8s+)
        s.reset_input_buffer()
        s.write(card.encode() + b"\n")
        s.flush()
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            line = s.readline().decode(errors="replace").strip()
            if not line:
                continue
            print(line)
            if line.startswith("CARD_RESULT"):
                return 0 if line.endswith("ESP_OK") else 1
    print("timeout waiting for CARD_RESULT", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
