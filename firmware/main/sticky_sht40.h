#pragma once

#include "driver/i2c_master.h"
#include "esp_err.h"

struct Sht40Reading {
    float temperature_c;
    float humidity_percent;
};

// Adds the onboard SHT40 to the shared sensor I2C bus.
esp_err_t sticky_sht40_init(i2c_master_bus_handle_t bus);

// Performs one high-precision measurement and validates both CRC bytes.
esp_err_t sticky_sht40_read(Sht40Reading &reading);
