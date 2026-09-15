# AGENTS.md — for the agent reading this repository

You are looking at the firmware, docs and tools for **Sticky**: a Seeed
reTerminal Sticky (ESP32-S3, 3.97" 800×480 4-gray e-ink, touch, PDM mic,
buzzer, IMU, 750 mAh) running custom ESP-IDF firmware that makes it a body
for one tiny.technology agent. The device is a thin client. The intelligence
is server-side. Everything the device does is a verb it received or a page it
draws locally.

This file is the map. It is written so you can act correctly in your first
ten minutes without reading 33k lines. Where it conflicts with the tree, the
tree wins — then fix this file.

## 1. Orientation in 60 seconds

```
firmware/main/           ESP-IDF v5.4 app. tiny_*.cpp modules, headers in tiny/.
                         board/canvas/font/sticky_* = HAL adapted from Seeed's demo.
vendor/Sticky_dashboard_demo/components/   Seeed's drivers, byte-identical. NEVER edit.
docs/                    mkdocs-material site (first-person voice pages + third-person ledgers)
docs/dashboard/          a SEPARATE product: FastAPI + React owner dashboard (sticky.cagatay.my).
                         Excluded from the site build. ~18k LOC. Not needed to build firmware.
docs/install/            web installer: manifest.json + sha256sums.txt (parts are NOT committed)
tools/                   flash/monitor/provision/release/package scripts (bash + python)
tests/host/              plain C++ host tests against shipped firmware files (no board)
playground/tiny/         the Seeed Sticky Playground registry entry, verbatim layout
recovery/                factory-dump manifest + sha (the 32 MB dump itself is gitignored)
```

Versions live in exactly one place: `firmware/main/tiny/tiny_version.h` —
`TINY_FW_VERSION` (string, e.g. `0.27.0-u2`) and `TINY_GRAMMAR_VERSION`
(int, currently 12). CMake reads the first; the heartbeat and `status` report
both. Bump the grammar on **any** change to a verb, its args, or a reply
shape, and write a line in `CHANGELOG.md`.

Live truth about a device is never in a file. Ask it: `status` over the relay
(`sticky_display` tool → verb `status`, or `use_device invoke`). It answers
`fw`, `fw_commit`, `grammar_version`, battery, Wi-Fi, heap watermarks, SD,
`card_id` on glass, `locked`, `auth_halted`.

## 2. Build, flash, test — the exact commands

```sh
source ~/esp/esp-idf-v5.4/export.sh          # ESP-IDF v5.4, target esp32s3
cd firmware && idf.py build                  # → build/tiny_sticky.bin (+ bootloader, table, ota_data)
STICKY_PORT=/dev/cu.usbmodemXXXX ../tools/flash.sh        # app only, 460800 then 115200 fallback
STICKY_PORT=/dev/cu.usbmodemXXXX ../tools/first_flash.sh  # blank board: all four parts, 45 s boot log
STICKY_PORT=/dev/cu.usbmodemXXXX ../tools/monitor.sh
tests/run.sh                                 # host tests: askq, bootmark, sleep/wake — must stay green
```

- `tools/port.sh` resolves the port and **refuses to guess** when several
  CH343 "USB Single Serial" adapters are present (SO-101 servo buses share the
  chip; flashing one is destructive). Its embedded map is the owner's bench —
  set `STICKY_PORT` on any other machine.
- `firmware/sdkconfig` is gitignored; `sdkconfig.defaults` **is the record**.
  Two P0s were Kconfig defaults (mbedTLS pinned to internal RAM; the power
  latch). If you change a default, say so in the commit.
- `firmware/build/` is the release input for `tools/release.sh` and
  `tools/playground_package.sh`. Both refuse a `-dirty` build.
- Serial card console: any `{json}` line on UART0 renders as a card;
  `tools/render_card.py '{"type":"text","title":"hi","body":"…"}'`. A line
  containing `"config"` is a bench provisioning blob → NVS → reboot.
