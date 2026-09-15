#!/bin/bash
# provision_new.sh — enroll a NEW Sticky in the tiny fleet and flash it in ONE run.
#
#   WIFI_SSID=MyAP WIFI_KEY=secret tools/provision_new.sh /dev/cu.usbmodemXXXX
#
# Needs: ESP-IDF v5.4 exported (esptool, esp_idf_nvs_partition_gen), a built
# firmware/build/, a tiny.technology session in ~/.tiny/credentials.json, and the
# WiFi the board should join: WIFI_SSID/WIFI_KEY env, or else the "wifi" block of
# an earlier receipt (.secrets.device.json). TINY_API overrides the API base.
#
# Why one run: POST /api/devices returns the device token exactly once. This
# script mints it, writes {device_id, token, api, name, networks} straight into
# an NVS image (namespace "tiny", key "cfg" — the blob tiny_config.cpp reads)
# and flashes bootloader + partition table + otadata + app + NVS together, so
# the board boots provisioned and heartbeats without the softAP dance. The
# token never touches the shell history; the receipt lands in
# .secrets.<name>.json (gitignored, 0600).
#
# Guards (enroll-a-device.md): the cloud name == the board's own AP SSID
# (tiny-XXXX, FNV-1a over the base MAC — same formula as tiny_provision.cpp);
# a fleet row already holding that name means the board was enrolled before
# and this refuses (adopt instead — never mint a ghost row). NEVER run this on
# the SO-101 or Nicla ports: pass the port explicitly, this script guesses nothing.
set -euo pipefail
PORT="${1:?usage: provision_new.sh /dev/cu.usbmodemXXXX}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
CREDS="$HOME/.tiny/credentials.json"
API="${TINY_API:-https://tiny.technology}"
WIFI_SRC="$DIR/.secrets.device.json"   # fallback: an earlier receipt's "wifi" block
if [ -z "${WIFI_SSID:-}" ]; then
  [ -f "$WIFI_SRC" ] || { echo "REFUSED: set WIFI_SSID/WIFI_KEY (no $WIFI_SRC to copy from)"; exit 1; }
  WIFI_SSID=$(python3 -c "import json;print(json.load(open('$WIFI_SRC'))['wifi']['ssid'])")
  WIFI_KEY=$(python3 -c "import json;print(json.load(open('$WIFI_SRC'))['wifi']['key'])")
fi
export WIFI_SSID WIFI_KEY
NVS_OFF=0x9000; NVS_SIZE=0x6000

source ~/esp/esp-idf-v5.4/export.sh >/dev/null 2>&1
cd "$DIR/firmware/build"
[ -f tiny_sticky.bin ] || { echo "REFUSED: no firmware/build/tiny_sticky.bin — build first"; exit 1; }

# 1. identity from the silicon
MAC=$(python -m esptool --chip esp32s3 -p "$PORT" -b 115200 --before default_reset --after no_reset read_mac 2>&1 \
      | grep -m1 -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}')
[ -n "$MAC" ] || { echo "REFUSED: could not read MAC on $PORT (is it an ESP32-S3?)"; exit 1; }
NAME=$(python3 - "$MAC" <<'EOF'
import sys
h=0x811c9dc5
for b in bytes.fromhex(sys.argv[1].replace(':','')):
    h^=b; h=(h*0x01000193)&0xffffffff
print(f"tiny-{h&0xffff:04x}")
EOF
)
echo "[provision] $PORT mac=$MAC name=$NAME"

# 2. duplicate guard, then enroll (session-authenticated; token shown once)
TOK=$(python3 -c "import json;print(json.load(open('$CREDS'))['token'])")
if curl -sf -H "Authorization: Bearer $TOK" "$API/api/devices" | python3 -c "
import json,sys; devs=json.load(sys.stdin); devs=devs.get('devices',devs)
sys.exit(0 if any(d.get('name')=='$NAME' for d in devs) else 1)"; then
  echo "REFUSED: fleet already has a device named $NAME — adopt it (POST /api/devices/adopt), do not re-enroll"; exit 1
fi
RECEIPT="$DIR/.secrets.$NAME.json"
umask 077
curl -sf -X POST "$API/api/devices" -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d "{\"name\":\"$NAME\",\"platform\":\"esp32s3\",\"kind\":\"daemon\",\"capabilities\":[\"render_ui\",\"status\",\"sensors\",\"say\",\"ask\",\"play\",\"voice\",\"screenshot\",\"miccheck\",\"page\",\"rotate\",\"glance\",\"tap\",\"swipe\",\"scroll\",\"lock\",\"unlock\",\"sleep\",\"ota\",\"messages\",\"config\",\"agent\"]}" \
  > "$RECEIPT"
DEVICE_ID=$(python3 -c "import json;d=json.load(open('$RECEIPT'));assert d.get('ok'),d;print(d['device_id'])")
echo "[provision] enrolled device_id=$DEVICE_ID (receipt $RECEIPT)"

# 3. NVS image: the exact JSON blob tiny_config.cpp expects
WORK=$(mktemp -d)
python3 - "$RECEIPT" "$NAME" "$API" "$WORK" <<'EOF'
import json,sys,csv,os
rec,name,api,work=sys.argv[1:]
r=json.load(open(rec)); w={"ssid":os.environ["WIFI_SSID"],"key":os.environ.get("WIFI_KEY","")}
cfg={"device_id":r["device_id"],"token":r["device_token"],"api":api,"name":name,
     "networks":[w]}
r.update({"mac_name":name,"wifi":w})
json.dump(r,open(rec,"w"),indent=2)
with open(f"{work}/nvs.csv","w",newline="") as f:
    wr=csv.writer(f); wr.writerow(["key","type","encoding","value"])
    wr.writerow(["tiny","namespace","",""])
    wr.writerow(["cfg","data","string",json.dumps(cfg,separators=(",",":"))])
EOF
python -m esp_idf_nvs_partition_gen generate "$WORK/nvs.csv" "$WORK/nvs.bin" $((NVS_SIZE)) >/dev/null
echo "[provision] nvs image $(stat -f%z "$WORK/nvs.bin") bytes"

# 4. flash everything (hub chain corrupts >=921600; 460800 then 115200)
flash() { python -m esptool --chip esp32s3 -p "$PORT" -b "$1" --before default_reset --after hard_reset \
  write_flash --flash_mode dio --flash_size 32MB --flash_freq 80m \
  0x0 bootloader/bootloader.bin 0x8000 partition_table/partition-table.bin 0xf000 ota_data_initial.bin \
  0x20000 tiny_sticky.bin $NVS_OFF "$WORK/nvs.bin"; }
flash 460800 || flash 115200
rm -rf "$WORK"

# 5. boot evidence
mkdir -p "$DIR/.logs"
LOG="$DIR/.logs/first-boot-$NAME.log"
( stty -f "$PORT" 115200 raw -echo 2>/dev/null || true; ( cat "$PORT" & CP=$!; sleep 40; kill $CP 2>/dev/null ) > "$LOG" 2>&1 || true )
echo "[provision] boot log $LOG:"
grep -a -E "tiny_config|provision|wifi|heartbeat|got ip|panic|abort|Guru" "$LOG" | head -20
echo "[provision] done: $NAME $DEVICE_ID — verify with GET $API/api/devices (online within ~60s)"
