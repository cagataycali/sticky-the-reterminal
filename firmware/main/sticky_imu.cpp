#include "sticky_imu.h"

#include <cmath>
#include <cstddef>
#include <cstdint>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "pin_config.h"

namespace {

constexpr char kTag[] = "sticky_imu";
constexpr uint32_t kI2cClockHz = 400000;
constexpr int kI2cTimeoutMs = 100;
constexpr uint8_t kWhoAmIRegister = 0x0F;
constexpr uint8_t kExpectedWhoAmI = 0x6A;
constexpr uint8_t kAccelerometerControlRegister = 0x10;
constexpr uint8_t kControlRegister3 = 0x12;
constexpr uint8_t kAccelerometerOutputRegister = 0x28;
constexpr uint8_t kGyroControlRegister = 0x11;   // CTRL2_G
constexpr uint8_t kGyroOutputRegister = 0x22;    // OUTX_L_G
constexpr uint8_t kGyro104Hz250dps = 0x40;       // ODR 104 Hz, FS +/-250 dps
constexpr uint8_t kGyroPowerDown = 0x00;
constexpr float kGyroScaleDps = 0.00875F;        // 8.75 mdps/LSB at 250 dps
constexpr TickType_t kGyroSettle = pdMS_TO_TICKS(110);
constexpr uint8_t kAccelerometer104Hz2g = 0x40;
constexpr uint8_t kRegisterAutoIncrement = 0x04;
constexpr float kAccelerationScaleG = 0.000061F;
constexpr float kOrientationThresholdG = 0.70F;

i2c_master_dev_handle_t s_device = nullptr;

esp_err_t write_register(uint8_t reg, uint8_t value)
{
    const uint8_t data[] = {reg, value};
    return i2c_master_transmit(s_device, data, sizeof(data), kI2cTimeoutMs);
}

esp_err_t read_registers(uint8_t reg, uint8_t *data, size_t length)
{
    return i2c_master_transmit_receive(
        s_device, &reg, 1, data, length, kI2cTimeoutMs);
}

int16_t read_int16_le(const uint8_t *data)
{
    return static_cast<int16_t>(
        (static_cast<uint16_t>(data[1]) << 8) | data[0]);
}

// Calibration comments carried over from the vendor demo — they encode which
// way this specific board is glued into its case.
StickyImuOrientation classify_orientation(float x, float y, float z)
{
    const float abs_x = std::fabs(x);
    const float abs_y = std::fabs(y);
    const float abs_z = std::fabs(z);

    if (abs_z >= kOrientationThresholdG && abs_z > abs_x && abs_z > abs_y) {
        return z >= 0.0F ? StickyImuOrientation::FaceUp
                         : StickyImuOrientation::FaceDown;
    }
    if (abs_x >= kOrientationThresholdG && abs_x > abs_y) {
        // -X is the normal landscape down direction.
        return x < 0.0F ? StickyImuOrientation::ArrowDown
                        : StickyImuOrientation::ArrowUp;
    }
    if (abs_y >= kOrientationThresholdG) {
        // Portrait -Y points toward the physical ground.
        return y < 0.0F ? StickyImuOrientation::ArrowRight
                        : StickyImuOrientation::ArrowLeft;
    }
    return StickyImuOrientation::Unknown;
}

}  // namespace

