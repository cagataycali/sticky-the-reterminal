# Changelog

Firmware versions come from `firmware/main/tiny/tiny_version.h`; the verb
grammar version (`TINY_GRAMMAR_VERSION`) is what clients gate on. One line per
release, newest first.

## 0.28.0 — grammar v12 — first-run onboarding

- **firmware** — a real first boot. New `tiny_onboard` module: welcome →
  wi-fi (phone QR to the portal, or the on-glass scan list + keyboard) →
  link (honest join verdict in-flow) → pair → a one-screen tour, then home.
  The step is derived from device state, never stored as a cursor. `home`
  (long-press AI, bottom-edge swipe) re-enters the flow until the device is
  paired and the tour dismissed (`ob_done` in NVS). UP/DOWN inside the flow
  = next/back. Touch namespace `ob:*`, card_ids `ob-*`.
- **firmware** — the provisioning portal comes up in **APSTA** so the
  on-glass Wi-Fi scan works while a phone talks to the AP; its page is two
  forms (Wi-Fi alone, or device id + token alone — each a valid step) with
  scanned networks as a datalist; `/pair` anchors the second form. The portal
  no longer paints its own card; the shell owns the glass. The pre-0.28 card
  promised "open the tiny app", which cannot onboard a Sticky (the app
  provisions Nicla over BLE only) — the copy now says what actually works.
- **firmware** — `render_qr_card` inside a scrolling composite: sized against
  the visible floor, top-aligned, real end-y. Before, `max_y` was the
  measuring horizon (1<<28), so any `qr` part in a composite painted
  off-canvas and left a phantom scrollbar. Whole `qr` cards were unaffected.
- **firmware** — the portal's httpd task runs on 8 KB, not the 4 KB default.
  The real first-boot test overflowed it on the pair form (`***ERROR*** A
  stack overflow in task httpd`): `tiny_config_merge_json` keeps two
  `tiny_config_t` on the caller's stack. Portal provisioning had only ever
  worked by luck. Also `box-sizing` on the portal page (form overflowed a
  390 px phone by 26 px).
- **grammar v12** — `page onboard [welcome|wifi|link|pair|ready]` previews
  any first-run card (screenshots for the registry README come from here);
  `status` gains `onboard:"<step>"`; heartbeat `capabilities[]` gains `sd`
  (dispatched since 0.26.0, never advertised — 23 = 23 now).
- **repo** — `AGENTS.md` (the map for agents), `playground/tiny/` (Seeed
  Sticky Playground registry entry, validated with Seeed's `npm run validate`),
  `tools/playground_package.sh` (refuses `-dirty` builds). `PLAYGROUND.md`
  rewritten for the registry's real `firmwares/<id>/` layout.

## Unreleased

- **repo** — the tree is firmware-only: the parametric case moved out so
  the site and the .bin are the only surface. Playground submission
  metadata lives in `PLAYGROUND.md`.

- **docs** — Quickstart leads with the web installer (browser button + `curl … | sh`);
  ESP-IDF moved to an optional "Build it yourself". The verb count is **23**
  everywhere (the `dispatch_inner()` table), with the `sd` drift stated plainly:
  23 dispatched, 22 advertised in the heartbeat `caps[]` array — a firmware fix,
  still open. `$STICKY_PORT` is defined once and carries the CH343 "USB Single
  Serial" ambiguity note; the owner's own port no longer appears in RECOVERY.
  `og:image`/`twitter:image` lose a doubled slash. Deep sleep is described
  honestly as ~0 mA = 10–20 µA (e-ink's zero-draw *hold* claim unchanged).
  The landing wears the same `sk-header`/`sk-footer` chrome as every inner page.

- **0.27.0-u2** (2026-08-29) — repair a duplicated tail in `tiny_touch.cpp`; builds green.
- **0.27.0-u1** — bounded `%u` on roster NVS keys (format-truncation fix).
- **0.27.0** (grammar 11) — the universe on the glass: `agent` verb family, universe card, `u:` touch namespace, `@slug` home badge, honest refusal rendering.
- **0.26.0** — microSD on the shared SPI2 bus: `/sdcard` mount, `sd` verb (status/ls/df/probe/format), gallery frames and photos archived to the card; 128 GB proven.
- **0.25.x** (grammar 9–10) — gallery card + `g:` namespace, composite scroll rail, power-latch hold through runtime, RTC black-box crumb, stack-overflow fix in `tiny_node`, device DM send fixed, ASCII fold of private font bytes at the wire.
- **0.24.0** — silent mode (buzzer funnel, NVS-persisted) and a batch of settings/status fixes.
- **0.22.0** (grammar 9) — `config` verb: WiFi roaming list in NVS, 30 s re-roam task, settings QR.
- **0.21.0** — ask lives on the home surface; keyboard v2 with symbols plane.
- **0.20.0** — agent-home card: the home screen is the agent UI.
- **0.19.0** (grammar 7) — image card: server-packed 1-bit / 4-gray canvas over HTTPS.
- **0.18.0** (grammar 6) — glance: title-bar cluster (unread, lock) redrawn by partial refresh.
- **0.17.0** — pocket lockout: lock module, UP+DOWN unlock chord, input gates.
- **0.16.2** (grammar 4) — `swipe` verb for gesture injection.
- **0.15.x** (grammar 3) — streamed asks render live on the glass; chat card.
- **0.14.x** (grammar 1–2) — the 14-verb grammar, body scroll, DM surface (`messages`), `fw_commit` in status.
