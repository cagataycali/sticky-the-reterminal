---
readtime: 3
description: "The 23 verbs Sticky answers to — one line each, with what the receipt tells you."
---

# Commands — everything I answer to

Twenty-three verbs, each dispatched in `tiny_node.cpp`, and my heartbeat
**advertises what I implement** (one drift, below). Envelopes are
`{type:"invoke", prompt:"<verb> [args]"}`; I always reply — unknown input gets
the help string, never silence. From any tiny surface:

```
use_device invoke → prompt: "status"
```

<figure class="glass" markdown>
  <div class="glass__bezel"><img src="../img/glass/say.png" alt="say — one text card titled *tiny*, an ack chime, no agent turn. What you sent is exactly what I painted." width="800" height="480" loading="eager" decoding="async"></div>
  <figcaption markdown="span">`say` — one text card titled *tiny*, an ack chime, no agent turn. What you sent is exactly what I painted.</figcaption>
</figure>

## The verbs

| Verb | Arguments | What happens | The receipt says |
|---|---|---|---|
| `render_ui` | [card spec](cards.md) JSON | paints a card; buttons become touch regions | `rendered card_id=<id>` — what the panel *committed* |
| `say` | text | text card titled *tiny*, ack chime, no agent turn | `ok` |
| `screenshot` | — | framebuffer → hosted PNG | the URL. Refused during `play` |
| `page` | <code>home&vert;<wbr>status&vert;<wbr>sensors&vert;<wbr>settings&vert;<wbr>wifi&vert;<wbr>ble&vert;<wbr>back&vert;<wbr>next&vert;<wbr>prev</code> | the relay twin of every tap path in [stickyOS](stickyos.md) | card id |
| `rotate` | <code>0&vert;<wbr>90&vert;<wbr>180&vert;<wbr>270&vert;<wbr>auto</code> | holds an orientation against gravity; `auto` returns it to the IMU | rotation; screenshot and touch stay in panel space |
| `tap` | `x y` (panel coords) | synthetic touch through the resolver a finger uses | `rotation`, `result`, `card_id` — never tap from a stale screenshot |
| `swipe` | `x0 y0 x1 y1` | synthetic swipe through the real release classifier | `accepted` / `routed` / `route`; travel inside the 24 px slop is refused, not downgraded to a tap |
| `scroll` | <code>up&vert;<wbr>down&vert;<wbr>top&vert;<wbr>±px</code> | steps ⅔ of a view; bare form reports | `offset`, `content_h`, `view_h`, `scrollable` |
| `glance` | — | redraws the current card via *partial* refresh, no network | `refresh:"partial"`, battery, wifi, unread, locked |
| `status` | — | machine JSON with a human `summary` inside | `fw`, `fw_commit`, `grammar_version`, `battery_pct`, `rssi_dbm`, `card_id`, `locked`, `last_boot` |
| `sensors` | `[card]` | live I²C read: SHT40, IMU, RTC, BQ27220; `card` also paints the page | JSON. **Read `mah_trusted` first** — the gauge claims 7500 mAh on a 750 mAh pack |
| `miccheck` | `[seconds]` | records PDM audio, replies levels instead of uploading | <code>verdict:"live&vert;<wbr>SILENT"</code>, `rms`, `peak` |
| `ask` | text | a full owner-scoped agent turn — tools, memory, other devices — streamed onto home | the answer. One at a time; a second is refused |
| `voice` | `[seconds]` | records, uploads, then `ask` with `audioUrl` — the AI button's path | the answer, or "didn't catch that" on 422 |
| `agent` | <code>[slug &vert; list &vert; clear &vert; add&vert;<wbr>rm &lt;slug&gt;]</code> | which tiny answers my asks; persists in NVS | `active`, `roster` |
| `messages` | <code>[unread &vert; thread &lt;login&gt;]</code> | paints the inbox or a thread; `unread` answers counts and paints **nothing** | counts, or card id |
| `lock` / `unlock` | — | pocket lockout: touch discarded, AI button refused, every mic path dead at one funnel | `locked` |
| `sleep` | `[seconds]` | reply first, goodbye card, then deep sleep at 10&ndash;20 µA; AI button wakes | ok, sent while the radio is up |
| `play` | `<manifest_url> [interval_s] [max_frames]` / `stop` / `status` | frames prefetched to PSRAM *before* the ack; a pure metronome paints them | `status` holds the last run's verdict |
| `config` | <code>{"networks":[{ssid,password} &vert; {ssid,forget:true}]}</code> | merges a Wi-Fi roaming list into NVS; **identity fields are refused** | counts only — keys never echoed, log prints `<redacted>` |
| `ota` | `[channel]` | sha256-verified stage, reply, *then* reboot into a rollback-armed trial | ok, or a downgrade refusal (`force:"1"` overrides, loudly) |
| `sd` | <code>[status &vert; df &vert; ls [path] &vert; probe &vert; format yes]</code> | the microSD's own verb (0.26); same shape as the `sd` object in `status` | JSON, or `no card`; `format` without `yes` is refused |

!!! note "Parity, restored in 0.28.0"
    **23 dispatched, 23 advertised.** Between 0.26.0 and 0.27.x `sd` dispatched
    but was missing from the heartbeat `caps[]` array, so nothing offered it.
    0.28.0 (grammar v12) added it. The rule stands: advertise exactly what
    dispatches — and this box is the receipt that it was once broken.

## Two `status` fields worth knowing

- **`internal_min_free`** — `heap_free` counts PSRAM and once said 8.2 MB free
  while I died of internal-RAM exhaustion; a TLS handshake needs ~40 KB.
- **`grammar_version`** — the integer consumers gate on; a version *string* can lie.

Lockout guards *my inputs*, not my owner's reach: `say`, `render_ui` and
`screenshot` work while locked. Chord: [stickyOS](stickyos.md).

## Not in the firmware

`wake` (a sleeping radio hears nothing — button or timer only) · `reprovision`
(the rescue portal opens itself) · `press` (no synthetic key twin yet).

## Reply plumbing

<figure class="glass" markdown>
  <div class="glass__bezel"><img src="../img/glass/ask.png" alt="ask — the answer streams onto my glass word by word; the receipt is the same text, so you can check I did not paraphrase." width="800" height="480" loading="lazy" decoding="async"></div>
  <figcaption markdown="span">`ask` — the answer streams onto my glass word by word; the receipt is the same text, so you can check I did not paraphrase.</figcaption>
</figure>

Replies go `PATCH /api/devices/relay` with `inReplyTo`; the payload is a
JSON string capped at 8 KB, images as hosted URLs. One `401` never wipes my
identity — three in a row trip an auth-halt and the rescue portal.
Exact shapes: [API contract](API_CONTRACT.md).
