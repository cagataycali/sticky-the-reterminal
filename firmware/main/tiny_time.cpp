// tiny_time — see tiny/tiny_time.h. UTC everywhere, on purpose.
#include "tiny/tiny_time.h"
#include "tiny/tiny_display.h"

#include <cstdio>
#include <cstring>
#include <ctime>
#include <sys/time.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_netif_sntp.h"
#include "esp_sntp.h"
#include "sticky_rtc.h"

static const char *TAG = "tiny_time";

static bool s_sntp_started = false;
static bool s_synced = false;
static bool s_from_rtc = false;

static void tm_from_rtc(const RtcDateTime &r, struct tm *out) {
    memset(out, 0, sizeof *out);
    out->tm_year = r.year - 1900;
    out->tm_mon = r.month - 1;
    out->tm_mday = r.day;
    out->tm_hour = r.hour;
    out->tm_min = r.minute;
    out->tm_sec = r.second;
    out->tm_isdst = 0;
}

extern "C" esp_err_t tiny_time_init_from_rtc(void) {
    RtcDateTime r = {};
    const esp_err_t e = sticky_rtc_read(r);
    if (e != ESP_OK) {
        ESP_LOGW(TAG, "RTC not usable (%s) — clock stays unset until SNTP",
                 esp_err_to_name(e));
        return e;
    }

    // The RTC holds UTC by our own convention (sticky_rtc_write), so timegm is
    // the right conversion. TZ is fixed to UTC at startup so mktime/timegm and
    // every strftime below agree no matter what else the app does.
    setenv("TZ", "UTC0", 1);
    tzset();
    struct tm t = {};
    tm_from_rtc(r, &t);
    const time_t secs = mktime(&t);
    if (secs <= 0) {
        ESP_LOGW(TAG, "RTC value did not convert to a time_t");
        return ESP_ERR_INVALID_RESPONSE;
    }
    struct timeval tv = {};
    tv.tv_sec = secs;
    if (settimeofday(&tv, nullptr) != 0) {
        ESP_LOGE(TAG, "settimeofday from RTC failed");
        return ESP_FAIL;
    }
    s_from_rtc = true;
    ESP_LOGI(TAG, "system clock set from RTC: %04d-%02d-%02d %02d:%02d:%02dZ",
             r.year, r.month, r.day, r.hour, r.minute, r.second);
    return ESP_OK;
}

// Waits for SNTP off the boot path. Reason: with only a 6 s
// blocking wait, the first exchange (DNS + UDP round trip) regularly misses the
// window — IDF's own background sync then corrects the system clock while our
// s_synced stayed false, so `clock_source` reported "rtc" for a clock that had
// actually come from the network, and the RTC write-back never ran. A flag that
// can be wrong about its own provenance is worse than no flag.
static void sntp_waiter_task(void *arg) {
    const int rounds = 20;  // 20 x 15 s = 5 min of patience, then give up
    for (int i = 0; i < rounds; ++i) {
        if (esp_netif_sntp_sync_wait(pdMS_TO_TICKS(15000)) != ESP_OK) continue;
        s_synced = true;
        time_t now = 0;
        time(&now);
        struct tm t = {};
        gmtime_r(&now, &t);
        ESP_LOGI(TAG, "sntp ok (round %d): %04d-%02d-%02d %02d:%02d:%02dZ", i + 1,
                 t.tm_year + 1900, t.tm_mon + 1, t.tm_mday, t.tm_hour, t.tm_min,
                 t.tm_sec);
        RtcDateTime r = {};
        r.year = t.tm_year + 1900;
        r.month = t.tm_mon + 1;
        r.day = t.tm_mday;
        r.hour = t.tm_hour;
        r.minute = t.tm_min;
        r.second = t.tm_sec;
        const esp_err_t rw = sticky_rtc_write(r);
        ESP_LOGI(TAG, "rtc write-back: %s", esp_err_to_name(rw));
        // The glance cluster (0.14.21) omits the clock until sync — honest,
        // but it means the card on the glass right now is missing its clock
        // and would stay that way until the NEXT render, which on e-ink can
        // be hours. Redraw the current card once, here, the moment the claim
        // "synced" becomes true. INVALID_STATE (no card yet — boot race)
        // needs no handling: the first card will draw its own clock.
        (void)tiny_display_rerender();
        break;
    }
    if (!s_synced)
        ESP_LOGW(TAG, "sntp never answered — clock stays on the RTC");
    vTaskDelete(nullptr);
}

extern "C" esp_err_t tiny_time_sync_sntp(int timeout_ms) {
    if (!s_sntp_started) {
        esp_sntp_config_t cfg = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
        cfg.start = true;
        cfg.server_from_dhcp = false;
        const esp_err_t e = esp_netif_sntp_init(&cfg);
        if (e != ESP_OK) {
            ESP_LOGE(TAG, "sntp init: %s", esp_err_to_name(e));
            return e;
        }
        s_sntp_started = true;
    }

    const esp_err_t w = esp_netif_sntp_sync_wait(pdMS_TO_TICKS(timeout_ms));
    if (w != ESP_OK) {
        // Not a failure — just slower than the boot path is willing to wait.
        // Keep waiting on a task so the sync is still recorded and still
        // reaches the RTC.
        ESP_LOGI(TAG, "sntp not in %d ms (%s) — waiting off the boot path",
                 timeout_ms, esp_err_to_name(w));
        if (// 8 KB, not 3: the sync path now ends in tiny_display_rerender(), which
        // parses and draws a whole card on THIS task (same lesson the rotation
        // commit path learned — cJSON + text primitives want ~6 KB). The task
        // deletes itself after one sync, so the cost is transient.
        xTaskCreate(sntp_waiter_task, "tiny_time", 8192, nullptr, 3,
                        nullptr) != pdPASS)
            ESP_LOGE(TAG, "could not start the sntp waiter task");
        return w;
    }

    s_synced = true;
    time_t now = 0;
    time(&now);
    struct tm t = {};
    gmtime_r(&now, &t);
    ESP_LOGI(TAG, "sntp ok: %04d-%02d-%02d %02d:%02d:%02dZ", t.tm_year + 1900,
             t.tm_mon + 1, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec);

    // Write it back so the next boot has a real clock before wifi exists.
    RtcDateTime r = {};
    r.year = t.tm_year + 1900;
    r.month = t.tm_mon + 1;
    r.day = t.tm_mday;
    r.hour = t.tm_hour;
    r.minute = t.tm_min;
    r.second = t.tm_sec;
    const esp_err_t rw = sticky_rtc_write(r);
    if (rw != ESP_OK)
        ESP_LOGW(TAG, "RTC write-back failed: %s", esp_err_to_name(rw));
    return ESP_OK;
}

extern "C" bool tiny_time_is_synced(void) { return s_synced; }

extern "C" const char *tiny_time_source(void) {
    if (s_synced) return "sntp";
    if (s_from_rtc) return "rtc";
    return "unset";
}

extern "C" void tiny_time_utc_string(char *out, size_t cap) {
    if (!out || cap == 0) return;
    out[0] = 0;
    if (!s_synced && !s_from_rtc) return;  // 1970 is not a time worth printing
    time_t now = 0;
    time(&now);
    struct tm t = {};
    gmtime_r(&now, &t);
    snprintf(out, cap, "%04d-%02d-%02d %02d:%02d:%02dZ", t.tm_year + 1900,
             t.tm_mon + 1, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec);
}
