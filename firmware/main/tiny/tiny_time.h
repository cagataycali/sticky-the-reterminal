// tiny_time — the device's clock, in two directions.
//
// Boot:      PCF8563 -> system time. The RTC is battery-backed, so tiny knows
//            roughly what time it is with no wifi and no backend. Without this,
//            every reboot starts at 1970 and any on-card timestamp is a lie.
// Wifi up:   SNTP -> system time -> PCF8563. One sync makes the RTC true and
//            keeps it true across the next power cut.
//
// Everything here is UTC. A wall clock whose zone depends on who set it cannot
// be compared to anything; rendering local time is a display concern and needs
// a timezone from cagatay (see docs/QUESTIONS.md).
#pragma once
#include <stdbool.h>
#include <stddef.h>

#include "esp_err.h"
#ifdef __cplusplus
extern "C" {
#endif

// Reads the PCF8563 and sets the system clock from it. ESP_ERR_INVALID_STATE
// when the RTC has never been set (VL flag) — that is not a failure, it is the
// factory state, and it means "wait for SNTP".
esp_err_t tiny_time_init_from_rtc(void);

// Blocks up to timeout_ms for an SNTP answer, then writes the result back to
// the PCF8563. Safe to call more than once; only starts SNTP once.
esp_err_t tiny_time_sync_sntp(int timeout_ms);

// true once SNTP has answered this boot (i.e. the clock is trustworthy).
bool tiny_time_is_synced(void);

// "unset" | "rtc" | "sntp" — where the current system time came from, so the
// sensors/status payloads can say how much to trust it.
const char *tiny_time_source(void);

// "YYYY-MM-DD HH:MM:SSZ" from the system clock; empty string if unset.
void tiny_time_utc_string(char *out, size_t cap);

#ifdef __cplusplus
}
#endif
