// bootmark host test — compiles the SHIPPED firmware/main/tiny_bootmark.cpp.
// The file-backed NVS shim makes a process restart a genuine reboot; a
// process EXIT mid-run is a genuine crash (statics die like the chip's).
// Modes:
//   fresh   — first boot ever: prev==0, stages write, "crash" at stage 5
//   after5  — boot after that crash: prev MUST say 5 (the flight recorder's
//             whole reason to exist), then run clean to 9
//   after9  — boot after a clean run: prev==9
#include "tiny/tiny_bootmark.h"
#include <cstdio>
#include <cstring>
#include <cstdlib>

static int fails = 0;
#define CHECK(name, cond) do { \
    if (cond) printf("ok   %s\n", name); \
    else { printf("FAIL %s\n", name); fails++; } \
} while (0)

int main(int argc, char **argv)
{
    const char *mode = argc > 1 ? argv[1] : "fresh";

    if (strcmp(mode, "fresh") == 0) {
        CHECK("prev is -1 before any write", tiny_bootmark_prev() == -1);
        tiny_bootmark_stage(1);
        CHECK("first boot ever: prev reads 0", tiny_bootmark_prev() == 0);
        tiny_bootmark_stage(2);
        CHECK("prev latched once, later stages don't move it",
              tiny_bootmark_prev() == 0);
        tiny_bootmark_stage(3);
        tiny_bootmark_stage(4);
        tiny_bootmark_stage(5);
        // process exits here == crash after stage 5, before stage 6
    } else if (strcmp(mode, "after5") == 0) {
        tiny_bootmark_stage(1);
        CHECK("crash evidence: prev names stage 5", tiny_bootmark_prev() == 5);
        for (uint8_t st = 2; st <= 9; st++) tiny_bootmark_stage(st);
        CHECK("clean run: prev still the OLD verdict", tiny_bootmark_prev() == 5);
    } else if (strcmp(mode, "after9") == 0) {
        tiny_bootmark_stage(1);
        CHECK("clean-run evidence: prev reads 9", tiny_bootmark_prev() == 9);
    } else {
        fprintf(stderr, "unknown mode %s\n", mode);
        return 2;
    }
    return fails ? 1 : 0;
}
