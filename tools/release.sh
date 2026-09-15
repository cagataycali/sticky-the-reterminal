#!/bin/bash
# release.sh — turn firmware/build into a web-installable release.
#
#   tools/release.sh            # reuse firmware/build if its version matches tiny_version.h
#   tools/release.sh --build    # force `idf.py build` first (needs ESP-IDF exported)
#   tools/release.sh --deploy   # …then `mkdocs gh-deploy --force` (binaries ride along)
#   tools/release.sh --vendor-ewt  # refresh docs/js/esp-web-tools from npm (Apache-2.0)
#
# Produces (committed, small):
#   docs/install/manifest.json     ESP Web Tools manifest, parts under fw/
#   docs/install/sha256sums.txt    one line per part, sha256sum(1) format
# The 4 binaries themselves are NOT committed. tools/install_assets_hook.py copies
# them from firmware/build into site/install/fw/ at mkdocs build time, so a
# `mkdocs gh-deploy` publishes exactly the bytes the manifest describes.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$DIR/firmware/build"
OUT="$DIR/docs/install"
VER=$(sed -n 's/.*TINY_FW_VERSION *"\([^"]*\)".*/\1/p' "$DIR/firmware/main/tiny/tiny_version.h")
[ -n "$VER" ] || { echo "release: cannot read TINY_FW_VERSION" >&2; exit 1; }

DO_BUILD=0; DO_DEPLOY=0; DO_VENDOR=0
for a in "$@"; do
  case "$a" in
    --build) DO_BUILD=1 ;;
    --deploy) DO_DEPLOY=1 ;;
    --vendor-ewt) DO_VENDOR=1 ;;
    *) echo "release: unknown flag $a" >&2; exit 2 ;;
  esac
done

# Parts: offset → file, straight from IDF's own flasher_args.json (single source).
parts_json() {
  python3 - "$BUILD/flasher_args.json" <<'PY'
import json, sys
fa = json.load(open(sys.argv[1]))
items = sorted(fa["flash_files"].items(), key=lambda kv: int(kv[0], 16))
print(json.dumps([{"offset": k, "file": v} for k, v in items]))
PY
}

# Does the build dir carry this version? IDF writes the app descriptor into the
# binary; `esptool image_info` is heavy, so grep the version string instead.
build_matches() {
  [ -f "$BUILD/flasher_args.json" ] && [ -f "$BUILD/tiny_sticky.bin" ] \
    && grep -aq "$VER" "$BUILD/tiny_sticky.bin"
}

if [ "$DO_BUILD" = 1 ] || ! build_matches; then
  echo "release: building $VER (idf.py build)"
  # shellcheck disable=SC1090
  [ -n "${IDF_PATH:-}" ] || source ~/esp/esp-idf-v5.4/export.sh >/dev/null 2>&1 \
    || { echo "release: ESP-IDF not exported and ~/esp/esp-idf-v5.4 missing" >&2; exit 1; }
  ( cd "$DIR/firmware" && idf.py reconfigure >/dev/null && idf.py build )
  build_matches || { echo "release: build does not contain $VER" >&2; exit 1; }
else
  echo "release: firmware/build already carries $VER — reusing"
fi

mkdir -p "$OUT"
PARTS=$(parts_json)
python3 - "$BUILD" "$OUT" "$VER" "$PARTS" <<'PY'
import hashlib, json, os, sys
build, out, ver, parts = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4])
manifest_parts, sums = [], []
for p in parts:
    src = os.path.join(build, p["file"])
    name = os.path.basename(p["file"])
    data = open(src, "rb").read()
    manifest_parts.append({"path": f"fw/{name}", "offset": int(p["offset"], 16)})
    sums.append(f"{hashlib.sha256(data).hexdigest()}  fw/{name}\n")
    print(f"release:   {p['offset']:>8}  {len(data):>9} B  fw/{name}")
manifest = {
    "name": "Sticky",
    "version": ver,
    "home_assistant_domain": None,
    "new_install_prompt_erase": True,
    "new_install_improv_wait_time": 0,
    "builds": [{
        "chipFamily": "ESP32-S3",
        "parts": manifest_parts,
    }],
}
manifest = {k: v for k, v in manifest.items() if v is not None}
with open(os.path.join(out, "manifest.json"), "w") as f:
    json.dump(manifest, f, indent=2); f.write("\n")
with open(os.path.join(out, "sha256sums.txt"), "w") as f:
    f.writelines(sums)
print(f"release: wrote {out}/manifest.json + sha256sums.txt for {ver}")
PY

# The two "Install Sticky <small>vX</small>" buttons are the only version strings
# a human reads on the site. They are static HTML, so rewrite them here — 0.28.0
# shipped with the buttons still saying 0.27.0-u2 because nothing did.
for f in "$DIR/docs/overrides/home.html" "$DIR/docs/install/index.md"; do
  sed -i.bak -E "s#(Install Sticky <small>)v[^<]*(</small>)#\1v$VER\2#" "$f" && rm -f "$f.bak"
done
grep -q "<small>v$VER</small>" "$DIR/docs/install/index.md" || { echo "release: install button did not take v$VER" >&2; exit 1; }
echo "release: install buttons say v$VER"

if [ "$DO_VENDOR" = 1 ]; then
  # refresh the vendored ESP Web Tools bundle (Apache-2.0) used by docs/install/index.md
  EWT="$DIR/docs/js/esp-web-tools"; T=$(mktemp -d)
  TARBALL=$(curl -fsSL https://registry.npmjs.org/esp-web-tools/latest | python3 -c 'import json,sys;print(json.load(sys.stdin)["dist"]["tarball"])')
  curl -fsSL "$TARBALL" | tar xz -C "$T"
  find "$EWT" -name '*.js' -delete
  cp "$T"/package/dist/web/*.js "$EWT"/ && cp "$T"/package/LICENSE "$EWT"/LICENSE
  V=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$T/package/package.json")
  sed -i.bak "s/esp-web-tools [0-9][0-9.]*/esp-web-tools $V/" "$EWT/README.md" && rm -f "$EWT/README.md.bak"
  rm -rf "$T"; echo "release: vendored esp-web-tools $V"
fi

if [ "$DO_DEPLOY" = 1 ]; then
  echo "release: mkdocs gh-deploy --force"
  ( cd "$DIR" && mkdocs gh-deploy --force )
fi
