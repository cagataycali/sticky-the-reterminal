// tiny_sensors — see tiny/tiny_sensors.h for the two rules (null over guess,
// no network).
#include "tiny/tiny_sensors.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "board.h"
#include "cJSON.h"
#include "esp_log.h"
#include "sticky_battery.h"
#include "sticky_imu.h"
#include "sticky_rtc.h"
#include "sticky_sht40.h"
#include "tiny/tiny_time.h"
#include "tiny/tiny_display.h"

static const char *TAG = "tiny_sensors";

// Read state is recomputed on every call — the point of a sensor verb is the
// value NOW. These flags only record whether the chip ever answered at init,
// so a missing chip is reported as "not present" instead of "read failed".
static bool s_sht40_up = false;
static bool s_imu_up = false;
static bool s_rtc_up = false;

extern "C" esp_err_t tiny_sensors_init(void) {
    i2c_master_bus_handle_t bus = board_sensor_i2c_bus();
    if (!bus) {
        ESP_LOGE(TAG, "sensor I2C bus is null — board_init() must run first");
        return ESP_ERR_INVALID_STATE;
    }

    esp_err_t e = sticky_sht40_init(bus);
    s_sht40_up = (e == ESP_OK);
    ESP_LOGI(TAG, "sht40 init: %s", esp_err_to_name(e));

    e = sticky_imu_init(bus);
    s_imu_up = (e == ESP_OK);
    ESP_LOGI(TAG, "imu init: %s", esp_err_to_name(e));

    e = sticky_rtc_init(bus);
    s_rtc_up = (e == ESP_OK);
    ESP_LOGI(TAG, "rtc init: %s", esp_err_to_name(e));

    return (s_sht40_up || s_imu_up || s_rtc_up) ? ESP_OK : ESP_FAIL;
}

// One place where a failed read becomes a named error, so every reader of the
// JSON and every reader of the card sees the same story.
static void note_error(cJSON *errors, const char *sensor, esp_err_t e) {
    char msg[64];
    snprintf(msg, sizeof msg, "%s: %s", sensor, esp_err_to_name(e));
    cJSON_AddItemToArray(errors, cJSON_CreateString(msg));
    ESP_LOGW(TAG, "%s", msg);
}

