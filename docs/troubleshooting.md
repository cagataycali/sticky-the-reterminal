---
readtime: 3
description: "Sticky's observed defects, symptom-first — what you see, why, and the shipped fix."
---

# Troubleshooting — when I misbehave

Every entry is a defect observed on my hardware, with the cause and the
shipped fix. Symptoms first, because that's what you have. First move, always:
`status` (read `fw`, `last_boot`, `internal_min_free`), then `screenshot`.

## Quick table

| You see | Why | Do |
|---|---|---|
| `ask failed (422): nothing transcribed` | no usable speech in the recording — not a bug | hold the AI button, speak a full sentence |
| `ask failed: couldn't reach tiny` | TLS/HTTP write failed locally; I retry once | chase my Wi-Fi or heap, not tiny |
| Dashes or letters render as `???` | pre-0.14.15 folding | `ota`. Current builds render çğıöşü as written; CJK is an honest `?` |
| A `kv` card is title + footer + void | it reads `rows`, not `kv` | send `rows`; a missing field is now named on the glass, overflow says `+N did not fit` |
| Phantom taps after boot | GT911 ghosts | fixed: 2-poll debounce + boot grace |
| A tap hits the wrong button | hit-regions published at draw time, seconds before the ink showed them | fixed in 0.9.x: regions publish with the card commit |
| Heartbeat gets a `401` | a blip | one 401 never wipes identity; 3+ in 5 min → auth-halt, `auth_halted:true`, rescue portal |
| Asleep, won't answer | radio is off | only the AI button or the sleep timer wakes me |
| Portal never appears | boot-gated on zero stored networks | expected on a provisioned me; it reopens itself if my token is revoked |
| Opening the serial port reboots me | CH343 asserts DTR/RTS on open and close | the port is for flashing; iterate over `ota` |
| `ota` says "already up to date" | channel pointer matches what I run | publish a new manifest first |

## The ones that need a paragraph

### A card you sent never appeared, and the reply was silence

Relay envelopes are delivered once; if a bug made me consume one without
handling it, it's gone — re-send after any fix. (Historical cause: the relay
delivers `payload` as a *serialized JSON string*; early firmware read it as an
object and skipped silently. I parse both now.)

### Wake feels slow

`status` carries `first_paint_ms` and `boot_path` when the boot measured
itself. Cold boot near **8 s** is normal (the splash pause is a diagnostic
screen); deep-sleep wake near **1.1 s** is the record; **~2.5 s** means the
partial-first path fell back to a full flash — expected after power loss
mid-sleep. Past 3 s is news: file it *with the numbers*.

### I go silent after a horizontal swipe

Fixed in 0.16.5. I never rebooted, I **wedged**: a ring-modulus bug could
swipe me into an unrequested BLE scan with Wi-Fi live on the same radio, and
every later envelope waited behind it. Three fixes: the page ring counts its
4 roots only (`static_assert`), the dead-end blips instead of misbehaving,
and a wedge watchdog reboots me after 45 s of one stuck route.

### My mic is deaf after I slept

The ESP32-S3 pin mux reclaims GPIO19/20 for USB-Serial-JTAG on deep-sleep
wake ([hardware](hardware.md)). Fixed: reclaimed before *every* capture.
Prove it with nobody home: `miccheck 2` — a dead mux reads flat zeros.

### I died at minute four

On 0.23.0 every TLS handshake failed `ALLOC_FAILED` while `status` said
**8.19 MB free**. The field counted PSRAM; mbedTLS starves for *internal*
RAM. Resolved in three moves: instruments (`internal_min_free`, first reading
41 KB), root cause (a Kconfig default pinning mbedTLS to internal RAM), floor
raised (static buffers and BLE pools to PSRAM): **41 K → 73 K → 115 K+**.
`internal_min_free` is my health gauge; `heap_free` is an alibi.

### The version string says the feature is there; my glass disagrees

Version fields describe the tree the binary *claims*. Settle it in order:
`status` (`fw_commit` + uptime — did I really reboot?), probe the behaviour,
then `strings` on the artifact.

## If you are testing me, not using me

Pending envelopes execute when I drain them, not when you sent them — a
surprising reply needs a timestamp and a fresh probe. `rendered card_id=…`
means the renderer parsed your card; only a `screenshot` proves pixels.
Rotation is runtime state, so screenshot and tap coordinates go together.

---

Not covered? Every fix has a commit with the symptom in its subject line —
[search the history](https://github.com/cagataycali/sticky-the-reterminal/commits/main),
or open an issue with `status` and a `screenshot` attached.
