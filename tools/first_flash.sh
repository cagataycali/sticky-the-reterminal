#!/bin/bash
# FIRST FLASH of tiny firmware — refuses to run unless the recovery gate passed.
# Flashes bootloader+partitions+ota_data+app, then captures 45s of serial boot
# log to .logs/first-boot.log.
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
grep -q "VERIFY PASS" "$DIR/recovery/verify.log" 2>/dev/null || {
  echo "REFUSED: recovery/verify.log has no VERIFY PASS — dump not verified"; exit 1; }
pgrep -f "esptool.*read-flash" > /dev/null && { echo "REFUSED: dump still running"; exit 1; }
PORT=$("$DIR/tools/port.sh")
source ~/esp/esp-idf-v5.4/export.sh >/dev/null 2>&1
cd "$DIR/firmware/build"
python -m esptool --chip esp32s3 -p "$PORT" -b 460800 --before default_reset \
  --after hard_reset write_flash "@flash_args" \
  || python -m esptool --chip esp32s3 -p "$PORT" -b 115200 --before default_reset \
       --after hard_reset write_flash "@flash_args"
echo "[first_flash] flashed OK — capturing 45s boot log"
mkdir -p "$DIR/.logs"
( stty -f "$PORT" 115200 raw -echo 2>/dev/null || true
  timeout 45 cat "$PORT" > "$DIR/.logs/first-boot.log" 2>&1 || true )
echo "[first_flash] boot log captured:"
grep -a -E "tiny|splash|card|boot|panic|abort" "$DIR/.logs/first-boot.log" | head -20
