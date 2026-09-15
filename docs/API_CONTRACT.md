---
readtime: 3
description: "Every HTTP route Sticky's firmware touches — exact wire shapes, the two invariants that bit us, and the provisioning portal."
---

# API contract — exact wire shapes

Third person; contracts don't do voices. Base URL from config (`api`, default
`https://tiny.technology`). Every device call authenticates with **`deviceId`
+ `token` in the JSON body**, not headers. 15 s timeout, TLS, `application/json`.

## Two invariants that bit us

1. **Relay payloads are strings containing JSON**, not nested objects — the
   sender `JSON.stringify`s. Parse the string, then read `.prompt`. Early
   firmware read it as an object and skipped every envelope in silence.
2. **One `401` never erases identity.** Blips happen. Three consecutive 401s
   trip an auth-halt (slow heartbeat, `auth_halted:true`, rescue portal); the
   token is kept for forensics. Never retry-loop.

## Routes

| route | when | body (besides `deviceId`,`token`) | reply |
|---|---|---|---|
| `POST /api/devices/heartbeat` | every 30 s, ~60 s idle | `capabilities[]` = exactly what dispatches; `wantUnread:"1"` opt-in | `{ok}`; `unread` rides the beat when asked |
| `PUT /api/devices/relay` | poll: 5 s active, 60 s after two quiet minutes | `max:5` | `{messages:[{id, payload:"<string>"}]}` — execute in order; unknown verb → help text |
| `PATCH /api/devices/relay` | one per envelope | `inReplyTo`, `payload:"<string ≤ 8000 B>"` → `{result[, images:[{url,format}]]}` | ack |
| `POST /api/media` | screenshot, voice WAV | `data:<base64>`, `contentType` | `{url}` on **plugin.tiny.technology** — a different host, which matters for OTA pinning |
| `POST /api/devices/ask` | `ask`, `voice`, AI button | `text` **or** `audioUrl`; `tiny:"<slug>"` when an agent is chosen | `{text, card?}` — the owner's **full** agent turn. Node runtime, not Edge (25 s first-byte 504'd long turns) |
| `POST /api/devices/transcript` | after every ask | `text`, `label:"sticky-ask"`, `audioUrl`, `durationS` | durable record |
| `POST /api/devices/event` | button tap on a card | `kind:"ui_tap"`, `card_id`, `button_id` | lands in the owner's activity feed |
| `POST /api/devices/messages` | messages app, `messages` verb | <code>op:unread&vert;<wbr>inbox&vert;<wbr>thread&vert;<wbr>send</code> (+`with` / `to`,`body`) | see below |
| `PUT /api/firmware/manifest` | `ota`, boot check | `channel:"sticky-stable"` | `{bundle:{url, sha256[, force]}}` |

`status` exposes `poll_cadence`, so the claim is a read, not a belief. Boot
and wake always start *active*.

## Messages on a device token

`unread` → `{unread, from[]}` · `inbox` → threads + counts · `thread` (with
`with:<login>`) → one conversation, **marks read platform-wide** — fetch only on
a real human open · `send` → sends as the owner, marked *"via <device name>"*.
The ops dispatch in-process to the existing DM handlers, so `send` inherits
every guardrail (2000 chars, 100/day, no self-DM). A 503 is never masked as an
empty inbox.

## OTA: three guards, each with a receipt

The manifest lists `files[]` with sha256; hosts must be on the pinned allowlist.
The original trigger was **direction-blind** (`strcmp` difference, not
*newer*): integrity verified, direction unchecked. Since 0.14.9:

- **install** — numeric `ver_cmp`; unparseable is refused, fail closed.
  `force:"1"` overrides and logs loudly. `force` is *channel state*, set at
  publish and self-clearing on the next forward publish — closing a deadlock
  where the publish rail allowed a rollback but dropped the flag the install
  rail needed.
- **publish** — the route 409s a backwards pointer unless `force:true`; the
  publisher also proves ancestry ([release rule 5](release.md)).
- **watch** — the dashboard's `ota` opens a 30-minute watch and renders a
  verdict: upgraded / DOWNGRADED / expired — never silence.

## Provisioning portal (device-side, Nicla-compatible)

```
softAP  SSID tiny-XXXX (FNV-1a over the full MAC — a serial suffix collides), key "tinysetup", 192.168.4.1
GET  /info   → {"kind":"reterminal-sticky","name","mac","version","provisioned"}
POST /setup  {device_id, token[, api, name], networks:[{ssid,key},…]}  → merge into NVS → 200 → reboot
GET  /       → minimal HTML form (manual fallback)
```

Two entries into one contract: *first boot* (empty NVS) and *rescue* after a
3-strike auth-halt, where the live STA widens to APSTA so a re-issued token
reaches a bricked-auth device with no cable. One audited hole stays honest
here until fixed: the rescue card's text sits at 468 of a 512-byte buffer.

## `/api/frames` — the remote render rail

The dashboard rasterizes anim-grammar JSON
into exact-size frames for the `play`
verb; the device stays a dumb pixel-pusher.

| route | auth | shape |
|---|---|---|
| `POST /api/frames` | session | anim spec → manifest. `seq_id = sha256(spec)[:16]`, idempotent. Caps: **≤120 frames · ≤60 s · ≥250 ms/frame**; over-cap → 422 with the arithmetic |
| `GET /api/frames/<seq>/manifest` | capability URL | <code>{frames[], previews[], interval_ms, format:"1bit"&vert;<wbr>"gray4", frame_bytes, count, closed_loop, caps}</code> |
| `GET /api/frames/<seq>/<n>.raw` | capability URL | `Content-Length` **exact** — 48 000 or 96 000 B, no padding: byte count *is* the format check. `<n>.png` previews the same bytes |
| `GET /api/frames` | public | machine-readable copy of this contract |
| `GET /duck` · `GET /ansi/<seq>` | public | the same frames as xterm-256 half-blocks — `curl sticky.cagatay.my/duck` |

Canvas semantics: 800×480 persists across ops; each paint op emits one frame.
On the record: the SPA catch-all once resolved `%2e%2e` outside `dist` and
served `.sticky_auth.json`; fixed the same commit, secret rotated.
