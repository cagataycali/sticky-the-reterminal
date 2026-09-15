#pragma once

#include "driver/i2c_master.h"
#include "esp_err.h"

struct BatteryReading {
    int percent = 0;
};

// Everything the gauge will tell us in one transaction. `charging` is derived
// from the BQ27220 DSG (discharging) status bit, and `current_ma` is reported
// alongside it so the claim is checkable rather than trusted: a positive
// current with charging=false would mean the bit decode is wrong.
struct BatteryDetail {
    int percent = 0;
    int voltage_mv = 0;
    int current_ma = 0;          // signed; negative = discharging
    int average_current_ma = 0;
    int remaining_mah = 0;
    int full_charge_mah = 0;
    // What the GAUGE believes the pack is (DesignCapacity, 0x3C) versus what the
    // hardware actually is. They disagree by 10x on this board, which is why
    // every absolute mAh above is suspect — see kPackDesignMah in the .cpp.
    // design_mah == 0 means the read failed, not "a zero-capacity pack".
    int design_mah = 0;
    int pack_mah = 0;            // nameplate, from docs/hardware.md
    bool mah_trusted = false;    // design_mah agrees with pack_mah?
    int health_pct = 0;
    int cycle_count = 0;
    float temperature_c = 0.0F;  // gauge-internal, not ambient (that is SHT40)
    bool charging = false;
    bool full = false;
    bool present = false;
    uint16_t status_bits = 0;
};

// Attaches the BQ27220 fuel gauge to the shared sensor I2C bus.
esp_err_t sticky_battery_init(i2c_master_bus_handle_t bus);

// Reads the battery state of charge as a percentage from 0 to 100.
esp_err_t sticky_battery_read(BatteryReading &reading);

// Reads the full gauge register set (voltage, current, health, status flags).
esp_err_t sticky_battery_read_detail(BatteryDetail &detail);
