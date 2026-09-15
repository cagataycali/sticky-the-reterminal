#!/bin/sh
# Sticky installer — flash the tiny firmware onto a reTerminal Sticky (ESP32-S3).
#
#   curl -fsSL https://cagataycali.github.io/sticky-the-reterminal/install.sh | sh
#
# What it does, in order, and stops at the first thing it cannot prove:
#   1. finds esptool   — uvx esptool > pipx run esptool > python3 -m pip install --user esptool
#   2. downloads the manifest, the 4 parts and sha256sums from the SAME origin, verifies them
#   3. finds exactly ONE Sticky USB port — a WCH CH343 bridge, 1a86:55d3 "USB Single Serial"
#      (macOS /dev/cu.usbmodem*, Linux /dev/ttyACM*)
#      0 or >1 candidates → it prints the list and stops. It never guesses.
#   4. flashes at 460800, falls back to 115200
#   5. tells you the three next steps (join tiny-XXXX · open 192.168.4.1 · paste your token)
#
# Knobs:
#   STICKY_PORT=/dev/cu.usbmodemXXXX   skip auto-detect, use this port
#   STICKY_BASE=https://…/install      fetch manifest + parts from another origin
#   STICKY_DRY_RUN=1                   do everything except flash; print the esptool command
#   STICKY_NO_ERASE=1                  keep NVS (token + Wi-Fi) — default for a fresh install is a full erase
set -eu

BASE="${STICKY_BASE:-https://cagataycali.github.io/sticky-the-reterminal/install}"
DRY="${STICKY_DRY_RUN:-0}"
say()  { printf '\033[1;36msticky\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31msticky\033[0m %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || die "curl is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required (esptool is Python)"

OS=$(uname -s)
case "$OS" in
  Darwin|Linux) ;;
  *) die "unsupported OS '$OS' — on Windows use the browser installer: ${BASE%/install}/install/" ;;
esac

# ── 1. esptool ────────────────────────────────────────────────────────────────
if command -v uvx >/dev/null 2>&1; then
  ESPTOOL="uvx esptool"
elif command -v pipx >/dev/null 2>&1; then
  ESPTOOL="pipx run esptool"
elif python3 -c 'import esptool' >/dev/null 2>&1; then
  ESPTOOL="python3 -m esptool"
else
  say "installing esptool for your user (python3 -m pip install --user esptool)"
  python3 -m pip install --user --quiet esptool || die "could not install esptool; install uv (https://astral.sh/uv) and re-run"
  ESPTOOL="python3 -m esptool"
fi
say "esptool via: $ESPTOOL"

# ── 2. artifacts ─────────────────────────────────────────────────────────────
TMP=$(mktemp -d 2>/dev/null || mktemp -d -t sticky)
trap 'rm -rf "$TMP"' EXIT INT TERM
say "fetching $BASE/manifest.json"
curl -fsSL "$BASE/manifest.json" -o "$TMP/manifest.json" || die "manifest not reachable"
curl -fsSL "$BASE/sha256sums.txt" -o "$TMP/sha256sums.txt" || die "sha256sums not reachable"
VER=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$TMP/manifest.json")
# "path offset" per part, sorted by offset
python3 - "$TMP/manifest.json" > "$TMP/parts.txt" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
b = [x for x in m["builds"] if x["chipFamily"] == "ESP32-S3"]
b or sys.exit("manifest has no ESP32-S3 build")
for p in sorted(b[0]["parts"], key=lambda p: p["offset"]):
    print(p["path"], hex(p["offset"]))
PY
say "firmware $VER"
mkdir -p "$TMP/fw"
while read -r path off; do
  curl -fsSL "$BASE/$path" -o "$TMP/$path" || die "could not download $path"
  size=$(wc -c < "$TMP/$path" | tr -d ' ')
  say "  $off  $size B  $path"
done < "$TMP/parts.txt"
( cd "$TMP" && if command -v sha256sum >/dev/null 2>&1; then sha256sum -c --quiet sha256sums.txt;
  else shasum -a 256 -c --quiet sha256sums.txt; fi ) || die "sha256 mismatch — refusing to flash"
say "sha256 verified"

