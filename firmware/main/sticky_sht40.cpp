#include "sticky_sht40.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "pin_config.h"

namespace {

constexpr uint32_t kI2cClockHz = 400000;
constexpr int kI2cTimeoutMs = 100;
constexpr int kRetryDelayMs = 20;
constexpr uint8_t kMeasureHighPrecision = 0xFD;
constexpr TickType_t kMeasurementTime = pdMS_TO_TICKS(10);
constexpr char kTag[] = "sticky_sht40";

i2c_master_bus_handle_t s_bus = nullptr;

uint8_t calculate_crc8(const uint8_t *data, size_t length)
{
    uint8_t crc = 0xFF;
    for (size_t index = 0; index < length; ++index) {
        crc ^= data[index];
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc & 0x80U) != 0U
                      ? static_cast<uint8_t>((crc << 1) ^ 0x31U)
                      : static_cast<uint8_t>(crc << 1);
        }
    }
    return crc;
}

}  // namespace

esp_err_t sticky_sht40_init(i2c_master_bus_handle_t bus)
{
    if (bus == nullptr) {
        ESP_LOGE(kTag, "Init failed: sensor I2C bus is null");
        return ESP_ERR_INVALID_ARG;
    }
    s_bus = bus;
    ESP_LOGI(kTag, "Initialized: I2C address=0x%02X, clock=%lu Hz",
             SHT40_I2C_ADDR, static_cast<unsigned long>(kI2cClockHz));
    return ESP_OK;
}

esp_err_t sticky_sht40_read(Sht40Reading &reading)
{
    if (s_bus == nullptr) {
        ESP_LOGE(kTag, "Read failed: module is not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    ESP_LOGI(kTag, "Starting measurement at address 0x%02X",
             SHT40_I2C_ADDR);

    // Register the sensor only for the duration of this measurement. This
    // keeps the shared I2C bus ownership explicit and the public API small.
    i2c_device_config_t device_config = {};
    device_config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    device_config.device_address = SHT40_I2C_ADDR;
    device_config.scl_speed_hz = kI2cClockHz;

    i2c_master_dev_handle_t device = nullptr;
    esp_err_t result =
        i2c_master_bus_add_device(s_bus, &device_config, &device);
    if (result != ESP_OK || device == nullptr) {
        ESP_LOGE(kTag, "Add I2C device failed: %s",
                 esp_err_to_name(result != ESP_OK ? result : ESP_FAIL));
        return result != ESP_OK ? result : ESP_FAIL;
    }

    uint8_t data[6] = {};
    result = i2c_master_transmit(
        device, &kMeasureHighPrecision, 1, kI2cTimeoutMs);
    if (result != ESP_OK) {
        ESP_LOGE(kTag, "Send measurement command 0x%02X failed: %s",
                 kMeasureHighPrecision, esp_err_to_name(result));

        // Recover a bus left busy by a reset or an interrupted transaction,
        // then retry once. This stays in the device layer so callers keep a
        // simple read API.
        ESP_LOGW(kTag, "Resetting sensor I2C bus and retrying once");
        const esp_err_t reset_result = i2c_master_bus_reset(s_bus);
        if (reset_result == ESP_OK) {
            vTaskDelay(pdMS_TO_TICKS(kRetryDelayMs));
            result = i2c_master_transmit(
                device, &kMeasureHighPrecision, 1, kI2cTimeoutMs);
            if (result == ESP_OK) {
                ESP_LOGI(kTag, "Measurement command accepted after bus reset");
            } else {
                ESP_LOGE(kTag, "Retry failed: %s", esp_err_to_name(result));
            }
        } else {
            ESP_LOGE(kTag, "Sensor I2C bus reset failed: %s",
                     esp_err_to_name(reset_result));
        }
    }
    if (result == ESP_OK) {
        ESP_LOGI(kTag, "Measurement command accepted; waiting 10 ms");
        vTaskDelay(kMeasurementTime);
        result = i2c_master_receive(
            device, data, sizeof(data), kI2cTimeoutMs);
        if (result != ESP_OK) {
            ESP_LOGE(kTag, "Receive 6-byte result failed: %s",
                     esp_err_to_name(result));
        }
    }

    if (result == ESP_OK &&
        (calculate_crc8(data, 2) != data[2] ||
         calculate_crc8(data + 3, 2) != data[5])) {
        ESP_LOGE(kTag,
                 "CRC failed: data=%02X %02X %02X %02X %02X %02X",
                 data[0], data[1], data[2], data[3], data[4], data[5]);
        result = ESP_ERR_INVALID_CRC;
    }

    if (result == ESP_OK) {
        const uint16_t raw_temperature =
            (static_cast<uint16_t>(data[0]) << 8) | data[1];
        const uint16_t raw_humidity =
            (static_cast<uint16_t>(data[3]) << 8) | data[4];

        reading.temperature_c =
            -45.0F + 175.0F * static_cast<float>(raw_temperature) / 65535.0F;
        reading.humidity_percent =
            -6.0F + 125.0F * static_cast<float>(raw_humidity) / 65535.0F;
        reading.humidity_percent =
            std::clamp(reading.humidity_percent, 0.0F, 100.0F);
        ESP_LOGI(kTag, "Read success: temperature=%.2f C, humidity=%.2f %%",
                 static_cast<double>(reading.temperature_c),
                 static_cast<double>(reading.humidity_percent));
    }

    const esp_err_t remove_result = i2c_master_bus_rm_device(device);
    if (remove_result != ESP_OK) {
        ESP_LOGE(kTag, "Remove I2C device failed: %s",
                 esp_err_to_name(remove_result));
    }
    return result != ESP_OK ? result : remove_result;
}
