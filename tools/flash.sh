#!/bin/bash
# idf.py flash with the port + safe-baud fallback (hub chain corrupts >=921600).
set -e
PORT=$(dirname "$0")/port.sh; PORT=$($PORT)
cd "$(dirname "$0")/../firmware"
idf.py -p "$PORT" -b 460800 "$@" flash || idf.py -p "$PORT" -b 115200 "$@" flash
