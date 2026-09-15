# vendor/ — third-party code, unmodified

## Sticky_dashboard_demo/components

Seeed Studio's official ESP-IDF v5.4 demo for the reTerminal Sticky, published on
the [ESP-IDF basics](https://www.seeedstudio.com/sticky/docs/en/device-guide/esp-basics/)
guide as
[Sticky_dashboard_demo.zip](https://files.seeedstudio.com/wiki/reterminal_sticky/res/Sticky_dashboard_demo.zip)
(SHA-256 `f52918c42411f73375db1125b4c5ee0fc8691d8b8523c26d3c8aab7e8a256101`).
Only the hardware components are kept — `firmware/CMakeLists.txt` adds this
directory via `EXTRA_COMPONENT_DIRS`; the demo application (`main/`) is not used
and was removed. Files are byte-identical to the zip. Fix bugs upstream or in
`firmware/main`, not here.

| component | what | license |
| --- | --- | --- |
| `seeed_epaper` | SSD1677 / UC8179 e-paper panel driver | not stated by Seeed |
| `gt911` | GT911 capacitive touch controller | not stated by Seeed |
| `bq27220` | TI BQ27220 battery gauge over I²C | not stated by Seeed |
| `debug_logging` | Kconfig-gated log macros | not stated by Seeed |
| `button` | Espressif `iot_button` 4.1.6 from esp-iot-solution | Apache-2.0 (`button/license.txt`) |

The Seeed-authored components ship with no license text or copyright header.
They are © Seeed Technology Co., Ltd. and are redistributed here as received,
for use with the hardware they were published for. They are **not** covered by
this repository's Apache-2.0 `LICENSE`. If you need certainty about
redistribution terms, ask Seeed; do not assume.
