# Restarting the dashboard — the ONE correct way

```bash
launchctl kickstart -k gui/$(id -u)/technology.tiny.sticky-dashboard
```

That's it. Verified 2026-08-26: new pid, ppid=1, health 200 in ~5s.

## Why nothing else

The dashboard is owned by **launchd** (`~/Library/LaunchAgents/technology.tiny.sticky-dashboard.plist`,
`KeepAlive=true`, `RunAtLoad=true`, runs `deploy/launch_dashboard.sh`). It **survives reboot**
and resurrects on crash. Same for the tunnel: `technology.tiny.cloudflared-sticky`.

Forensics — every earlier restart habit was wrong and only worked by luck:

- `kill -9 $(lsof -ti :8787)` → launchd relaunches within seconds (`launchctl list`
  shows exit `-9`). The port comes back WITHOUT your help.
- `nohup /tmp/launch_sticky.sh &` after the kill → **races launchd and loses**: launchd's
  child binds :8787 first, the nohup'd server dies on "address in use", and you're left
  *believing* you deployed. It only ever "worked" because both scripts serve the same
  files — the code refresh actually came from launchd's relaunch.
- `/tmp/launch_sticky.sh` itself is on tmpfs — wiped on reboot. Never reference it again.
- `pkill -f "python server.py"` → matches the invoking shell's own cmdline (v2's lesson).

## Env truth

launchd's launcher (`deploy/launch_dashboard.sh`) is the canonical env:
`STICKY_MOCK=0 · STICKY_DEVICE=sticky · STICKY_DASH_PORT=8787`, logs in `.logs/`.
If you need a new env var, add it THERE, then kickstart.

## Known cost

A kickstart clears in-memory state: the **premiere queue slot** (`/api/frames/premiere-queue`)
resets to idle. Check it's not `waiting`/`running` before restarting, or re-queue after.
