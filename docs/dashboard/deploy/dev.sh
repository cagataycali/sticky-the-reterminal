#!/bin/bash
# A second dashboard on :8790 for development, with an EMPTY passkey store —
# never touches the launchd instance on :8787 or its .sticky_auth.json.
# Playwright's virtual authenticator enrols a fresh passkey each run, so the
# store must start empty (a stale one shows "Your Stickies" and login fails).
#   STICKY_MOCK=1 deploy/dev.sh   → software Sticky, no hardware needed
set -e
PORT=${STICKY_DASH_PORT:-8790}
STORE=${STICKY_AUTH_STORE:-/tmp/dash-dev-auth.json}
lsof -iTCP:"$PORT" -sTCP:LISTEN -t | xargs -r kill; sleep 0.5; rm -f "$STORE"
cd "$(dirname "$0")/.."
STICKY_DASH_PORT=$PORT STICKY_AUTH_STORE=$STORE STICKY_MOCK=${STICKY_MOCK:-0} STICKY_DEVICE=${STICKY_DEVICE:-sticky} \
  nohup "${PYTHON:-$HOME/.tiny/pypi/bin/python}" server.py > /tmp/dash-dev.log 2>&1 &
sleep 2; curl -s -o /dev/null -w "dev :$PORT health %{http_code}\n" "localhost:$PORT/api/health"
