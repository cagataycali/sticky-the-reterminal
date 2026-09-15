#!/bin/bash
# askq host test — compiles the SHIPPED firmware/main/tiny_askq.cpp against
# shim headers (tests/host/shim). Two process runs share one store file:
# the restart IS the reboot. See askq_cases.cpp.
set -euo pipefail
cd "$(dirname "$0")"
export ASKQ_NVS_FILE=/tmp/askq_nvs_store.tsv
rm -f "$ASKQ_NVS_FILE"
c++ -std=c++17 -Wall -Werror -I shim -I ../../firmware/main \
    ../../firmware/main/tiny_askq.cpp askq_cases.cpp -o /tmp/askq_test
/tmp/askq_test fresh
/tmp/askq_test reboot
