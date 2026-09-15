#!/bin/bash
# Waits for the stock-dump esptool to exit, then runs the M0 verify gate.
# Writes everything to recovery/verify.log. NEVER flashes — that is a
# separate, logged step (proof before first flash).
set -u
cd "$(dirname "$0")/../recovery"
echo "[autoverify] $(date) waiting for read-flash to finish..."
while pgrep -f "esptool.*read-flash" > /dev/null; do sleep 20; done
echo "[autoverify] $(date) esptool exited."
sleep 3
ls -la stock-firmware-32MB.bin 2>/dev/null
if ../tools/verify_dump.sh; then
  echo "[autoverify] VERIFY PASS $(date)"
else
  echo "[autoverify] VERIFY FAIL $(date) — do not flash"
fi
