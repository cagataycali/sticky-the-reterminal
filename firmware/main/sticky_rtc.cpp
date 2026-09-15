#include "sticky_rtc.h"

#include <cstdint>

#include "esp_log.h"
#include "pin_config.h"

namespace {

constexpr char kTag[] = "sticky_rtc";
constexpr uint32_t kI2cClockHz = 400000;
constexpr int kI2cTimeoutMs = 100;
constexpr uint8_t kTimeRegister = 0x02;
constexpr uint8_t kLowVoltageFlag = 0x80;

i2c_master_dev_handle_t s_device = nullptr;

bool valid_bcd(uint8_t value)
{
    return (value & 0x0FU) <= 9U && ((value >> 4) & 0x0FU) <= 9U;
}

int bcd_to_decimal(uint8_t value)
{
    return static_cast<int>((value >> 4) * 10U + (value & 0x0FU));
}

uint8_t decimal_to_bcd(int value)
{
    return static_cast<uint8_t>(((value / 10) << 4) | (value % 10));
}

bool valid_date_time(const RtcDateTime &value)
{
    if (value.year < 1900 || value.year > 2099 ||
        value.month < 1 || value.month > 12 ||
        value.day < 1 || value.hour < 0 || value.hour > 23 ||
        value.minute < 0 || value.minute > 59 ||
        value.second < 0 || value.second > 59) {
        return false;
    }

    constexpr int days_per_month[] = {
        31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31,
    };
    int maximum_day = days_per_month[value.month - 1];
    const bool leap_year =
        (value.year % 4 == 0 && value.year % 100 != 0) || value.year % 400 == 0;
    if (value.month == 2 && leap_year) {
        maximum_day = 29;
    }
    return value.day <= maximum_day;
}

}  // namespace

esp_err_t sticky_rtc_init(i2c_master_bus_handle_t bus)
{
    if (bus == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_device != nullptr) {
        return ESP_OK;
    }

    i2c_device_config_t config = {};
    config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    config.device_address = PCF8563_I2C_ADDR;
    config.scl_speed_hz = kI2cClockHz;

    const esp_err_t result = i2c_master_bus_add_device(bus, &config, &s_device);
    if (result == ESP_OK) {
        ESP_LOGI(kTag, "Initialized: I2C address=0x%02X", PCF8563_I2C_ADDR);
    }
    return result;
}

esp_err_t sticky_rtc_read(RtcDateTime &date_time)
{
    if (s_device == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }

    uint8_t raw[7] = {};
    const esp_err_t result = i2c_master_transmit_receive(
        s_device, &kTimeRegister, 1, raw, sizeof(raw), kI2cTimeoutMs);
    if (result != ESP_OK) {
        ESP_LOGE(kTag, "Read failed: %s", esp_err_to_name(result));
        return result;
    }

    // PCF8563 sets VL when the clock integrity is not guaranteed.
    if ((raw[0] & kLowVoltageFlag) != 0U) {
        ESP_LOGW(kTag, "RTC time invalid: low-voltage flag is set");
        return ESP_ERR_INVALID_STATE;
    }

    const uint8_t second = raw[0] & 0x7FU;
    const uint8_t minute = raw[1] & 0x7FU;
    const uint8_t hour = raw[2] & 0x3FU;
    const uint8_t day = raw[3] & 0x3FU;
    const uint8_t month = raw[5] & 0x1FU;
    const uint8_t year = raw[6];
    if (!valid_bcd(second) || !valid_bcd(minute) || !valid_bcd(hour) ||
        !valid_bcd(day) || !valid_bcd(month) || !valid_bcd(year)) {
        ESP_LOGW(kTag, "RTC time invalid: malformed BCD data");
        return ESP_ERR_INVALID_RESPONSE;
    }

    RtcDateTime value = {};
    value.second = bcd_to_decimal(second);
    value.minute = bcd_to_decimal(minute);
    value.hour = bcd_to_decimal(hour);
    value.day = bcd_to_decimal(day);
    value.month = bcd_to_decimal(month);
    const int short_year = bcd_to_decimal(year);
    value.year = (raw[5] & 0x80U) != 0U ? 1900 + short_year
                                        : 2000 + short_year;

    if (!valid_date_time(value)) {
        ESP_LOGW(kTag, "RTC time invalid: value is out of range");
        return ESP_ERR_INVALID_RESPONSE;
    }

    date_time = value;
    ESP_LOGI(kTag, "Read success: %04d-%02d-%02d %02d:%02d:%02d",
             value.year, value.month, value.day,
             value.hour, value.minute, value.second);
    return ESP_OK;
}

esp_err_t sticky_rtc_write(const RtcDateTime &date_time)
{
    if (s_device == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!valid_date_time(date_time)) {
        ESP_LOGE(kTag, "Refusing to write an invalid date/time");
        return ESP_ERR_INVALID_ARG;
    }

    // Register 0x02..0x08: sec (bit7 = VL, written 0 to declare the time good),
    // min, hour, day, weekday, month (bit7 = century), year. Weekday is written
    // as a computed value rather than left stale — nothing here reads it, but a
    // phone or another driver might.
    const int y = date_time.year;
    const bool century_1900 = y < 2000;
    const int short_year = century_1900 ? y - 1900 : y - 2000;

    // Zeller's congruence, 0 = Sunday, matching the PCF8563 weekday encoding.
    int m = date_time.month, yy = y;
    if (m < 3) { m += 12; yy -= 1; }
    const int weekday =
        (date_time.day + (13 * (m + 1)) / 5 + yy + yy / 4 - yy / 100 +
         yy / 400 + 6) % 7;

    const uint8_t payload[8] = {
        kTimeRegister,
        decimal_to_bcd(date_time.second),   // VL cleared by writing bit7 = 0
        decimal_to_bcd(date_time.minute),
        decimal_to_bcd(date_time.hour),
        decimal_to_bcd(date_time.day),
        static_cast<uint8_t>(weekday & 0x07),
        static_cast<uint8_t>(decimal_to_bcd(date_time.month) |
                             (century_1900 ? 0x80U : 0x00U)),
        decimal_to_bcd(short_year),
    };

    const esp_err_t result =
        i2c_master_transmit(s_device, payload, sizeof(payload), kI2cTimeoutMs);
    if (result != ESP_OK) {
        ESP_LOGE(kTag, "Write failed: %s", esp_err_to_name(result));
        return result;
    }
    ESP_LOGI(kTag, "Set to %04d-%02d-%02d %02d:%02d:%02d (weekday %d)",
             date_time.year, date_time.month, date_time.day, date_time.hour,
             date_time.minute, date_time.second, weekday);
    return ESP_OK;
}
