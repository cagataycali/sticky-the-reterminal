#!/bin/bash
# changelog.sh — the release half of changelog.d (https://cagataycali.github.io/changelog.d/).
#
#   tools/changelog.sh check                 every pending changelog.d/ note is valid
#   tools/changelog.sh release "<heading>"   fold the notes into CHANGELOG.md as a new
#                                            release section, e.g.
#                                            "0.30.0 — grammar v13 — <name>"
#   tools/changelog.sh notes <version>       print one release section's entries
#                                            (used for the GitHub release body)
#   tools/changelog.sh pending               exit 1 when notes are still waiting
#
# `release` runs `changelog-d apply` (all or nothing: one bad note changes nothing),
# then renames `## [Unreleased]` to `## <heading>` and puts a fresh empty
# `## [Unreleased]` above it. Sections below it are history and are never touched.
# Needs `changelog-d` on PATH:  pip install git+https://github.com/cagataycali/changelog.d
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
LOG=CHANGELOG.md
FRAG=changelog.d
UNREL='## [Unreleased]'

need_cli() {
  command -v changelog-d >/dev/null || {
    echo "changelog: changelog-d not on PATH — pip install git+https://github.com/cagataycali/changelog.d" >&2
    exit 1
  }
}

pending_count() {
  find "$FRAG" -maxdepth 1 -type f -name '*.md' ! -name README.md 2>/dev/null | wc -l | tr -d ' '
}

cmd="${1:-}"
case "$cmd" in
  check)
    need_cli
    changelog-d check
    ;;
  pending)
    n=$(pending_count)
    if [ "$n" != 0 ]; then
      echo "changelog: $n note(s) in $FRAG/ not yet folded — run tools/changelog.sh release \"<heading>\"" >&2
      exit 1
    fi
    echo "changelog: no pending notes"
    ;;
  release)
    heading="${2:-}"
    [ -n "$heading" ] || { echo "usage: tools/changelog.sh release \"<version> — grammar vN — <name>\"" >&2; exit 2; }
    case "$heading" in "## "*|*$'\n'*) echo "changelog: give the heading text without '## '" >&2; exit 2 ;; esac
    if grep -qxF "## $heading" "$LOG"; then
      echo "changelog: '## $heading' already exists in $LOG" >&2; exit 1
    fi
    [ "$(grep -cxF "$UNREL" "$LOG")" = 1 ] || { echo "changelog: $LOG needs exactly one '$UNREL'" >&2; exit 1; }
    need_cli
    [ "$(pending_count)" != 0 ] || { echo "changelog: no notes in $FRAG/ — nothing to release" >&2; exit 1; }
    changelog-d apply
    python3 - "$LOG" "$UNREL" "## $heading" <<'PY'
import sys
path, unrel, heading = sys.argv[1:]
lines = open(path, encoding="utf-8").read().split("\n")
i = lines.index(unrel)
lines[i:i + 1] = [unrel, "", heading]
# apply() leaves two blank lines before the next (released) section; keep one.
j = next((k for k in range(i + 3, len(lines)) if lines[k].startswith("## ")), None)
if j is not None:
    while lines[j - 1] == "" and lines[j - 2] == "":
        del lines[j - 1]
        j -= 1
open(path, "w", encoding="utf-8").write("\n".join(lines))
PY
    echo "changelog: released as '## $heading'; fresh '$UNREL' above it"
    ;;
  notes)
    ver="${2:-}"
    [ -n "$ver" ] || { echo "usage: tools/changelog.sh notes <version>" >&2; exit 2; }
    out=$(awk -v ver="$ver" '
      /^## / { if (inside) exit; split(substr($0, 4), w, " "); inside = (w[1] == ver); next }
      inside { print }' "$LOG" | sed -e '/./,$!d')
    [ -n "$out" ] || { echo "changelog: no '## $ver …' section with entries in $LOG" >&2; exit 1; }
    printf '%s\n' "$out"
    ;;
  *)
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
