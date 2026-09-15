# Contributing

## Toolchain

- ESP-IDF **v5.4** installed at `~/esp/esp-idf-v5.4` (any path works if you `source
  <idf>/export.sh` yourself). Target: ESP32-S3, 32 MB flash, 8 MB PSRAM.
- Python 3 and `esptool` come with IDF. A C++17 host compiler (`c++`) runs the tests.

## Build

```sh
source ~/esp/esp-idf-v5.4/export.sh
cd firmware && idf.py build
```

`firmware/CMakeLists.txt` reads the version from `firmware/main/tiny/tiny_version.h`
and pulls the hardware drivers from `vendor/` — do not edit `vendor/`.

## Flash and watch

```sh
STICKY_PORT=/dev/cu.usbmodemXXXX tools/flash.sh     # incremental: app only
STICKY_PORT=/dev/cu.usbmodemXXXX tools/first_flash.sh   # blank board: bootloader + table + app
STICKY_PORT=/dev/cu.usbmodemXXXX tools/monitor.sh
```

`tools/port.sh` resolves the port; it refuses to guess when more than one serial
adapter is plugged in. Enrolling a board into a tiny.technology account is
`tools/provision_new.sh` (see its header).

## Tests

```sh
tests/run.sh
```

Host tests compile shipped firmware files against the shims in `tests/host/shim`
— no device needed. Keep them green; add a case when you fix a bug.

## Pull requests

- One change per PR, small enough to review in ten minutes. Say what and why.
- Firmware changes must build; anything touching a verb or reply shape bumps
  `TINY_GRAMMAR_VERSION` and gets a line in `CHANGELOG.md`.
- No build logs, receipts, tokens or your own WiFi in the tree. `.gitignore`
  already covers the usual places; `git status` before you push.
- Less code is more code. Deleting is a contribution.
