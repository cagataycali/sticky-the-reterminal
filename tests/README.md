# Tests

```sh
tests/run.sh          # all of them, ~5 s, exit code is the verdict
```

Everything here runs on the **host** with a plain C++17 compiler. No ESP-IDF,
no board. Each test compiles the *shipped* firmware source against the stubs in
`host/shim/` (`esp_err.h`, `esp_log.h`, `nvs.h` backed by a TSV file, a
FreeRTOS header that is all no-ops), so a test can never drift onto a stale
copy of the code.

| test | compiles | asserts |
|---|---|---|
| `askq_host_test.sh` | `firmware/main/tiny_askq.cpp` | the offline ask queue: FIFO order, depth-8 eviction, and that the queue survives a reboot — the script runs the binary twice against one NVS file; the second process *is* the reboot (20 checks) |
| `bootmark_host_test.sh` | `firmware/main/tiny_bootmark.cpp` | the boot flight recorder: `prev` reads -1 before any write, latches the crash stage, reports 9 after a clean run (6 checks) |
| `dr52_host_test.sh` | `utf8_complete_len` + `copy_label_utf8`, extracted from `tiny_display.cpp` at build time | UTF-8-safe truncation of card labels: never cut through a multi-byte rune, Turkish and emoji cases, NULL/empty (10 checks) |

Each was checked by mutation: break the function under test and the test
fails (askq: 2, bootmark: 2, dr52: 4 checks go red).

What is **not** tested here: anything that needs the panel, WiFi or the
tiny.technology relay. That is verified on a real Sticky — `tools/monitor.sh`
and the `status` / `screenshot` verbs are the tools for it.

## Adding one

Copy the nearest `*_host_test.sh` + cases file, compile the shipped `.cpp`
(not a copy), add a shim header only if the module needs one, and make the
binary exit non-zero on any failed check. `run.sh` picks up every
`*_host_test.sh` automatically.
