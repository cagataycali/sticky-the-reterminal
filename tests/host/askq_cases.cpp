// askq host test driver — compiled TOGETHER WITH the shipped tiny_askq.cpp
// (not a copy, not an extract: the object under test is the same file the
// firmware links). Two phases across two PROCESS RUNS; the restart is the
// reboot, because statics die with the process exactly like they die with
// the chip.
#include <cstdio>
#include <cstring>
#include <string>
#include "tiny/tiny_askq.h"

static int fails = 0;
#define CHECK(cond, name) do { \
    if (cond) printf("PASS %s\n", name); \
    else { printf("FAIL %s\n", name); fails++; } } while (0)

int main(int argc, char **argv) {
    const bool fresh = (argc > 1 && strcmp(argv[1], "fresh") == 0);
    char buf[TINY_ASKQ_MAX_LEN];
    if (fresh) {
        CHECK(tiny_askq_count() == 0, "T1 empty count");
        CHECK(tiny_askq_peek(buf, sizeof buf) == ESP_ERR_NOT_FOUND, "T2 empty peek");
        CHECK(tiny_askq_pop() == ESP_ERR_NOT_FOUND, "T3 empty pop");
        CHECK(tiny_askq_push("") == ESP_ERR_INVALID_ARG, "T4 empty text refused");
        std::string big(TINY_ASKQ_MAX_LEN, 'x');
        CHECK(tiny_askq_push(big.c_str()) == ESP_ERR_INVALID_SIZE, "T5 oversize refused");
        char t[16];
        for (int i = 1; i <= 8; ++i) {
            snprintf(t, sizeof t, "ask-%d", i);
            if (tiny_askq_push(t) != ESP_OK) { CHECK(false, "T6 push 8"); break; }
        }
        CHECK(tiny_askq_count() == 8, "T6 push 8 -> count 8");
        CHECK(tiny_askq_push("ninth") == ESP_ERR_NO_MEM, "T7 depth 8 enforced");
        CHECK(tiny_askq_peek(buf, sizeof buf) == ESP_OK && !strcmp(buf, "ask-1"),
              "T8 FIFO head is ask-1");
        CHECK(tiny_askq_pop() == ESP_OK, "T9 pop");
        CHECK(tiny_askq_peek(buf, sizeof buf) == ESP_OK && !strcmp(buf, "ask-2"),
              "T10 FIFO order after pop");
        CHECK(tiny_askq_push("wrapped") == ESP_OK, "T11 push into wrapped slot");
        CHECK(tiny_askq_count() == 8, "T12 wrap keeps count 8");
        for (int i = 2; i <= 8; ++i) {
            snprintf(t, sizeof t, "ask-%d", i);
            bool ok = tiny_askq_peek(buf, sizeof buf) == ESP_OK && !strcmp(buf, t)
                      && tiny_askq_pop() == ESP_OK;
            if (!ok) { CHECK(false, "T13 in-order drain"); return 1; }
        }
        CHECK(tiny_askq_peek(buf, sizeof buf) == ESP_OK && !strcmp(buf, "wrapped"),
              "T13 wrapped item drains LAST (true ring)");
        CHECK(tiny_askq_pop() == ESP_OK && tiny_askq_count() == 0, "T14 drained to 0");
        // leave two behind for the reboot phase
        CHECK(tiny_askq_push("survives-1") == ESP_OK &&
              tiny_askq_push("survives-2") == ESP_OK, "T15 park 2 for reboot");
    } else {
        // PHASE B: a different process = a rebooted chip. NVS is the file.
        CHECK(tiny_askq_count() == 2, "T16 reboot: count survives");
        CHECK(tiny_askq_peek(buf, sizeof buf) == ESP_OK && !strcmp(buf, "survives-1"),
              "T17 reboot: order survives");
        CHECK(tiny_askq_pop() == ESP_OK, "T18 pop after reboot");
        CHECK(tiny_askq_peek(buf, sizeof buf) == ESP_OK && !strcmp(buf, "survives-2"),
              "T19 second survivor next");
        CHECK(tiny_askq_pop() == ESP_OK && tiny_askq_count() == 0, "T20 clean end");
    }
    printf(fails ? "RESULT: %d FAIL\n" : "RESULT: all green\n", fails);
    return fails ? 1 : 0;
}
