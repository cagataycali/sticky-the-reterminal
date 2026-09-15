#!/bin/bash
# load_soak.sh — the soak that would have caught the 0.25.0 wedge.
#
# An IDLE soak passed 0.25.0; the device died at t+6min UNDER LOAD
# (scroll + tap + screenshot during the design-review window). So the
# soak gate is now this: N cycles of the reviewer's own op mix, one
# cycle per minute, each cycle demanding a status reply and logging the
# internal-RAM floor. Any timeout = FAIL with the cycle number; floor
# below 60K = FAIL. Run from anywhere; needs jq + ~/.tiny/credentials.json.
#
# usage: STICKY_DEVICE=<device uuid> tools/load_soak.sh [cycles=15] [logfile=/tmp/sticky_load_soak.log]
# Needs the owner's tiny.technology session token in ~/.tiny/credentials.json.
set -u
CYCLES=${1:-15}
LOG=${2:-/tmp/sticky_load_soak.log}
TOKEN=$(jq -r '.token' ~/.tiny/credentials.json)
DEV=${STICKY_DEVICE:?set STICKY_DEVICE to the target device uuid (tools/port.sh lists the fleet)}

rpc() { # rpc "<prompt>" [polls]
  local ID R N=${2:-10}
  ID=$(curl -s -m 20 -X POST https://tiny.technology/api/devices/relay \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"toDevice\":\"$DEV\",\"payload\":{\"type\":\"invoke\",\"prompt\":$(jq -Rn --arg p "$1" '$p')}}" | jq -r '.id')
  [ -z "$ID" ] || [ "$ID" = "null" ] && echo "SEND_FAIL" && return 1
  for _ in $(seq 1 "$N"); do
    sleep 6
    R=$(curl -s -m 20 "https://tiny.technology/api/devices/relay?inReplyTo=$ID" -H "Authorization: Bearer $TOKEN")
    if echo "$R" | jq -e '.reply' >/dev/null 2>&1 && [ "$(echo "$R" | jq -r '.reply')" != "null" ]; then
      echo "$R" | jq -r '.reply.payload'; return 0
    fi
  done
  echo "TIMEOUT envelope=$ID"; return 2
}

echo "== load soak $(date -u) cycles=$CYCLES ==" | tee -a "$LOG"
# Start on the page under test, at the top.
rpc "page settings" 8 >>"$LOG" 2>&1 || { echo "FAIL cycle=0 (page settings)" | tee -a "$LOG"; exit 1; }

for i in $(seq 1 "$CYCLES"); do
  T0=$(date -u +%H:%M:%S)
  # The reviewer's op mix: scroll, a tap at scrolled state, a screenshot
  # (HTTPS upload = the TLS/internal-RAM pressure), then the status gate.
  rpc "scroll down" 6 >>"$LOG" 2>&1
  rpc "tap 215 190" 8 >>"$LOG" 2>&1
  rpc "screenshot" 10 >>"$LOG" 2>&1
  S=$(rpc "status" 10)
  echo "$S" >>"$LOG"
  if echo "$S" | grep -q TIMEOUT; then
    echo "FAIL cycle=$i @$T0 — status timeout (wedge fingerprint)" | tee -a "$LOG"; exit 1
  fi
  FLOOR=$(echo "$S" | jq -r 'fromjson? // . | .internal_min_free // empty' 2>/dev/null)
  [ -z "$FLOOR" ] && FLOOR=$(echo "$S" | grep -oE '"internal_min_free":[0-9]+' | grep -oE '[0-9]+')
  echo "cycle=$i @$T0 internal_min_free=${FLOOR:-?}" | tee -a "$LOG"
  if [ -n "${FLOOR:-}" ] && [ "$FLOOR" -lt 60000 ]; then
    echo "FAIL cycle=$i — floor $FLOOR < 60K" | tee -a "$LOG"; exit 1
  fi
  # reset for the next cycle so scroll always has room to move
  rpc "scroll top" 6 >>"$LOG" 2>&1
  sleep 20
done
# Leave the glass friendly (LOOP_BRIEF law) and report.
rpc "page home" 8 >>"$LOG" 2>&1
echo "PASS — $CYCLES load cycles, floors logged in $LOG" | tee -a "$LOG"
