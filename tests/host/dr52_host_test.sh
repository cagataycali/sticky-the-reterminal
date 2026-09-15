#!/bin/bash
# Deep-sleep/wake contract host test — A2 (rune-safe truncation) + A1's storage half (byte
# identity through the region copy). The functions under test are EXTRACTED
# FROM THE SHIPPED SOURCE at build time, so this file can never quietly test
# a stale copy: if tiny_display.cpp changes, this tests the new bytes.
set -euo pipefail
cd "$(dirname "$0")"
SRC=../../firmware/main/tiny_display.cpp
GEN=/tmp/dr52_gen.cpp

# Extract utf8_complete_len + copy_label_utf8 verbatim (brace-balanced).
python3 - "$SRC" "$GEN" <<'PY'
import sys, re
src = open(sys.argv[1]).read()
def extract(name):
    # find the DEFINITION (ends with '{'), skipping forward declarations (';')
    i = -1
    while True:
        i = src.index(name, i + 1)
        j = src.index(")", src.index("(", i))
        tail = src[j+1:j+8].lstrip()
        if tail.startswith("{"):
            i = src.rindex("static", 0, i)
            break
    j = src.index("{", i); depth = 0; k = j
    while True:
        if src[k] == "{": depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0: return src[i:k+1]
        k += 1
body = extract("utf8_complete_len") + "\n\n" + extract("copy_label_utf8")
open(sys.argv[2], "w").write(
    "#include <cstring>\n#include <cstdio>\n#include <cstdlib>\n"
    "static size_t utf8_complete_len(const char *s, size_t len);\n\n"
    + body + "\n" + open("dr52_cases.inc").read())
PY
c++ -std=c++17 -Wall -Werror -o /tmp/dr52_test "$GEN"
/tmp/dr52_test
