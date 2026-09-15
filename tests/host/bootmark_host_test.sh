#!/bin/bash
# bootmark host test — compiles the SHIPPED firmware/main/tiny_bootmark.cpp
# against the shim headers. Three process runs share one store file:
# run 1 "crashes" after stage 5, run 2 must read prev==5, run 3 prev==9.
set -euo pipefail
cd "$(dirname "$0")"
export ASKQ_NVS_FILE=/tmp/bootmark_nvs_store.tsv
rm -f "$ASKQ_NVS_FILE"
c++ -std=c++17 -Wall -Werror -I shim -I ../../firmware/main \
    ../../firmware/main/tiny_bootmark.cpp bootmark_cases.cpp -o /tmp/bootmark_test
/tmp/bootmark_test fresh
/tmp/bootmark_test after5
/tmp/bootmark_test after9
echo "bootmark host test: ALL GREEN"