extern "C" esp_err_t tiny_sensors_json(char *out, size_t cap) {
    if (!out || cap == 0) return ESP_ERR_INVALID_ARG;

    cJSON *s = cJSON_CreateObject();
    cJSON *errors = cJSON_CreateArray();
    if (!s || !errors) {
        if (s) cJSON_Delete(s);
        if (errors) cJSON_Delete(errors);
        return ESP_ERR_NO_MEM;
    }

    // ---- SHT40: ambient ----
    Sht40Reading air = {};
    bool have_air = false;
    if (s_sht40_up) {
        const esp_err_t e = sticky_sht40_read(air);
        have_air = (e == ESP_OK);
        if (!have_air) note_error(errors, "sht40", e);
    } else {
        note_error(errors, "sht40", ESP_ERR_NOT_FOUND);
    }
    if (have_air) {
        cJSON_AddNumberToObject(s, "temperature_c", air.temperature_c);
        cJSON_AddNumberToObject(s, "humidity_pct", air.humidity_percent);
    } else {
        cJSON_AddNullToObject(s, "temperature_c");
        cJSON_AddNullToObject(s, "humidity_pct");
    }

    // ---- IMU: how the sticky is hanging ----
    StickyImuState imu = {};
    bool have_imu = false;
    if (s_imu_up) {
        const esp_err_t e = sticky_imu_read(imu);
        have_imu = (e == ESP_OK && imu.valid);
        if (!have_imu) note_error(errors, "imu", e);
    } else {
        note_error(errors, "imu", ESP_ERR_NOT_FOUND);
    }
    if (have_imu) {
        cJSON_AddStringToObject(s, "orientation",
                                sticky_imu_orientation_name(imu.orientation));
        cJSON *a = cJSON_CreateObject();
        cJSON_AddNumberToObject(a, "x", imu.acceleration_x_g);
        cJSON_AddNumberToObject(a, "y", imu.acceleration_y_g);
        cJSON_AddNumberToObject(a, "z", imu.acceleration_z_g);
        cJSON_AddItemToObject(s, "accel_g", a);
    } else {
        cJSON_AddNullToObject(s, "orientation");
        cJSON_AddNullToObject(s, "accel_g");
    }
    // The gyro used to be a permanent null with errors:[] — honest but
    // unexplained, which the spec's own rule forbids. It is
    // now really read: powered up for ~110 ms, sampled, powered down.
    StickyGyroReading gyro = {};
    bool have_gyro = false;
    if (s_imu_up) {
        const esp_err_t e = sticky_imu_read_gyro(gyro);
        have_gyro = (e == ESP_OK && gyro.valid);
        if (!have_gyro) note_error(errors, "gyro", e);
    } else {
        note_error(errors, "gyro", ESP_ERR_NOT_FOUND);
    }
    if (have_gyro) {
        cJSON *g = cJSON_CreateObject();
        cJSON_AddNumberToObject(g, "x", gyro.x_dps);
        cJSON_AddNumberToObject(g, "y", gyro.y_dps);
        cJSON_AddNumberToObject(g, "z", gyro.z_dps);
        cJSON_AddItemToObject(s, "gyro_dps", g);
    } else {
        cJSON_AddNullToObject(s, "gyro_dps");
    }

    // ---- RTC: the wall clock that survives a reboot ----
    RtcDateTime now = {};
    bool have_rtc = false;
    if (s_rtc_up) {
        const esp_err_t e = sticky_rtc_read(now);
        have_rtc = (e == ESP_OK);
        if (!have_rtc) note_error(errors, "rtc", e);
    } else {
        note_error(errors, "rtc", ESP_ERR_NOT_FOUND);
    }
    if (have_rtc) {
        char stamp[24];
        snprintf(stamp, sizeof stamp, "%04d-%02d-%02d %02d:%02d:%02d",
                 now.year, now.month, now.day, now.hour, now.minute,
                 now.second);
        cJSON_AddStringToObject(s, "rtc", stamp);
    } else {
        cJSON_AddNullToObject(s, "rtc");
    }
    // A bare timestamp with no zone and no provenance is unusable: before SNTP
    // existed this field read 40 minutes off local and nothing said why.
    cJSON_AddStringToObject(s, "rtc_zone", "UTC");
    cJSON_AddStringToObject(s, "clock_source", tiny_time_source());
    char sysz[28];
    tiny_time_utc_string(sysz, sizeof sysz);
    if (sysz[0]) cJSON_AddStringToObject(s, "system_time", sysz);
    else         cJSON_AddNullToObject(s, "system_time");

    // ---- BQ27220: the pack ----
    BatteryDetail bat = {};
    const esp_err_t be = sticky_battery_read_detail(bat);
    const bool have_bat = (be == ESP_OK);
    if (!have_bat) note_error(errors, "bq27220", be);
    if (have_bat) {
        cJSON *b = cJSON_CreateObject();
        cJSON_AddNumberToObject(b, "percent", bat.percent);
        cJSON_AddNumberToObject(b, "voltage_mv", bat.voltage_mv);
        cJSON_AddNumberToObject(b, "current_ma", bat.current_ma);
        cJSON_AddNumberToObject(b, "average_current_ma",
                                bat.average_current_ma);
        cJSON_AddNumberToObject(b, "remaining_mah", bat.remaining_mah);
        cJSON_AddNumberToObject(b, "full_charge_mah", bat.full_charge_mah);
        // The two mAh fields above are only as good as the gauge's design
        // capacity, so they now travel WITH it. `mah_trusted:false` is the
        // field saying "read percent instead" — a number that knows it is
        // wrong beats a number that looks right.
        cJSON_AddNumberToObject(b, "design_mah", bat.design_mah);
        cJSON_AddNumberToObject(b, "pack_mah", bat.pack_mah);
        cJSON_AddBoolToObject(b, "mah_trusted", bat.mah_trusted);
        cJSON_AddNumberToObject(b, "health_pct", bat.health_pct);
        cJSON_AddNumberToObject(b, "cycle_count", bat.cycle_count);
        cJSON_AddNumberToObject(b, "temperature_c", bat.temperature_c);
        cJSON_AddBoolToObject(b, "charging", bat.charging);
        cJSON_AddBoolToObject(b, "full", bat.full);
        cJSON_AddBoolToObject(b, "present", bat.present);
        cJSON_AddItemToObject(s, "battery", b);
        // A misconfigured gauge is a real fault, so it belongs in errors[]
        // where a reader already looks for faults — not only in a flag they
        // have to know to check.
        if (!bat.mah_trusted) {
            char msg[96];
            snprintf(msg, sizeof msg,
                     "bq27220: design %d mAh vs %d mAh pack - mAh untrusted",
                     bat.design_mah, bat.pack_mah);
            cJSON_AddItemToArray(errors, cJSON_CreateString(msg));
        }
    } else {
        cJSON_AddNullToObject(s, "battery");
    }

    cJSON_AddItemToObject(s, "errors", errors);

    // A sentence for the human reading the relay transcript. `left` is what is
    // still writable: snprintf returns what it WOULD have written, so adding
    // its return blindly walks the cursor past the end of the buffer.
    char summary[224];
    size_t n = 0;
    const auto append = [&](const char *fmt, auto... a) {
        if (n >= sizeof summary - 1) return;
        const int w = snprintf(summary + n, sizeof summary - n, fmt, a...);
        if (w > 0) n = (size_t)w >= sizeof summary - n ? sizeof summary - 1
                                                       : n + (size_t)w;
    };
    append("%s", "sensors:");
    if (have_air)
        append(" %.1fC %.0f%%RH", (double)air.temperature_c,
               (double)air.humidity_percent);
    if (have_imu)
        append(" %s", sticky_imu_orientation_name(imu.orientation));
    if (have_gyro)
        append(" gyro %.1f/%.1f/%.1f dps", (double)gyro.x_dps,
               (double)gyro.y_dps, (double)gyro.z_dps);
    if (have_rtc)
        append(" rtc %02d:%02dZ(%s)", now.hour, now.minute, tiny_time_source());
    if (have_bat)
        append(" %d%% %dmV %s", bat.percent, bat.voltage_mv,
               bat.charging ? "charging" : "on battery");
    if (cJSON_GetArraySize(errors) > 0)
        append(" (%d unread)", cJSON_GetArraySize(errors));
    cJSON_AddStringToObject(s, "summary", summary);

    char *js = cJSON_PrintUnformatted(s);
    cJSON_Delete(s);
    if (!js) return ESP_ERR_NO_MEM;
    // strlcpy TRUNCATES and tells you so in its return value. Ignoring that
    // return shipped a defect on 2026-08-27: three new battery fields pushed
    // this JSON past a 768-byte caller buffer, so `sensors` replied a string cut
    // mid-token — INVALID JSON — while returning ESP_OK. A truncated reply is
    // not a smaller truth; it is a parse error at the other end, and it is worse
    // than no reply because it looks like data. So: refuse, say what it would
    // have taken, and let the caller answer with something that parses.
    const size_t need = strlcpy(out, js, cap);
    free(js);
    if (need >= cap) {
        ESP_LOGE(TAG, "sensors json needs %u bytes, buffer is %u — refusing to "
                      "return truncated JSON", (unsigned)need + 1, (unsigned)cap);
        snprintf(out, cap,
                 "{\"error\":\"sensors json truncated\",\"need_bytes\":%u,"
                 "\"buffer_bytes\":%u}", (unsigned)need + 1, (unsigned)cap);
        return ESP_ERR_INVALID_SIZE;
    }
    return ESP_OK;
}

