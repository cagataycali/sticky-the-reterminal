#pragma once

#include "esp_err.h"
#include "driver/i2c_master.h"

// Keeps the board powered and puts shared peripherals into a safe idle state.
esp_err_t board_init();

// Shared I2C1 bus used by the onboard sensors.
i2c_master_bus_handle_t board_sensor_i2c_bus();