esp_err_t sticky_imu_init(i2c_master_bus_handle_t bus)
{
    if (bus == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_device != nullptr) {
        return ESP_OK;
    }

    i2c_device_config_t config = {};
    config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    config.device_address = LSM6DS3_I2C_ADDR;
    config.scl_speed_hz = kI2cClockHz;

    esp_err_t result = i2c_master_bus_add_device(bus, &config, &s_device);
    if (result != ESP_OK) {
        return result;
    }

    uint8_t who_am_i = 0;
    result = read_registers(kWhoAmIRegister, &who_am_i, 1);
    if (result != ESP_OK || who_am_i != kExpectedWhoAmI) {
        ESP_LOGE(kTag, "Unexpected WHO_AM_I: 0x%02X", who_am_i);
        i2c_master_bus_rm_device(s_device);
        s_device = nullptr;
        return result != ESP_OK ? result : ESP_ERR_NOT_FOUND;
    }

    result = write_register(kAccelerometerControlRegister,
                            kAccelerometer104Hz2g);
    if (result == ESP_OK) {
        result = write_register(kControlRegister3, kRegisterAutoIncrement);
    }
    if (result != ESP_OK) {
        i2c_master_bus_rm_device(s_device);
        s_device = nullptr;
        return result;
    }

    // 104 Hz needs a few sample periods before the output registers are real.
    vTaskDelay(pdMS_TO_TICKS(100));
    ESP_LOGI(kTag, "Initialized: address=0x%02X, accelerometer=104Hz +/-2g",
             LSM6DS3_I2C_ADDR);
    return ESP_OK;
}

esp_err_t sticky_imu_read(StickyImuState &state)
{
    if (s_device == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }

    uint8_t data[6] = {};
    const esp_err_t result =
        read_registers(kAccelerometerOutputRegister, data, sizeof(data));
    if (result != ESP_OK) {
        ESP_LOGE(kTag, "Accelerometer read failed: %s",
                 esp_err_to_name(result));
        state.valid = false;
        return result;
    }

    state.acceleration_x_g = read_int16_le(data) * kAccelerationScaleG;
    state.acceleration_y_g = read_int16_le(data + 2) * kAccelerationScaleG;
    state.acceleration_z_g = read_int16_le(data + 4) * kAccelerationScaleG;
    state.orientation = classify_orientation(state.acceleration_x_g,
                                             state.acceleration_y_g,
                                             state.acceleration_z_g);
    state.valid = true;
    return ESP_OK;
}

esp_err_t sticky_imu_read_gyro(StickyGyroReading &reading)
{
    if (s_device == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }

    // Power up, settle, read, power down. The power-down runs even when the read
    // fails: leaving the gyro spinning because of an I2C hiccup would silently
    // cost battery for the rest of the boot.
    esp_err_t result = write_register(kGyroControlRegister, kGyro104Hz250dps);
    if (result != ESP_OK) {
        ESP_LOGE(kTag, "Gyro power-up failed: %s", esp_err_to_name(result));
        reading.valid = false;
        return result;
    }
    vTaskDelay(kGyroSettle);

    uint8_t data[6] = {};
    result = read_registers(kGyroOutputRegister, data, sizeof(data));
    const esp_err_t off = write_register(kGyroControlRegister, kGyroPowerDown);
    if (off != ESP_OK)
        ESP_LOGW(kTag, "Gyro power-down failed: %s", esp_err_to_name(off));
    if (result != ESP_OK) {
        ESP_LOGE(kTag, "Gyro read failed: %s", esp_err_to_name(result));
        reading.valid = false;
        return result;
    }

    reading.x_dps = read_int16_le(data) * kGyroScaleDps;
    reading.y_dps = read_int16_le(data + 2) * kGyroScaleDps;
    reading.z_dps = read_int16_le(data + 4) * kGyroScaleDps;
    reading.valid = true;
    ESP_LOGI(kTag, "Gyro: %.2f %.2f %.2f dps", (double)reading.x_dps,
             (double)reading.y_dps, (double)reading.z_dps);
    return ESP_OK;
}

const char *sticky_imu_orientation_name(StickyImuOrientation orientation)
{
    switch (orientation) {
        case StickyImuOrientation::ArrowUp:    return "arrow_up";
        case StickyImuOrientation::ArrowDown:  return "arrow_down";
        case StickyImuOrientation::ArrowLeft:  return "arrow_left";
        case StickyImuOrientation::ArrowRight: return "arrow_right";
        case StickyImuOrientation::FaceUp:     return "face_up";
        case StickyImuOrientation::FaceDown:   return "face_down";
        case StickyImuOrientation::Unknown:
        default:                               return "unknown";
    }
}
