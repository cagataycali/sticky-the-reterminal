#!/bin/bash
# playground_package.sh — turn firmware/build into a Sticky Playground registry entry.
#
#   tools/playground_package.sh                 # package firmware/build as-is
#   tools/playground_package.sh --build         # idf.py build first (ESP-IDF exported or at ~/esp/esp-idf-v5.4)
#   tools/playground_package.sh --validate      # …then copy into a registry clone and run its validator
#   tools/playground_package.sh --registry DIR  # where the registry clone lives (default /tmp/sticky-registry)
#
# Output (in this repo, committed):
#   playground/<id>/firmware/<version>/{bootloader,partition-table,ota_data_initial,tiny_sticky}.bin
#   playground/<id>/firmware/<version>/manifest.json      exact registry schema, sizes + sha256 filled
#   playground/<id>/firmware.json                          flash.versions[0] rewritten to this version
#
# The registry (Seeed-Projects/reterminal-sticky-playground-registry) wants
# firmwares/<id>/… — `playground/<id>/` here is that directory verbatim, so the
# PR is `cp -R playground/<id> <registry>/firmwares/<id>`. Nothing else.
#
# Truth rules (docs/release.md): the build must carry the version in
# tiny_version.h, must not be -dirty, and offsets come from IDF's own
# flasher_args.json — never typed by hand.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$DIR/firmware/build"
PG="$DIR/playground"
ID="$(ls "$PG" | grep -v '^\.' | grep -v -i readme | head -1)"
[ -n "$ID" ] || { echo "package: no playground/<id>/ directory" >&2; exit 1; }
ENTRY="$PG/$ID"
VER=$(sed -n 's/.*TINY_FW_VERSION *"\([^"]*\)".*/\1/p' "$DIR/firmware/main/tiny/tiny_version.h")
[ -n "$VER" ] || { echo "package: cannot read TINY_FW_VERSION" >&2; exit 1; }

DO_BUILD=0; DO_VALIDATE=0; REGISTRY="/tmp/sticky-registry"
while [ $# -gt 0 ]; do
  case "$1" in
    --build) DO_BUILD=1 ;;
    --validate) DO_VALIDATE=1 ;;
    --registry) shift; REGISTRY="$1" ;;
    *) echo "package: unknown flag $1" >&2; exit 2 ;;
  esac
  shift
done

build_matches() {
  [ -f "$BUILD/flasher_args.json" ] && [ -f "$BUILD/tiny_sticky.bin" ] \
    && grep -aq "$VER" "$BUILD/tiny_sticky.bin"
}

if [ "$DO_BUILD" = 1 ] || ! build_matches; then
  echo "package: building $VER"
  # shellcheck disable=SC1090
  [ -n "${IDF_PATH:-}" ] || source ~/esp/esp-idf-v5.4/export.sh >/dev/null 2>&1 \
    || { echo "package: ESP-IDF not exported and ~/esp/esp-idf-v5.4 missing" >&2; exit 1; }
  ( cd "$DIR/firmware" && idf.py reconfigure >/dev/null && idf.py build )
  build_matches || { echo "package: build does not contain $VER" >&2; exit 1; }
fi

# Rule 4: never publish a -dirty artifact. The sha is embedded by gen_commit.cmake.
# (No `grep -q` here: under pipefail, grep exiting early makes `strings` die of
# EPIPE and the pipeline reports failure — the check would silently pass.)
DIRTY_SHA=$(strings "$BUILD/tiny_sticky.bin" | grep -E '^[0-9a-f]{7,8}-dirty$' || true)
if [ -n "$DIRTY_SHA" ]; then
  echo "package: REFUSED — tiny_sticky.bin carries a -dirty fw_commit ($DIRTY_SHA)." >&2
  echo "         Commit firmware/ and rebuild (tools/playground_package.sh --build)." >&2
  exit 1
