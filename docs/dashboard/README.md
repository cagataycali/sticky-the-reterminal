# sticky dashboard — `sticky.cagatay.my`

The control surface for every Sticky on the account: a FastAPI backend that
speaks the tiny.technology device relay, and a small React SPA that shows
each device's real e-ink framebuffer inside a bezel and lets you drive it.
Passkeys are the only key. This directory is CODE; it is excluded from the
MkDocs site.

```
browser ── passkey ──▶ server.py (:8787, launchd) ──▶ tiny.technology relay ──▶ Sticky (polls 5–60 s)
                        │  /api/*?device=<id|name>       ▲
                        └─ frontend/dist (SPA, static)   └─ owner JWT from ~/.tiny/credentials.json
```

## Multi-device (since 2026-09-07)

Every `/api/*` route takes `?device=<id|name>`; when absent the default is
`STICKY_DEVICE` (`sticky`). `GET /api/devices` lists the fleet with
`online` (last heartbeat ≤ 120 s), `age_s`, `battery`, `fw`, `default`.
Per-device state — activity log, last screenshot meta, button zones,
glass etiquette window — is keyed by device id, so a card sent to
`tiny-3096` never shows up as a tap zone on `sticky`. The SPA keeps the
selection in `?device=` and `localStorage`; `/s/<device_id>` opens that
device with the list hidden.

| route | what |
|---|---|
| `GET /api/devices` | the fleet, ordered default → online → name |
| `GET /api/capabilities?device=` | verbs the device's own heartbeat advertises; greyed = refused 501 |
| `POST /api/invoke?device=` | `{command, args?, wait_s?}` → device receipt, or `{pending, envelope_id}` |
| `GET /api/result/<envelope_id>` | redeem a pending receipt (mailbox ≈ 24 h) |
| `GET /api/screenshot?device=` | the real framebuffer (800×480 panel space) + `rotation` meta |
| `GET /api/activity?device=` | the per-device log the Activity card shows |
| `/auth/*` | WebAuthn register/login/logout, 30-day session cookie |

Full table with auth levels: [`../API_CONTRACT.md`](../API_CONTRACT.md).

## Design language

The SPA is the docs site's language, ported (`frontend/src/styles.css`,
tokens from `docs/stylesheets/sticky.css`): paper background, the panel's
four grays as the only neutrals, one cyan accent (`--accent` for dots and
borders, `--accent-ink` for text so 12 px clears 4.5:1), hairlines instead
of boxes, 14 px card radius, 8 px rhythm, Inter + JetBrains Mono
self-hosted from `frontend/public/fonts/`. Dark scheme is designed, not
inverted; the device bezel stays white in it. `Bezel.tsx` is the landing
page's device geometry (106 × 65.5 mm, four magnets, three top buttons);
portrait rotation stands it up while taps stay in panel space.

Rules the components follow: a device that is offline is shown
**asleep with its last frame dimmed** and "last seen N ago", never an
error; every action ends in a `Receipt` (pending → landed → refused) in
the device's own words; unavailable verbs are greyed with the reason,
not hidden; 0 axe-core violations in both schemes is a build gate in
spirit (see Verify).

## Develop

```bash
cd frontend && npm ci && npm run build        # dist/ is gitignored — build it
deploy/dev.sh                                 # second instance on :8790, empty passkey store
STICKY_MOCK=1 deploy/dev.sh                   # no hardware: a software Sticky answers
npm run dev                                   # Vite HMR on :5173, proxies /api to :8787
```

Env: `STICKY_MOCK` (0 = real relay), `STICKY_DEVICE` (default device),
`STICKY_DASH_PORT`, `STICKY_AUTH_STORE` (passkey store; production uses
`.sticky_auth.json`, gitignored), `TINY_CREDENTIALS` (owner JWT path).

## Deploy

The frontend is static: `npm run build` in `frontend/` is live on the next
request (the server reads `dist/` from disk). The backend is owned by
launchd — the ONE correct restart is in [`deploy/RESTART.md`](deploy/RESTART.md):

```bash
launchctl kickstart -k gui/$(id -u)/technology.tiny.sticky-dashboard
curl -s https://sticky.cagatay.my/api/health
```

A kickstart clears the premiere queue slot; check `/api/frames/premiere-queue`
first. The Cloudflare tunnel is `technology.tiny.cloudflared-sticky`.

## Verify

Playwright with a virtual authenticator against `deploy/dev.sh`:
register → dashboard → per-device switch → screenshot both schemes at
1440 and 390 → axe-core (`wcag2a, wcag2aa, wcag21aa, best-practice`)
must report 0 violations. Wait ≥ 600 ms after a colour-scheme switch
before running axe — it otherwise samples text mid-transition and reports
blended colours. Only read-only or self-restoring verbs (`screenshot`,
`page home`) against the real fleet; the glass is the owner's desk.

## Files

`server.py` routes · `auth.py` passkeys · `tiny_relay.py` relay client ·
`glass_etiquette.py` the 409 "glass busy" window · `frames*.py` animation
sequences · `mock_device.py` the software Sticky · `frontend/` the SPA ·
`deploy/` launcher, restart truth, dev instance.