# ── 3. exactly one port ──────────────────────────────────────────────────────
if [ -n "${STICKY_PORT:-}" ]; then
  PORT="$STICKY_PORT"
  [ -e "$PORT" ] || die "STICKY_PORT=$PORT does not exist"
else
  if [ "$OS" = Darwin ]; then
    # The Sticky's USB is a WCH CH343 bridge (1a86:55d3 "USB Single Serial"), not the S3's
    # native CDC — so filter /dev/cu.usbmodem* by the bridge VIDs (WCH 0x1a86, Espressif 0x303a).
    # ioreg carries the USB serial that macOS turns into the usbmodem suffix.
    CANDS=$(ioreg -l -w0 2>/dev/null | python3 -c '
import glob, re, sys
txt = sys.stdin.buffer.read().decode("utf-8", "replace")
serials = set()
for blk in re.split(r"\n\s*[| ]*\+-o ", txt):
    v = re.search(r"\"idVendor\" = (\d+)", blk); s = re.search(r"\"USB Serial Number\" = \"([^\"]*)\"", blk)
    if v and s and int(v.group(1)) in (0x1a86, 0x303a):
        serials.add(s.group(1).strip())
ports = sorted(glob.glob("/dev/cu.usbmodem*"))
# macOS appends a one-digit location suffix to the USB serial (5C84335888 -> usbmodem5C843358881)
hits = [p for p in ports if any(x and re.fullmatch(r"/dev/cu\.usbmodem" + re.escape(x) + r"\d?", p) for x in serials)]
print("\n".join(hits))
')
  else
    # Linux: prefer stable by-id names for the WCH bridge / Espressif CDC; fall back to every ttyACM.
    CANDS=$(for p in /dev/serial/by-id/*1a86* /dev/serial/by-id/*WCH* /dev/serial/by-id/*Espressif*; do [ -e "$p" ] && echo "$p"; done 2>/dev/null | sort -u || true)
    [ -n "$CANDS" ] || CANDS=$(for p in /dev/ttyACM*; do [ -e "$p" ] && echo "$p"; done 2>/dev/null || true)
  fi
  N=$(printf '%s\n' "$CANDS" | grep -c . || true)
  if [ "$N" -eq 0 ]; then
    die "no ESP32-S3 USB port found. Plug the Sticky in with a DATA cable (not charge-only) and re-run; or set STICKY_PORT=/dev/…"
  elif [ "$N" -gt 1 ]; then
    printf '%s\n' "$CANDS" >&2
    die "$N candidate ports — I will not guess. Unplug the others or set STICKY_PORT=<one of the above>"
  fi
  PORT="$CANDS"
fi
say "port $PORT"

# ── 4. flash ─────────────────────────────────────────────────────────────────
ARGS="--chip esp32s3 --port $PORT --before default_reset --after hard_reset"
WRITE="write_flash --flash_mode dio --flash_size 32MB --flash_freq 80m"
[ "${STICKY_NO_ERASE:-0}" = 1 ] || WRITE="$WRITE --erase-all"
PARTS=$(while read -r path off; do printf '%s %s ' "$off" "$TMP/$path"; done < "$TMP/parts.txt")
CMD_FAST="$ESPTOOL $ARGS --baud 460800 $WRITE $PARTS"
CMD_SLOW="$ESPTOOL $ARGS --baud 115200 $WRITE $PARTS"
if [ "$DRY" = 1 ]; then
  say "DRY RUN — would run:"
  echo "  $CMD_FAST"
  echo "  (fallback) $CMD_SLOW"
else
  say "flashing $VER at 460800…"
  # shellcheck disable=SC2086
  $CMD_FAST || { say "retrying at 115200"; $CMD_SLOW; } || die "flash failed"
  say "flashed."
fi

# ── 5. next ──────────────────────────────────────────────────────────────────
cat <<EOF

  Sticky is rebooting with $VER. Give it a brain — three steps:

  1. Join the Wi-Fi it opens:   tiny-XXXX   (key: tinysetup)
  2. Open                        http://192.168.4.1
  3. Paste a device token from   https://tiny.technology → Devices → Add

  Manual: ${BASE%/install}/start/quickstart/
EOF
