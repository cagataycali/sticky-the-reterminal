#!/usr/bin/env bash
# Launch the sticky dashboard backend.
#   STICKY_MOCK=1 (default)  software Sticky — full envelope simulation
#   STICKY_MOCK=0 STICKY_DEVICE=<id|name>  live device via tiny.technology relay
#   STICKY_TLS=true          self-signed HTTPS (WebAuthn on LAN)
set -e
cd "$(dirname "$0")"
export STICKY_DASH_PORT="${STICKY_DASH_PORT:-8787}"
exec "${PY:-$HOME/.tiny/pypi/bin/python}" server.py
