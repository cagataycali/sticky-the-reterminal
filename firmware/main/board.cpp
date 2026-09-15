#include "board.h"

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_sleep.h"
#include "pin_config.h"

namespace {

i2c_master_bus_handle_t s_sensor_i2c_bus = nullptr;

esp_err_t configure_output(gpio_num_t pin, uint32_t level)
{
    gpio_config_t config = {};
    config.pin_bit_mask = 1ULL << pin;
    config.mode = GPIO_MODE_OUTPUT;
    config.pull_up_en = GPIO_PULLUP_DISABLE;
    config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    config.intr_type = GPIO_INTR_DISABLE;

    esp_err_t result = gpio_config(&config);
    if (result == ESP_OK) {
        result = gpio_set_level(pin, level);
    }
    return result;
}

}  // namespace

esp_err_t board_init()
{
    const bool woke_from_deep_sleep =
        esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_UNDEFINED;
    (void)woke_from_deep_sleep;

    // P0 (2026-08-26, "0.25.0 wedge"): the latch pins may be HELD from the
    // previous runtime (see gpio_hold_en below) — deep sleep, panic-reboot or
    // esp_restart alike. Preload both output levels on EVERY boot before
    // releasing holds, so the board never briefly drops its own power latch.
    // (This used to run only on the deep-sleep path.)
    {
        gpio_set_direction(static_cast<gpio_num_t>(PIN_POWER_HOLD),
                           GPIO_MODE_OUTPUT);
        gpio_set_level(static_cast<gpio_num_t>(PIN_POWER_HOLD), 1);
        gpio_set_direction(static_cast<gpio_num_t>(PIN_POWER_LOCK),
                           GPIO_MODE_OUTPUT);
        gpio_set_level(static_cast<gpio_num_t>(PIN_POWER_LOCK), 1);
    }

    gpio_deep_sleep_hold_dis();
    constexpr gpio_num_t kPossiblyHeldPins[] = {
        static_cast<gpio_num_t>(PIN_POWER_HOLD),
        static_cast<gpio_num_t>(PIN_POWER_LOCK),
        static_cast<gpio_num_t>(PIN_POWER_BTN),
        static_cast<gpio_num_t>(PIN_EPD_EN),
        static_cast<gpio_num_t>(PIN_TOUCH_EN),
        static_cast<gpio_num_t>(PIN_TOUCH_RST),
        static_cast<gpio_num_t>(PIN_MIC_EN),
        static_cast<gpio_num_t>(PIN_SD_EN),
        static_cast<gpio_num_t>(PIN_BUZZER),
    };
    for (gpio_num_t pin : kPossiblyHeldPins) {
        gpio_hold_dis(pin);
    }

    // Hold the board power first, then allow the rails to settle before
    // configuring buses and peripherals.
    gpio_config_t power_config = {};
    power_config.pin_bit_mask =
        (1ULL << PIN_POWER_HOLD) | (1ULL << PIN_POWER_LOCK);
    power_config.mode = GPIO_MODE_OUTPUT;
    power_config.pull_up_en = GPIO_PULLUP_DISABLE;
    power_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    power_config.intr_type = GPIO_INTR_DISABLE;

    esp_err_t result = gpio_config(&power_config);
    if (result != ESP_OK) {
        return result;
    }

    result = gpio_set_level(static_cast<gpio_num_t>(PIN_POWER_HOLD), 1);
    if (result != ESP_OK) {
        return result;
    }
    result = gpio_set_level(static_cast<gpio_num_t>(PIN_POWER_LOCK), 1);
    if (result != ESP_OK) {
        return result;
    }
    // P0 ROOT CAUSE (0.25.0 "wedge", 2026-08-26): a panic-reboot or
    // esp_restart resets every digital pad to Hi-Z. Unheld, GPIO45/46 float
    // during the CPU reset window and the battery-side latch drops — the
    // device POWERS OFF instead of rebooting. Every panic looked like a
    // permanent wedge (dark glass, wifi dead, no heartbeat, "no reboot").
    // gpio_hold_en survives soft resets (only a true power loss clears it),
    // so from here on a panic costs ~60s of downtime instead of a hand.
    gpio_hold_en(static_cast<gpio_num_t>(PIN_POWER_HOLD));
    gpio_hold_en(static_cast<gpio_num_t>(PIN_POWER_LOCK));
    vTaskDelay(pdMS_TO_TICKS(100));

    result = gpio_install_isr_service(0);
    if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) {
        return result;
    }

    // MicroSD and the e-paper panel share SPI2. Keep the unused SD card
    // deselected and powered off before the display takes ownership of SPI2.
    result = configure_output(static_cast<gpio_num_t>(PIN_SD_CS), 1);
    if (result != ESP_OK) {
        return result;
    }
    result = configure_output(static_cast<gpio_num_t>(PIN_SD_EN), 1);
    if (result != ESP_OK) {
        return result;
    }

    i2c_master_bus_config_t sensor_bus_config = {};
    sensor_bus_config.i2c_port = I2C_NUM_1;
    sensor_bus_config.sda_io_num = static_cast<gpio_num_t>(PIN_SENSOR_SDA);
    sensor_bus_config.scl_io_num = static_cast<gpio_num_t>(PIN_SENSOR_SCL);
    sensor_bus_config.clk_source = I2C_CLK_SRC_DEFAULT;
    sensor_bus_config.glitch_ignore_cnt = 7;
    sensor_bus_config.flags.enable_internal_pullup = 1;
    result = i2c_new_master_bus(&sensor_bus_config, &s_sensor_i2c_bus);
    if (result != ESP_OK) {
        return result;
    }

    vTaskDelay(pdMS_TO_TICKS(100));
    return ESP_OK;
}

i2c_master_bus_handle_t board_sensor_i2c_bus()
{
    return s_sensor_i2c_bus;
}
