---
title: Install
description: Put the tiny firmware on a reTerminal Sticky from your browser, or with one command.
readtime: 2
---

# Install

A reTerminal Sticky, a USB **data** cable, five minutes. Nothing to build,
nothing to sign up for yet. Two steps: put me on the board, then give me a brain.

## 1 · Put me on the board

<div class="install-hero" markdown>

<div class="install-card" markdown>

### In your browser

Chrome or Edge on a laptop — a phone cannot do this. Plug me in, press the
button, and pick the port called **USB Single Serial**. That is my CH343
bridge; macOS, Linux and Windows 10+ need no driver for it.

<div class="install-button-row">
  <esp-web-install-button manifest="manifest.json">
    <button slot="activate" class="md-button md-button--primary install-cta">Install Sticky <small>v0.28.0</small></button>
    <span slot="unsupported" class="install-note">This browser has no Web Serial. Use Chrome or Edge on a laptop — or the one-command path beside this.</span>
    <span slot="not-allowed" class="install-note">Web Serial needs HTTPS — you are on the right page, so this should not happen; reload.</span>
  </esp-web-install-button>
</div>

A minute or two. Keep the cable in until the dialog says done; I reboot on
my own.

</div>

<div class="install-card" markdown>

### Or one command

macOS or Linux. It finds `esptool` (or installs it for your user), checks
every byte against `sha256sums.txt`, and flashes only if there is **exactly
one** Sticky-class USB bridge (WCH `1a86:55d3`) on the bus — it never guesses
a port.

```sh
curl -fsSL https://cagataycali.github.io/sticky-the-reterminal/install.sh | sh
```

Read it before you run it: [`install.sh`](install.sh). Rehearse without
touching the board with `STICKY_DRY_RUN=1`; pin a port with
`STICKY_PORT=<port>`.

</div>

</div>

!!! warning "The first install erases the whole flash"
    A factory Sticky carries Seeed's demo, and my [NVS layout](../API_CONTRACT.md)
    starts empty — so the first flash wipes everything and starts clean. That is
    the honest default, not an accident. If you may ever want the factory image
    back, [dump it first](../start/quickstart.md#0-back-up-the-factory-brain-once-45-min);
    it needs the same cable and about 45 minutes — 32 MB at the one baud rate
    that never drops a byte.

## 2 · Give me a brain

My intelligence is not on the board. I am a 70-gram body for **your** tiny at
[tiny.technology](https://tiny.technology); the account is what makes me speak.

1. After the flash I open a Wi-Fi network called **`tiny-XXXX`**, key **`tinysetup`**.
2. Join it and open **http://192.168.4.1**.
3. On tiny.technology go to **Devices → Add**, copy the device token, and paste it
   into my form together with your home Wi-Fi. I reboot, join your network, and
   draw my first card.

From here on I update myself over the air; the cable is only for emergencies.
The full walk, including the rescue path, is the [Quickstart](../start/quickstart.md).

## What ships

Four parts, one image. Same binary on all three of my siblings — identity lives
in NVS, not in the image.

| part | offset | bytes |
|---|---|---|
| `bootloader.bin` | `0x0` | 21 088 |
| `partition-table.bin` | `0x8000` | 3 072 |
| `ota_data_initial.bin` | `0xf000` | 8 192 |
| `tiny_sticky.bin` | `0x20000` | 1 519 312 |

[`manifest.json`](manifest.json) · [`sha256sums.txt`](sha256sums.txt) · built by
[`tools/release.sh`](https://github.com/cagataycali/sticky-the-reterminal/blob/main/tools/release.sh)
from `firmware/` — 32 MB flash, dio @ 80 MHz.

<script type="module" src="../js/esp-web-tools/install-button.js"></script>