fi
if [ -n "$(git -C "$DIR" status --porcelain -- firmware/main firmware/CMakeLists.txt firmware/partitions.csv firmware/sdkconfig.defaults)" ]; then
  echo "package: REFUSED — firmware/ has uncommitted changes (docs/release.md rule 1)." >&2
  exit 1
fi

OUT="$ENTRY/firmware/$VER"
rm -rf "$OUT"; mkdir -p "$OUT"

# Copy every part IDF says it flashes, keeping IDF's basenames.
python3 - "$BUILD" "$OUT" "$ENTRY/firmware.json" "$VER" <<'PY'
import hashlib, json, os, shutil, sys
build, out, fwjson_path, ver = sys.argv[1:5]
fa = json.load(open(os.path.join(build, "flasher_args.json")))
settings = fa.get("flash_settings", {})
parts = []
for off_hex, rel in sorted(fa["flash_files"].items(), key=lambda kv: int(kv[0], 16)):
    src = os.path.join(build, rel)
    name = os.path.basename(rel)
    shutil.copyfile(src, os.path.join(out, name))
    data = open(src, "rb").read()
    parts.append({"path": name, "offset": int(off_hex, 16), "size": len(data),
                  "sha256": hashlib.sha256(data).hexdigest()})
# overlap check, same rule the registry validator applies
for a, b in zip(parts, parts[1:]):
    assert a["offset"] + a["size"] <= b["offset"], f"{a['path']} overlaps {b['path']}"
fw = json.load(open(fwjson_path))
manifest = {
    "name": fw["name"],
    "version": ver,
    "flashSize": settings.get("flash_size", "32MB"),
    "flashMode": settings.get("flash_mode", "dio"),
    "flashFreq": settings.get("flash_freq", "80m"),
    "baudRate": 460800,
    "new_install_prompt_erase": True,
    "builds": [{"chipFamily": "ESP32-S3", "parts": parts}],
}
json.dump(manifest, open(os.path.join(out, "manifest.json"), "w"), indent=2)
open(os.path.join(out, "manifest.json"), "a").write("\n")
# firmware.json: this version becomes versions[0]; older entries kept.
versions = [v for v in fw["flash"]["versions"] if v.get("version") != ver]
channel = "experimental" if fw.get("status") == "experimental" else fw.get("status", "beta")
versions.insert(0, {"version": ver, "channel": channel, "manifestPath": f"firmware/{ver}/manifest.json"})
fw["flash"]["versions"] = versions
json.dump(fw, open(fwjson_path, "w"), indent=2, ensure_ascii=False)
open(fwjson_path, "a").write("\n")
print(f"package: {len(parts)} parts -> {out}")
for p in parts:
    print(f"  0x{p['offset']:06x}  {p['size']:>8}  {p['sha256'][:16]}…  {p['path']}")
PY

# Keep only version directories firmware.json still references.
python3 - "$ENTRY" <<'PY'
import json, os, shutil, sys
entry = sys.argv[1]
fw = json.load(open(os.path.join(entry, "firmware.json")))
keep = {v["version"] for v in fw["flash"]["versions"] if "manifestPath" in v}
fwdir = os.path.join(entry, "firmware")
for d in os.listdir(fwdir):
    if d not in keep and os.path.isdir(os.path.join(fwdir, d)):
        shutil.rmtree(os.path.join(fwdir, d)); print(f"package: dropped unreferenced firmware/{d}")
PY

if [ "$DO_VALIDATE" = 1 ]; then
  [ -d "$REGISTRY/firmwares" ] || git clone -q --depth 1 \
      https://github.com/Seeed-Projects/reterminal-sticky-playground-registry.git "$REGISTRY"
  rm -rf "$REGISTRY/firmwares/$ID"; cp -R "$ENTRY" "$REGISTRY/firmwares/$ID"
  ( cd "$REGISTRY" && [ -d node_modules ] || true
    echo "package: validating in $REGISTRY"; npm run --silent validate )
fi
echo "package: OK — $ID $VER"
