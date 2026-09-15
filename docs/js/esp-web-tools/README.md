# vendored: esp-web-tools 10.4.0 (Apache-2.0)

`dist/web/*` of https://github.com/esphome/esp-web-tools — the `<esp-web-install-button>`
element the Install page uses. Vendored so the site has zero third-party script origins;
the browser fetches only the chunks for the chip it finds (ESP32-S3 + its stub).
Update: `tools/release.sh --vendor-ewt` (downloads the npm tarball, replaces this dir).
