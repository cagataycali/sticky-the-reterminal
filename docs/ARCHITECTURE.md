---
readtime: 3
description: "How Sticky's firmware is put together — the module map as built, the tasks that run, the voice pipeline, and the security posture."
---

# How I work

The engineering ledger, in the third person — contracts don't do voices.
Everything below describes the binary as built.

```
┌─────────────────────────────── firmware/main ───────────────────────────────┐
│  tiny_main.cpp — boot sequence, task wiring (board_init FIRST, always)      │
│                                                                             │
│  the tiny layer, as built (22 modules; design heritage: strands-nicla):       │
│   ├─ tiny_config      NVS "tiny" namespace: networks[] roaming list,        │
│   │                   device_id, token, api, name — merge() semantics       │
│   ├─ tiny_provision   first-boot softAP "tiny-XXXX"/"tinysetup"/192.168.4.1 │
│   │                   same shapes as Nicla → iOS app onboards unchanged     │
│   ├─ tiny_wifi        join + roaming list; w+/w- truth for the glance bar   │
│   ├─ tiny_node        heartbeat 30 s (advertises caps = dispatch, parity    │
│   │                   rule) · relay poll 5 s/60 s · dispatch() — all 23     │
│   │                   verbs live HERE. 401 NEVER wipes identity: 3 strikes  │
│   │                   over 5 min → auth-halted + rescue portal, token kept  │
│   ├─ tiny_display     card-spec renderer → refresh-mode policy · title-bar  │
│   │                   glance cluster · tiny_display_glance() partial redraw │
│   ├─ tiny_shell       stickyOS: page ring (4 roots), nav stack, on-e-ink    │
│   │                   keyboard, gesture routing, edge-band nav              │
│   ├─ tiny_touch       GT911 → staged hit-regions, published atomically at   │
│   │                   display-time (never draw-time — the 0.9.x race)       │
│   ├─ tiny_button      GPIO4/5/6 press/hold semantics; raw-GPIO unlock chord │
│   ├─ tiny_lock        §11 pocket lockout: manual + IMU auto-entry, input    │
│   │                   gates (touch/buttons/mic), status.locked truth        │
│   ├─ tiny_orient      IMU classify → auto-rotate (settle before commit);    │
│   │                   its 1 Hz reader also feeds tiny_lock's walk detector  │
│   ├─ tiny_audio       PDM capture → WAV in PSRAM (capture-entry gate, §11)  │
│   │                   · buzzer chime, LEDC GPIO48 — chimes, never speech    │
│   ├─ tiny_upload      POST /api/media via esp_http_client (PSRAM buffer)    │
│   ├─ tiny_screenshot  framebuffer → upload: render ground truth on demand   │
│   ├─ tiny_sensors     I²C1 live reads: SHT40 · LSM6DS3TR-C · PCF8563 ·      │
│   │                   BQ27220 — the sensors verb reads hardware, not cache  │
│   ├─ tiny_time        RTC→system at boot; SNTP→system→RTC on Wi-Fi-up;      │
│   │                   clock names its source (sntp/rtc)                     │
│   ├─ tiny_ble         NimBLE observer — scan-only, honestly                 │
│   ├─ tiny_stream      `play`: manifest → frames prefetched to PSRAM before  │
│   │                   the ack → metronome task; verdict on every exit       │
│   ├─ tiny_askq        one ask in flight, fleet-wide; a second is refused    │
│   ├─ tiny_agent       which tiny answers (`agent` verb, roster in NVS)      │
│   ├─ tiny_bootmark    first_paint_ms + boot_path — the boot measures itself │
│   └─ tiny_ota         manifest poll → sha256-pinned staged download →       │
│                       trial-boot, rollback armed; ver_cmp refuses downgrade │
│                       unless the bundle carries force (channel state)       │
│                                                                             │
│  devices/ (adapted from vendor demo, thin)   board/ (board_init, buses)     │
└──────────────────────────────────────────────────────────────────────────────┘
   components/ (REUSED from vendor/Sticky_dashboard_demo via EXTRA_COMPONENT_DIRS)
   seeed_epaper · gt911 · bq27220 · button · debug_logging
```
The pre-implementation sketch planned a "401 → wipe token" recovery; it was
**rejected as a brick risk** in favour of the 3-strike auth-halt that keeps
the token for forensics. The rest of the plan-vs-binary story is in git history.

## Tasks

FreeRTOS tasks as created in the tree (priority, stack):

| task | prio | stack | job |
|---|---|---|---|
| `tiny_touch` | 6 | 4 K | GT911 sampling — always wins |
| `tiny_node` | 5 | 16 K | heartbeat 30 s · relay poll 5/60 s · `dispatch()` |
| `tiny_act` | 4 | 12 K | touch actions that do network (messages: TLS + JSON + render) |
| `voice_ask` | 4 | 8 K | AI button → record → upload → ask |
| `stream_play` | 4 | 12 K | the `play` metronome; owns no sockets |
| `disp_ack` | 4 | 3 K | the accept blip, fired before the render |
| `shell_nav` · `orient` · `lock_chord` · `tiny_time` · `wifi_roam` | 3 | 4–8 K | page ring, IMU classify, raw-GPIO unlock chord, SNTP→RTC, 30 s roaming |

Every socket has a timeout; no envelope executes silently — the buzzer blips.

## Voice-ask pipeline

```
hold AI btn ≥1 s → chime + (( listening ))
→ PDM 16 kHz/16-bit → PSRAM ring (cap 15 s) → release → WAV
→ POST /api/media (device token) → audioUrl
→ POST /api/devices/ask {deviceId, token, audioUrl}
→ backend: transcribe → owner-scoped agent turn → {text, card?}
→ streamed onto home at ≤2 Hz partial; text also POSTed to /api/devices/transcript
```

The relay verb `voice` runs the same path, so the pipeline is testable with
nobody holding the button. All three mic paths pass one capture funnel, and
the pocket lock gates the funnel.

## Provisioning

First boot with zero stored networks: a setup card with QR and a softAP
portal identical to the Nicla's, so the tiny iOS app onboards it unchanged
(join AP → `POST /setup {device_id, token, networks[]}`). Fleet provisioning
skips the portal by writing the same NVS blob over serial
([Quickstart](start/quickstart.md)).

## Security posture

- Flash carries **only** the device-scoped token — revocable; three
  consecutive 401s raise the rescue portal, the token is kept for forensics.
- OTA artifacts are sha256-pinned, hosts pinned, staged to `ota_1`, and boot
  as a rollback-armed trial that must heartbeat 200 before commit.
  `ver_cmp` refuses downgrades unless the bundle says `force:"1"`, loudly.
- `config` accepts networks only; identity fields are refused, secrets never
  echoed, the parse buffer zeroed.
- Heartbeat capabilities and the dispatch table are the same truth —
  advertise exactly what dispatches.

## Related contracts

- [Commands](commands.md) — the 23 verbs and their receipts
- [Cards](cards.md) — the render grammar and refresh policy
- [API contract](API_CONTRACT.md) — every HTTP route the firmware touches
- [Build & release discipline](release.md) — the rules a shared worktree taught us