- CI: `.github/workflows/firmware-build.yml` (container `espressif/idf:v5.4`
  + `tests/run.sh`) and `release.yml` are **workflow_dispatch only** — Actions
  billing was off; the docs site publishes via `mkdocs gh-deploy` to
  `gh-pages` (legacy Pages). Do not assume a push builds anything.

## 3. Boot sequence — the contract (`tiny_main.cpp`)

Order is load-bearing. Do not reorder without reading the comments in the file.

1. `board_init()` **first** — owns the power latch (GPIO45/46). Without it the
   board powers itself off when the button is released.
2. `tiny_display_init()`, buzzer, restore silent/soft/rotation-lock from NVS
   **before** the boot chime (or mute un-mutes for one beep).
3. Cold boot: splash (`tiny` + bar). Deep-sleep wake (EXT1 button / timer) is
   a **glance wake**: no splash, paint home first, init inputs after —
   `first_paint_ms` ≈ 1.1 s is the acceptance, exposed in `status`.
4. Battery gauge + RTC → system clock (the paint's only data deps).
5. `tiny_shell_init()` renders **home before any network** — navigation must
   work offline.
6. Auto-rotate, microSD (after first paint, never in the glance budget).
7. `tiny_config_load()`: networks present → `tiny_wifi_connect()` → SNTP →
   `tiny_node_start()` if `device_id`+`token` present. **No networks → 
   `tiny_provision_start()`** (softAP portal). This is the first-boot path.
8. Serial card console loop forever.

`tiny_bootmark_stage(n)` writes the stage to NVS synchronously so a
crash-looping trial names the stage on the next boot (`boot_mark_prev`).

## 4. Modules (`firmware/main/tiny_*.cpp`) — one line each

| module | owns |
|---|---|
| `tiny_node` | heartbeat 30 s (idle 60 s) · relay poll 5 s / 60 s after 2 quiet min · `dispatch_inner()` — **every verb lives here** · reply PATCH · 3-strike 401 auth-halt (token kept, rescue portal) · ask queue drain · OTA trial commit |
| `tiny_display` | card-spec JSON → canvas → refresh policy (full vs partial) · title-bar glance cluster · hit-regions published atomically at **display time** · screenshot snapshot · gallery frames · stream notes |
| `tiny_shell` | stickyOS: ring home→status→sensors→settings, drill pages wifi/ble/cfgqr, nav stack (bottom = home), on-glass keyboard (wifi password / DM compose / typed ask), universe roster |
| `tiny_touch` | GT911 task (prio 6) → hit-region resolution → local action or `ui_tap` event upstream |
| `tiny_button` | GPIO4/5/6 press/hold · raw-GPIO UP+DOWN 1 s unlock chord that the gated layer cannot disable |
| `tiny_lock` | §11 pocket lockout: manual + IMU auto (face-down ≥3 s, walking ≥5 s after 30 s idle) · gates touch/buttons/**mic capture entry** · `status.locked` |
| `tiny_orient` | 1 Hz IMU classify → auto-rotate (settle before commit) · feeds lock's walk detector |
| `tiny_audio` | PDM 16 kHz WAV into PSRAM (cap 15 s) · re-claims GPIO19/20 from USB-JTAG before every capture (deep-sleep wake mutes the mic otherwise) · buzzer LEDC GPIO48, silent/soft tiers |
| `tiny_upload` / `tiny_screenshot` | `POST /api/media` (PSRAM buffer) · framebuffer → 2-bit PNG → URL |
| `tiny_sensors` | live I²C1 reads: SHT40, LSM6DS3TR-C, PCF8563, BQ27220 — never cached, nulls never fabricated |
| `tiny_time` | RTC→system at boot, SNTP→system→RTC on Wi-Fi; `clock_source` names itself |
| `tiny_wifi` | STA join + roaming `networks[]` walk · 30 s re-roam task · per-walk failure log (boot "confession" card on wrong password) |
| `tiny_config` | NVS namespace `tiny`, key `cfg`: ONE JSON blob {device_id, token, api, name, networks[]} · `merge_json` is the single owner of merge semantics (portal, serial, `config` verb all funnel here) |
| `tiny_provision` | softAP `tiny-XXXX` / `tinysetup` / 192.168.4.1 · `GET /` form · `GET /info` · `POST /setup` (JSON from the iOS app, or urlencoded from a human) → merge → reboot · also the **rescue portal** (APSTA) after auth-halt |
| `tiny_ota` | `PUT /api/firmware/manifest` → sha256-pinned download to the other slot → reboot as rollback-armed trial · hosts pinned to `tiny.technology` / `plugin.tiny.technology` · numeric `ver_cmp` refuses downgrade unless `force` |
| `tiny_stream` | `play <manifest>`: all frames prefetched to PSRAM in the **node task** before the ack, then a socket-less metronome task |
| `tiny_askq` | NVS ring of 8 queued text asks; one drained per healthy heartbeat |
| `tiny_agent` | which public tiny answers (`agent` verb, roster in NVS, `@slug` badge) |
| `tiny_ble` | NimBLE observer, scan-only |
| `tiny_bootmark` | boot stage crumbs in NVS `tinyboot` |

Other NVS namespaces: `tinywifi` (breadcrumbs), `tinyaskq`, `tinyboot`.

## 5. The verb grammar (v12) and the parity rule

Dispatched in `tiny_node.cpp::dispatch_inner()` — **23 verbs**:

`render_ui` `status` `sensors` `say` `ask` `agent` `play` `voice` `screenshot`
`miccheck` `page` `rotate` `glance` `tap` `swipe` `scroll` `lock` `unlock`
`sleep` `ota` `messages` `config` `sd`

Advertised in the heartbeat `caps[]` (`tiny_node.cpp` ~line 233) — the same
**23** since 0.28.0 (grammar v12 added `sd`, which had dispatched unadvertised
since 0.26.0). The rule: *advertise exactly what dispatches*. When you add a
verb, add it to BOTH, bump the grammar, write the CHANGELOG line.

Reply contract: `PATCH /api/devices/relay {inReplyTo, payload:"<string ≤8000 B>"}`
where payload is a serialized JSON string `{"result":…[, "images":[{url,format}]]}`.
Unknown verb → help text, never silence. `config` accepts **only**
`{"networks":[…]}`; identity fields are refused by design.

Card types the renderer knows (`tiny_display.cpp render_*_card`): `text`
`list` `kv` `menu` `qr` `chart` `chat` `composite` `image` `gallery`
`keyboard` `agent_home`, plus `universe` (shell). Agent-facing docs list the
first eight; `docs/cards.md` is the grammar with a real-glass capture per type.

## 6. First boot (`tiny_onboard.cpp`, since 0.28.0)

The step is **derived**, never stored: no networks → `welcome`/`wifi`;
networks but STA down → `link`; STA up but no `device_id`+`token` → `pair`;
provisioned but NVS `tinywifi/ob_done` unset → `ready`; else `done`.
`tiny_shell_home()` re-enters the flow while it is active (long-press AI and
the bottom-edge swipe cannot escape into a dead agent-home). UP/DOWN inside
the flow = `ob:next`/`ob:back`. `page onboard <step>` previews any card on a
provisioned device — that is how the README screenshots were taken.

Rails it reuses: the shell's Wi-Fi scan page + keyboard (`[Type it here]` →
`TINY_PAGE_WIFI`, its `[Back]` pops to onboarding), `tiny_config_merge_json`
as the only writer, the softAP portal (now **APSTA** so on-glass scan works
while a phone uses the AP; its page is two independent forms, Wi-Fi and
pair). `tiny_main.cpp` feeds the Wi-Fi walk verdict to `tiny_onboard_note_link`
instead of painting the old confession card when the device is unprovisioned.

Known limits (Phase A): pairing still means "mint a device on
tiny.technology/devices, then paste id + token at `192.168.4.1/pair`". The
planned Phase B is an RFC 8628-style pairing code (`POST /api/devices/pair/begin`
on the device, `/pair?code=` for the owner's phone, device polls for the
token); `render_pair()` is the only function that changes.

The flow was tested end to end on 0.28.0 from an erased NVS (welcome → portal
→ Wi-Fi → link → pair → ready → home). One thing it found: `POST /setup`
overflowed the 4 KB httpd task stack while merging config — the portal's
httpd now runs on 8 KB. Not exercised: a phone camera on the QR (decoded from
the framebuffer PNG instead), on-glass keyboard join during onboarding.

Rules that bit here: a `qr` part in a scrolling composite must be sized
against the visible floor (`render_qr_card` fix in 0.28.0); every body line
costs the QR ~38 px — keep onboarding bodies to two lines; a tour that scrolls
is a manual (four items fit); never paint a live mic button from an untrusted
string (SSID injection, 0.15.1); hit-regions publish at display time; verify
each new card with `screenshot` — three of five onboarding pages needed a
fix that only the glass showed.

## 7. Release discipline (`docs/release.md`)

1. Publish builds come from a **clean detached worktree**
   (`git status --porcelain -- firmware/` empty).
2. Labels are monotonic; a burned label is never reused.
3. Receipts outrank version strings — verify by making the surface answer.
4. **Never publish a `-dirty` or unpushed `fw_commit`.** (`gen_commit.cmake`
   injects `git describe` at build time; `-dirty` is scoped to `firmware/`.)
5. Prove ancestry before publishing: `git merge-base --is-ancestor <on-glass> <build>`.
6. Claim a feature only after string-verifying the artifact.

Release rail: `tools/release.sh [--build] [--deploy]` rewrites
`docs/install/manifest.json` + `sha256sums.txt` from `firmware/build/`; the
mkdocs hook `tools/install_assets_hook.py` copies the four parts into
`site/install/fw/` and refuses bytes that don't match the sums. Tags are
`v<TINY_FW_VERSION>`.

## 8. Sticky Playground registry (Seeed) — what we ship and how

Registry: `Seeed-Projects/reterminal-sticky-playground-registry`. Layout is
`firmwares/<id>/` with a **strict** `firmware.json` (unknown keys fail
validation), `README.md`, `assets/preview.*` (real Sticky screenshot or
photo), and for firmware-only entries `firmware/<version>/manifest.json` +
`.bin` parts. Community entries: `group/catalogSection = community`,
`mode = flash`, `category` required, `author.name`, `support.url`,
`source.url` + `source.license`, `assets.previewAlt`.

`playground/tiny/` in this repo **is** that directory, verbatim. Produce it
with `tools/playground_package.sh [--build] [--validate]`: reads offsets from
IDF's `flasher_args.json`, writes sizes + sha256, rewrites
`flash.versions[0]`, refuses `-dirty`, and with `--validate` copies into a
registry clone and runs `npm run validate`. PR = copy the directory to
`firmwares/tiny/`, fill the physical-device test table in the README, use
the registry PR template (device revision, install method, main workflow,
reboot state, USB reconnect + reinstall).

Gates before the PR: repo public, license question for `vendor/` answered
(Seeed components ship with no license text; ask Seeed, do not assume), a
tagged clean build, the test record filled from a real flash of the exact
package. `PLAYGROUND.md` at the root is the short version of this section.

## 9. Docs conventions

- Voice pages (`index.md`, `start/quickstart.md`, `commands.md`, `cards.md`,
  `cookbook.md`, `stickyos.md`, `hardware.md`, `troubleshooting.md`) are
  **first person** — Sticky speaks. Ledgers (`ARCHITECTURE.md`,
  `API_CONTRACT.md`, `release.md`, `RECOVERY.md`) are third
  person. Don't mix.
- Front matter `readtime: N` and `description:` on every page. mkdocs
  builds `--strict`; a dead anchor is a red build.
- Every glass image is a real framebuffer (`screenshot` verb). No mock-ups.
  `docs/img/glass/INDEX.md` records how each capture was produced.
- Counts and pins in prose (verb count, grammar version, fw line) drift.
  When you change one, grep for the old number across `docs/` and `README.md`.
- `docs/dashboard/` is excluded from the site.

## 10. Probing a live device without lying to yourself

- `status`/`sensors` do not repaint — the right first probe. `render_ui`,
  `say`, `page`, `tap`, `swipe`, `play` claim the glass.
- The owner may be holding the device. Look at `idle_for_s`, `card_id`,
  recent `ui_tap` events before painting anything. Restore home after.
- Pending envelopes execute at **drain time**, not send time. A damning
  receipt needs a timestamp and a fresh reply; three confirmations, same
  actor, seconds apart, before calling a P0.
- A reply is not a frame. `rendered card_id=…` proves the renderer parsed;
  only a `screenshot` proves pixels.
- Rotation is runtime state: screenshot → tap coordinates must be atomic.

## 11. Things that are personal, secret, or must never be committed

- `.secrets.*.json`, `.logs/`, `recovery/*.bin`, `firmware/sdkconfig`,
  `docs/dashboard/.anim_frames/`, `.claude/` — all gitignored. Check
  `git status` before pushing; use `git add -u` or explicit paths, never
  `git add -A`.
- The device token (`tind_…`) is the only credential on the device; it lives
  in NVS, not in the repo. Never echo it in a card, a log line, or a receipt.
- `sticky.cagatay.my` is the owner's dashboard host. It appears as an
  env-overridable default (`STICKY_PUBLIC_URL`, `Kconfig TINY_DASHBOARD_URL`,
  CORS). Not a secret, but when open-sourcing, keep every occurrence
  overridable and say so.
- `tools/port.sh` embeds the owner's three boards (port, device-id prefix,
  MAC). Harmless but personal; `STICKY_PORT` overrides.

## 12. Pitfalls a new agent hits in the first hour

- **Shared worktree.** Two editors on one checkout lose hunks between the
  editor and `git add`. Build in a worktree; commit small.
- **Task stacks.** `tiny_node` runs at 16 KB because big TLS transfers
  (screenshot upload, OTA) overflowed 8 KB; `node_stack_min_free` in `status`
  is the watermark. Any new HTTP in a fresh task must budget mbedTLS.
- **Internal RAM.** `internal_min_free` (MALLOC_CAP_INTERNAL) is the number
  that kills Wi-Fi/TLS; `heap_free` is PSRAM-dominated and lies. Gate: >60 KB
  under load. mbedTLS and NimBLE pools are already in PSRAM.
- **E-ink partial refresh** compares against the last *pushed* frame; a
  rotation change forces a full mono wipe. `blit_1bit` and image cards pin
  canvas rotation to landscape.
- **UTF-8.** Regions store raw UTF-8; folding to the 5×7 font's private bank
  (0x80–0x8B Turkish glyphs) happens only at paint; wire egress transliterates
  (`wire_fold_bank`). Truncate on rune boundaries (`utf8_complete_len`).
- **Provisioning strings are hostile.** SSIDs are 32 arbitrary bytes; always
  build JSON with cJSON, never `snprintf` into a JSON template.
- **`grep -q` under `set -o pipefail`** makes the producer die of EPIPE and
  the pipeline report failure — a "refuse if found" check silently passes.
  Capture into a variable instead (see `tools/playground_package.sh`).

## 13. When you are done

Run `tests/run.sh`. If you touched `firmware/`, build it. If you touched a
verb or a reply shape, bump `TINY_GRAMMAR_VERSION` and write the CHANGELOG
line. If you changed a number that appears in prose, grep for it. If you
painted the glass, restore home. Then update this file if it lied to you.