extern "C" esp_err_t tiny_sensors_render_card(void) {
    // Read once, paint what we read — the card must not disagree with the JSON,
    // so both go through the same reads in the same order.
    Sht40Reading air = {};
    const bool have_air = s_sht40_up && sticky_sht40_read(air) == ESP_OK;
    StickyImuState imu = {};
    const bool have_imu = s_imu_up && sticky_imu_read(imu) == ESP_OK && imu.valid;
    RtcDateTime now = {};
    const bool have_rtc = s_rtc_up && sticky_rtc_read(now) == ESP_OK;
    BatteryDetail bat = {};
    const bool have_bat = sticky_battery_read_detail(bat) == ESP_OK;

    cJSON *c = cJSON_CreateObject();
    if (!c) return ESP_ERR_NO_MEM;
    cJSON_AddStringToObject(c, "type", "kv");
    cJSON_AddStringToObject(c, "title", "Sensors");
    cJSON_AddStringToObject(c, "card_id", "sensors");
    // The renderer's key is "rows" (tiny_display.cpp:346) — "kv" is the TYPE.
    // Getting this wrong renders a title, a footer, buttons and an empty
    // body: a card that looks like it worked. Caught by the screenshot.
    cJSON *kv = cJSON_CreateObject();
    char buf[48];

    if (have_air) {
        snprintf(buf, sizeof buf, "%.1f C", (double)air.temperature_c);
        cJSON_AddStringToObject(kv, "temperature", buf);
        snprintf(buf, sizeof buf, "%.0f %%", (double)air.humidity_percent);
        cJSON_AddStringToObject(kv, "humidity", buf);
    } else {
        // Humidity used to VANISH on failure while temperature
        // said "unread" — the page's own every-row-answers law, broken by
        // its first row. Both answer now. "no reading" is the device's
        // voice; "unread" was the engineer's.
        cJSON_AddStringToObject(kv, "temperature", "no reading");
        cJSON_AddStringToObject(kv, "humidity", "no reading");
    }
    if (have_imu) {
        snprintf(buf, sizeof buf, "%s (%.2f %.2f %.2f g)",
                 sticky_imu_orientation_name(imu.orientation),
                 (double)imu.acceleration_x_g, (double)imu.acceleration_y_g,
                 (double)imu.acceleration_z_g);
        cJSON_AddStringToObject(kv, "orientation", buf);
    } else {
        cJSON_AddStringToObject(kv, "orientation", "no reading");
    }
    if (have_rtc) {
        snprintf(buf, sizeof buf, "%02d:%02d:%02d UTC (%s)", now.hour,
                 now.minute, now.second, tiny_time_source());
        cJSON_AddStringToObject(kv, "rtc", buf);
    } else {
        cJSON_AddStringToObject(kv, "rtc", "no reading");
    }
    if (have_bat) {
        // One row, not two: 5 rows is all that fits above the footer at this
        // body scale, and a 6th row was being dropped silently.
        // Value must fit ONE line: draw_wrapped clamps at max_y, so a longer
        // value loses its tail silently (health used to fall off the glass).
        // Health and cycles ride in the footer, which has a line to spare.
        char power[64];
        snprintf(power, sizeof power, "%d%% %d mV %d mA %s", bat.percent,
                 bat.voltage_mv, bat.current_ma,
                 bat.charging ? "charging" : "on battery");
        cJSON_AddStringToObject(kv, "battery", power);
    } else {
        cJSON_AddStringToObject(kv, "battery", "no reading");
    }
    cJSON_AddItemToObject(c, "rows", kv);
    char foot[96];
    if (have_bat)
        snprintf(foot, sizeof foot,
                 "read live over I2C1 - pack health %d%%, %d cycles, gauge %.0fC",
                 bat.health_pct, bat.cycle_count, (double)bat.temperature_c);
    else
        snprintf(foot, sizeof foot, "read live over I2C1 - no network needed");
    cJSON_AddStringToObject(c, "footer", foot);
    cJSON *btns = cJSON_CreateArray();
    cJSON *b1 = cJSON_CreateObject();
    cJSON_AddStringToObject(b1, "id", "sensors");
    cJSON_AddStringToObject(b1, "label", "Refresh");
    cJSON_AddItemToArray(btns, b1);
    cJSON *b2 = cJSON_CreateObject();
    cJSON_AddStringToObject(b2, "id", "status");
    cJSON_AddStringToObject(b2, "label", "Status");
    cJSON_AddItemToArray(btns, b2);
    // NAVIGATION LAW (UI_SPEC §buttons) — every root page carries
    // [Home]. Settings had it, sensors trusted the bottom-band-up gesture
    // alone: two sibling pages, two laws. Visible affordance wins on a
    // device with no persistent chrome — the first-boot hint card does not
    // persist, and a gesture nobody taught is a wall. (An earlier pass cut
    // REDUNDANT buttons; Home here duplicates nothing visible.)
    cJSON *b3 = cJSON_CreateObject();
    cJSON_AddStringToObject(b3, "id", "home");
    cJSON_AddStringToObject(b3, "label", "Home");
    cJSON_AddItemToArray(btns, b3);
    cJSON_AddItemToObject(c, "buttons", btns);

    char *cs = cJSON_PrintUnformatted(c);
    cJSON_Delete(c);
    if (!cs) return ESP_ERR_NO_MEM;
    const esp_err_t r = tiny_display_render_card(cs);
    free(cs);
    return r;
}
