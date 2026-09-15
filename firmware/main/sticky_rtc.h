#pragma once

#include "driver/i2c_master.h"
#include "esp_err.h"

struct RtcDateTime {
    int year = 0;
    int month = 0;
    int day = 0;
    int hour = 0;
    int minute = 0;
    int second = 0;
};

// Attaches the read-only PCF8563 device to the shared sensor I2C bus.
esp_err_t sticky_rtc_init(i2c_master_bus_handle_t bus);

// Reads and validates the current RTC date and time without changing it.
esp_err_t sticky_rtc_read(RtcDateTime &date_time);

// Sets the RTC. The caller decides the zone; everything in this firmware writes
// UTC, because a wall clock that silently means "whatever zone the setter was
// in" cannot be compared to anything. Writing also clears the VL flag, so a
// read stops reporting ESP_ERR_INVALID_STATE.
esp_err_t sticky_rtc_write(const RtcDateTime &date_time);
