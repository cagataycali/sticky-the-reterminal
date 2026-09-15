#!/bin/bash
# port.sh — print the serial port of the Sticky the other tools should talk to.
# Needs: nothing. Resolution order, first hit wins:
#   1. STICKY_PORT=/dev/cu.usbmodemXXXX   (any machine — set this and you are done)
#   2. STICKY=<name>                       (looks the name up in the map below)
#   3. the first entry of the map, if that port exists on this machine
# It never guesses: other CH343 adapters on the same hub can be servo buses or
# other boards, and flashing those is destructive. If nothing resolves it exits 1.
#
# Owner-machine map (one line per board: port name device_id-prefix base-MAC).
# Ports are identified by CH343 USB serial, visible in `ls /dev/cu.usbmodem*`.
MAP='
/dev/cu.usbmodem5C843358881 sticky    b20893b4 b8:1f:3f:d7:23:60
/dev/cu.usbmodem5C843369361 tiny-3096 e318f3fc b8:1f:3f:d7:80:40
/dev/cu.usbmodem5C850605681 tiny-5f51 69778d71 b8:1f:3f:d7:87:b8
'
if [ -n "${STICKY_PORT:-}" ]; then echo "$STICKY_PORT"; exit 0; fi
if [ -n "${STICKY:-}" ]; then
  P=$(awk -v n="$STICKY" '$2==n{print $1}' <<<"$MAP")
  [ -n "$P" ] && { echo "$P"; exit 0; }
  echo "port.sh: no board named '$STICKY' in the map; set STICKY_PORT" >&2; exit 1
fi
P=$(awk 'NF{print $1; exit}' <<<"$MAP")
[ -e "$P" ] && { echo "$P"; exit 0; }
echo "port.sh: default port $P not present; set STICKY_PORT=/dev/cu.usbmodemXXXX" >&2
exit 1
