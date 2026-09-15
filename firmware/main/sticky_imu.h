// sticky_imu — LSM6DS3TR-C accelerometer, one-shot reads.
//
// Adapted from Seeed's Sticky_dashboard_demo (main/devices/sticky_imu.*, see
// vendor/README.md), which is production-verified on this board. Two deliberate
// differences:
//   * no monitoring task and no app_event dependency — the `sensors` verb asks
//     a question and wants an answer NOW, not the last value a background task
//     happened to cache;
//   * therefore sticky_imu_read() does the I2C burst itself, so it works
//     without anything else having been started.
// The gyroscope is powered up ONLY for the duration of a read and switched off
// again (CTRL2_G = 0). An always-on gyro is ~0.6 mA of nothing on a 750 mAh wall
// device; the price is ~110 ms of settling inside sticky_imu_read_gyro().
#pragma once

#include "driver/i2c_master.h"
#include "esp_err.h"

enum class StickyImuOrientation {
    Unknown,
    ArrowUp,
    ArrowDown,
    ArrowLeft,
    ArrowRight,
    FaceUp,
    FaceDown,
};

struct StickyImuState {
    float acceleration_x_g = 0.0F;
    float acceleration_y_g = 0.0F;
    float acceleration_z_g = 0.0F;
    StickyImuOrientation orientation = StickyImuOrientation::Unknown;
    bool valid = false;
};

struct StickyGyroReading {
    float x_dps = 0.0F;
    float y_dps = 0.0F;
    float z_dps = 0.0F;
    bool valid = false;
};

// Initializes the accelerometer on the shared sensor I2C bus (104 Hz, +/-2 g).
esp_err_t sticky_imu_init(i2c_master_bus_handle_t bus);

// Reads acceleration + classifies orientation right now. ESP_ERR_INVALID_STATE
// if init never succeeded.
esp_err_t sticky_imu_read(StickyImuState &state);

// Powers the gyro up, waits for it to settle, reads one sample, powers it down.
// Blocks ~110 ms. At rest all three axes read near zero (a few dps of offset) —
// which is exactly how you tell a real reading from a fabricated one.
esp_err_t sticky_imu_read_gyro(StickyGyroReading &reading);

// Stable lower-case name for JSON/report use ("face_up", "arrow_left", ...).
const char *sticky_imu_orientation_name(StickyImuOrientation orientation);
