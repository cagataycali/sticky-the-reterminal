#!/bin/bash
# Gate for M0: the recovery dump must exist, be exactly 32MB, and match its sha.
set -e
cd "$(dirname "$0")/../recovery"
[ -f stock-firmware-32MB.bin ] || { echo "NO DUMP — do not flash"; exit 1; }
SIZE=$(stat -f%z stock-firmware-32MB.bin)
[ "$SIZE" = "33554432" ] || { echo "dump wrong size: $SIZE"; exit 1; }
if [ -f stock-firmware-32MB.bin.sha256 ]; then
  shasum -a 256 -c stock-firmware-32MB.bin.sha256
else
  shasum -a 256 stock-firmware-32MB.bin > stock-firmware-32MB.bin.sha256
  echo "sha recorded: $(cat stock-firmware-32MB.bin.sha256)"
fi
