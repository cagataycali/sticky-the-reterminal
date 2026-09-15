"""mkdocs hook — ship the firmware binaries next to the install manifest.

docs/install/manifest.json (written by tools/release.sh) points at fw/<part>.bin.
Those bytes are never committed; at build time this hook copies them from
firmware/build into site/install/fw/ and refuses to build a site whose parts
do not match docs/install/sha256sums.txt — a manifest must never describe
bytes other than the ones it ships with.

Also mirrors docs/install/install.sh to site/install.sh so the short URL
  curl -fsSL https://cagataycali.github.io/sticky-the-reterminal/install.sh | sh
works alongside the canonical /install/install.sh.

Set STICKY_SKIP_FW=1 to build the docs without a firmware/build dir (the
install page then 404s on parts — fine for text-only previews, never deploy).
"""
import hashlib
import json
import os
import shutil

from mkdocs.exceptions import PluginError

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, "firmware", "build")
INSTALL = os.path.join(ROOT, "docs", "install")


def _flasher_parts():
    fa = json.load(open(os.path.join(BUILD, "flasher_args.json")))
    return {os.path.basename(v): os.path.join(BUILD, v) for v in fa["flash_files"].values()}


def _expected_sums():
    sums = {}
    p = os.path.join(INSTALL, "sha256sums.txt")
    if not os.path.exists(p):
        return sums
    for line in open(p):
        line = line.strip()
        if line:
            digest, name = line.split(None, 1)
            sums[os.path.basename(name)] = digest
    return sums


def on_post_build(config, **kwargs):
    site = config["site_dir"]
    src_sh = os.path.join(INSTALL, "install.sh")
    if os.path.exists(src_sh):
        shutil.copy2(src_sh, os.path.join(site, "install.sh"))

    if os.environ.get("STICKY_SKIP_FW") == "1":
        return
    if not os.path.exists(os.path.join(BUILD, "flasher_args.json")):
        raise PluginError(
            "install_assets_hook: firmware/build is missing — run tools/release.sh "
            "(or STICKY_SKIP_FW=1 for a text-only preview)"
        )
    manifest_path = os.path.join(INSTALL, "manifest.json")
    if not os.path.exists(manifest_path):
        raise PluginError("install_assets_hook: docs/install/manifest.json missing — run tools/release.sh")
    manifest = json.load(open(manifest_path))
    expected = _expected_sums()
    parts = _flasher_parts()
    fw_dir = os.path.join(site, "install", "fw")
    os.makedirs(fw_dir, exist_ok=True)
    for build in manifest["builds"]:
        for part in build["parts"]:
            name = os.path.basename(part["path"])
            src = parts.get(name)
            if not src or not os.path.exists(src):
                raise PluginError(f"install_assets_hook: manifest part {name} not in firmware/build")
            data = open(src, "rb").read()
            digest = hashlib.sha256(data).hexdigest()
            if expected.get(name) and expected[name] != digest:
                raise PluginError(
                    f"install_assets_hook: {name} in firmware/build does not match "
                    f"sha256sums.txt — re-run tools/release.sh before deploying"
                )
            shutil.copy2(src, os.path.join(fw_dir, name))
    # sha256sums.txt lives in docs/install and is copied by mkdocs itself.
