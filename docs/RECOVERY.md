---
readtime: 3
description: "How to put a reTerminal Sticky back to its factory firmware — the byte-exact dump, Seeed's official image, and why the ESP32-S3 cannot be bricked by an app flash."
---

# Recovery — back to stock, always

**Rule zero: the ESP32-S3 cannot be bricked by an app flash.** The ROM serial bootloader lives in mask ROM. If the firmware is broken, hold the reset-hole strapping combo (or just let a bad app crash-loop — esptool can still sync during resets) and talk to `$STICKY_PORT` (see below).

## Path 1 — our byte-exact dump (preferred: restores THIS unit, NVS/SN/calibration included)

Created on arrival day from the as-shipped device:

```bash
# dump (how recovery/stock-firmware-32MB.bin was made) — ~45 min at default baud
# NOTE: 921600 fails through the USB hub chain ("serial noise"); default 115200 is reliable
esptool --port $STICKY_PORT read-flash 0 0x2000000 stock-firmware-32MB.bin

# restore
esptool --port $STICKY_PORT --baud 460800 write-flash 0 stock-firmware-32MB.bin
```

`$STICKY_PORT` is your board's serial port (`ls /dev/cu.usbmodem*`; Linux `/dev/ttyACM0`) — my CH343 bridge shows up as "USB Single Serial" and so do other boards, so if there is more than one candidate, unplug them rather than guess. The sha256 of my owner's dump is recorded in `recovery/stock-firmware-32MB.bin.sha256`; the 32 MB bin itself is gitignored — make your own before you flash me, it's the only copy of *your* board's factory state.

## Path 2 — official factory image (browser, zero tooling)

Seeed ships stock as a playground-registry integration: **`sticky-factory` v1.1.0**, one full 32MB image at offset 0, sha256 `8b149c809b7c07c680a59ab5621d7bd29ca85a8c1c9e9060b0040b387b4517e4` (`recovery/factory-manifest.json`).

1. Chrome/Edge → Sticky Playground (seeedstudio.com/sticky → Playground) → *reTerminal Sticky Official Firmware*
2. USB data cable, pick the serial port, **choose the erase option** (their own note: required when coming back from community/custom firmware)
3. flash, reboot → stock pairing screen → re-bind in Seeedash

## Path 3 — official image by hand

```bash
curl -LO https://raw.githubusercontent.com/Seeed-Projects/reterminal-sticky-playground-registry/main/integrations/sticky-factory/firmware/1.1.0/reterminal_sticky_1_1_0.bin
shasum -a 256 reterminal_sticky_1_1_0.bin   # must be 8b149c…17e4
esptool --port $STICKY_PORT --baud 460800 erase-flash
esptool --port $STICKY_PORT --baud 460800 write-flash 0 reterminal_sticky_1_1_0.bin
```

## While developing

- our partition table keeps a `factory` slot with the last-known-good tiny build; OTA goes to `ota_0/ota_1` with trial-boot + rollback (`tiny_boot`), so remote updates self-revert
- USB flashing always available regardless of what OTA did
- before ANY first `idf.py flash`: confirm `recovery/stock-firmware-32MB.bin` exists and its sha256 file matches
