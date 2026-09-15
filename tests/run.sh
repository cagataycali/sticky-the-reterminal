#!/bin/bash
# run.sh — run every host test. Needs: a C++17 compiler as `c++`. No device.
set -euo pipefail
cd "$(dirname "$0")/host"
for t in *_host_test.sh; do echo "== $t"; bash "$t"; done
echo "all host tests passed"
