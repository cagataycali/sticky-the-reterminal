#include "sticky_battery.h"

#include <algorithm>
#include <cstdint>
#include <cstdlib>

#include "bq27220.h"
#include "esp_log.h"
#include "pin_config.h"

namespace {

constexpr char kTag[] = "sticky_battery";
constexpr uint32_t kI2cClockHz = 400000;

// The pack this board actually ships with: Seeed reTerminal Sticky, 750 mAh
// (docs/hardware.md line 3). The BQ27220 in front of it reports a design
// capacity of 7500 mAh — exactly 10x — so `remaining_mah` / `full_charge_mah`
// have been off by an order of magnitude since the gauge was first read
// (the symptom was seen in the field first; this is where the two numbers
// finally sit next to each other). We do NOT silently divide by ten: that would be a
// guess dressed as a measurement. We report both numbers and say which we
// trust, and fixing it for real means writing the gauge's data flash, which
// needs cagatay's yes because it is a semi-permanent change to his hardware.
constexpr int kPackDesignMah = 750;
// Accept a 25% spread before crying wolf: a gauge that has learned a slightly
// aged pack is healthy, a gauge that is out by 10x is misconfigured.
constexpr int kDesignTolerancePct = 25;
// Standard command, BQ27220 datasheet: DesignCapacity, 2 bytes, mAh.
constexpr uint8_t kRegDesignCapacity = 0x3C;

i2c_master_dev_handle_t s_device = nullptr;

}  // namespace

esp_err_t sticky_battery_init(i2c_master_bus_handle_t bus)
{
    if (bus == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_device != nullptr) {
        return ESP_OK;
    }

    i2c_device_config_t config = {};
    config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    config.device_address = BQ27220_I2C_ADDR;
    config.scl_speed_hz = kI2cClockHz;

    esp_err_t result = i2c_master_bus_add_device(bus, &config, &s_device);
    if (result != ESP_OK) {
        ESP_LOGE(kTag, "Add I2C device failed: %s", esp_err_to_name(result));
        return result;
    }

    if (!bq27220_probe(s_device)) {
        ESP_LOGE(kTag, "BQ27220 not detected at address 0x%02X",
                 BQ27220_I2C_ADDR);
        i2c_master_bus_rm_device(s_device);
        s_device = nullptr;
        return ESP_ERR_NOT_FOUND;
    }

    ESP_LOGI(kTag, "Initialized: I2C address=0x%02X", BQ27220_I2C_ADDR);
    return ESP_OK;
}

esp_err_t sticky_battery_read(BatteryReading &reading)
{
    if (s_device == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }

    uint16_t percent = 0;
    if (!bq27220_read_state_of_charge(s_device, percent)) {
        ESP_LOGE(kTag, "Failed to read state of charge");
        return ESP_FAIL;
    }

    reading.percent = std::clamp<int>(percent, 0, 100);
    ESP_LOGI(kTag, "Read success: %d%%", reading.percent);
    return ESP_OK;
}

esp_err_t sticky_battery_read_detail(BatteryDetail &detail)
{
    if (s_device == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }

    Bq27220Data_t data;
    if (!bq27220_read_data(s_device, data)) {
        ESP_LOGE(kTag, "Failed to read gauge data block");
        return ESP_FAIL;
    }

    battery_status_t status = {};
    status.full = data.battery_status;

    detail.percent = std::clamp<int>(data.state_of_charge_percent, 0, 100);
    detail.voltage_mv = data.voltage_mv;
    detail.current_ma = data.current_ma;
    detail.average_current_ma = data.average_current_ma;
    detail.remaining_mah = data.remaining_capacity_mah;
    detail.full_charge_mah = data.full_charge_capacity_mah;

    // Read the gauge's own idea of the pack. A failed read leaves design_mah 0
    // and mah_trusted false — "I could not check" and "it disagrees" both mean
    // do not believe the mAh, so collapsing them is honest here.
    uint16_t design = 0;
    detail.pack_mah = kPackDesignMah;
    if (bq27220_read_word(s_device, kRegDesignCapacity, design)) {
        detail.design_mah = design;
        const int slack = kPackDesignMah * kDesignTolerancePct / 100;
        detail.mah_trusted = design > 0 &&
                             std::abs(static_cast<int>(design) - kPackDesignMah) <= slack;
    } else {
        ESP_LOGW(kTag, "DesignCapacity (0x%02X) read failed — mAh unverifiable",
                 kRegDesignCapacity);
    }
    if (!detail.mah_trusted)
        ESP_LOGW(kTag, "gauge design capacity %d mAh vs %d mAh pack — absolute "
                       "mAh NOT trustworthy (percent is separate)",
                 detail.design_mah, kPackDesignMah);
    detail.health_pct = std::clamp<int>(data.state_of_health_percent, 0, 100);
    detail.cycle_count = data.cycle_count;
    detail.temperature_c = data.temperature_c;
    // DSG == 1 means the pack is discharging, so charging is its complement.
    detail.charging = !status.DSG;
    detail.full = status.FC;
    detail.present = status.BATTPRES;
    detail.status_bits = data.battery_status;

    ESP_LOGI(kTag,
             "Detail: %d%% %dmV %dmA %s status=0x%04X",
             detail.percent, detail.voltage_mv, detail.current_ma,
             detail.charging ? "charging" : "discharging", detail.status_bits);
    return ESP_OK;
}
