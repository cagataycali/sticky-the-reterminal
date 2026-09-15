#!/bin/bash
# stickyOS dashboard launcher — used by the launchd agent so the control
# surface at sticky.cagatay.my survives reboots. LIVE mode: the mock died
# the day the hardware enrolled. Auth store is .sticky_auth.json (enrolled);
# no bootstrap token here — a secret in a plist is a secret in a backup.
export STICKY_MOCK=0
# D1 (2026-09-07): the dashboard is multi-device — every /api/* takes
# ?device=<id|name>; STICKY_DEVICE is only the DEFAULT when a request names none.
export STICKY_DEVICE=sticky
export STICKY_DASH_PORT=8787
# Optional secrets for glass adapters (GITHUB_TOKEN for the `github` card, …):
# KEY=VALUE lines in ~/.tiny/sticky-dashboard.env, mode 0600, never in git or
# in this plist. Absent file = those cards fall back to their fixtures.
if [ -r "$HOME/.tiny/sticky-dashboard.env" ]; then
  set -a; . "$HOME/.tiny/sticky-dashboard.env"; set +a
fi
cd "$HOME/sticky-the-reterminal/docs/dashboard"
exec "$HOME/.tiny/pypi/bin/python" server.py
