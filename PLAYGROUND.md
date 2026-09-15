# Playground submission — reTerminal Sticky

The [Seeed Sticky Playground](https://www.seeedstudio.com/sticky/playground/firmware/)
lists community firmware from the
[`Seeed-Projects/reterminal-sticky-playground-registry`](https://github.com/Seeed-Projects/reterminal-sticky-playground-registry)
repo. Our entry lives in this repo as **`playground/tiny/`** — the registry's
`firmwares/tiny/` directory, verbatim, so the PR is a copy.

```
playground/tiny/
  firmware.json            strict schema (schemas/firmware.schema.json) — unknown keys fail
  README.md                what it does, install, controls, physical-device test record
  assets/preview.png       a real framebuffer (screenshot verb), never a mock-up
  assets/*.png             extra glass captures the README embeds
  firmware/<version>/      manifest.json + the four .bin parts — GENERATED, gitignored
```

## Produce the package

```sh
tools/playground_package.sh --build --validate
```

- builds `firmware/` (ESP-IDF v5.4) unless `firmware/build/` already carries
  `TINY_FW_VERSION`;
- **refuses** a `-dirty` `fw_commit` or uncommitted `firmware/` (docs/release.md
  rules 1 and 4);
- copies the parts IDF's own `flasher_args.json` flashes, at their offsets
  (`0x0` bootloader · `0x8000` partition table · `0xf000` ota_data ·
  `0x20000` app), writes sizes + SHA-256 into `manifest.json`, and makes this
  version `flash.versions[0]` in `firmware.json`;
- with `--validate`, copies the entry into a registry clone
  (`/tmp/sticky-registry`, cloned if missing) and runs Seeed's
  `npm run validate`.

The same bytes are what `tools/release.sh` publishes to the web installer, so
`docs/install/sha256sums.txt` and `playground/tiny/firmware/<ver>/manifest.json`
must agree. If they don't, one of them was built from a different tree.

## Submit

1. Tag the commit the package was built from: `git tag v<version>` (the
   README's *Source commit* line is `fw_commit` from `status`).
2. `cp -R playground/tiny <registry>/firmwares/tiny` on a branch off upstream
   `main`; `npm test && npm run validate`.
3. Flash the **exact package** to a Sticky (esptool at the manifest offsets,
   or the registry's own browser flasher from a local build of the site) and
   fill every `_pending_` row in `playground/tiny/README.md`.
4. Open the PR with the registry template: *Firmware: new community firmware*,
   *Firmware-only package*, physical-device test fields. Title
   `Add tiny <version>`.

## Gates before the first PR

- [ ] this repository is public (the registry links `source.url`)
- [ ] `vendor/` terms: Seeed's `seeed_epaper`/`gt911`/`bq27220`/`debug_logging`
      ship without a license text — ask Seeed (meilily) before claiming
      Apache-2.0 covers the tree; `vendor/README.md` states the situation
- [ ] a clean, tagged build (`fw_commit` == tag commit, no `-dirty`)
- [ ] `assets/preview.png` refreshed from the shipped version's glass
- [ ] physical-device test record filled from the exact package

## What this repo does *not* ship there

- **A case.** The parametric OpenSCAD case (bumper + wrap + TPU sleeve) is
  not in this repo; Playground has a separate *3D Printables* category for it.
- **The dashboard.** `docs/dashboard/` is an owner-side product, not firmware.
