// tiny_wifi — STA bring-up. Ordered roaming list, 15s per network (the same
// per-attempt bound the Nicla uses; a hung join must never brick boot).
#include "tiny/tiny_wifi.h"

#include <string.h>

#include "esp_check.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

static const char *TAG = "tiny_wifi";
static EventGroupHandle_t s_events;
static const int GOT_IP = BIT0, FAILED = BIT1;
static volatile bool s_up = false;
static int s_retries = 0;
static volatile uint8_t s_last_reason = 0;

// Per-walk failure log. tiny_wifi_walk_failed() lets the boot path ask
// "was <just-added ssid> tried, and why did it fail?" — the difference between
// "saved ✓" (a lie) and "couldn't join X - wrong password?" (the truth).
typedef struct { char ssid[33]; uint8_t reason; } tiny_fail_rec_t;
static tiny_fail_rec_t s_fails[8];
static int s_fail_count = 0;

static void handler(void *arg, esp_event_base_t base, int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_up = false;
        // Keep the WHY. reason 15/202/204 = handshake/auth (wrong
        // password territory); 201 = AP not found. Without this the walk
        // only knows "failed", and the human gets a silent lie.
        s_last_reason = ((wifi_event_sta_disconnected_t *)data)->reason;
        if (s_retries++ < 2) {
            esp_wifi_connect();
        } else {
            xEventGroupSetBits(s_events, FAILED);
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "got ip " IPSTR, IP2STR(&e->ip_info.ip));
        s_up = true;
        s_retries = 0;   // future drops get their fast retries back
        xEventGroupSetBits(s_events, GOT_IP);
    }
}

// Re-roam: before this task existed, a dropped AP got 2
// quick retries and then the radio sat dark until reboot — fatal for a pocket
// device that WALKS between networks. Every 30s of downlink: reload the saved
// roaming list from NVS (so a `config` merge is picked up with no reboot) and
// walk it in order. tiny_wifi_connect stops/starts the radio itself, and the
// link being down means no other task is mid-socket on it by definition.
static void roam_task(void *) {
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(30000));
        if (s_up) continue;
        tiny_config_t cfg;
        if (tiny_config_load(&cfg) != ESP_OK || cfg.network_count <= 0) continue;
        ESP_LOGI(TAG, "re-roam: link down, walking %d saved network(s)",
                 cfg.network_count);
        tiny_wifi_connect(&cfg);
    }
}

esp_err_t tiny_wifi_connect(const tiny_config_t *cfg) {
    if (cfg->network_count <= 0) return ESP_ERR_INVALID_ARG;
    static bool inited = false;
    if (!inited) {
        ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "netif");
        ESP_RETURN_ON_ERROR(esp_event_loop_create_default(), TAG, "evloop");
        esp_netif_create_default_wifi_sta();
        wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
        ESP_RETURN_ON_ERROR(esp_wifi_init(&init), TAG, "init");
        s_events = xEventGroupCreate();
        ESP_RETURN_ON_ERROR(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                                       handler, NULL), TAG, "h1");
        ESP_RETURN_ON_ERROR(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                                       handler, NULL), TAG, "h2");
        ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "mode");
        xTaskCreate(roam_task, "wifi_roam", 4096, NULL, 3, NULL);
        inited = true;
    }

    s_fail_count = 0;  // fresh failure log per walk
    for (int i = 0; i < cfg->network_count; ++i) {
        const tiny_net_t *n = &cfg->networks[i];
        ESP_LOGI(TAG, "joining \"%s\" (%d/%d)", n->ssid, i + 1, cfg->network_count);
        s_last_reason = 0;
        wifi_config_t wc = {};
        strlcpy((char *)wc.sta.ssid, n->ssid, sizeof wc.sta.ssid);
        strlcpy((char *)wc.sta.password, n->key, sizeof wc.sta.password);
        wc.sta.threshold.authmode = n->key[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
        esp_wifi_stop();
        ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &wc), TAG, "cfg");
        s_retries = 0;
        xEventGroupClearBits(s_events, GOT_IP | FAILED);
        ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "start");
        EventBits_t bits = xEventGroupWaitBits(s_events, GOT_IP | FAILED,
                                               pdFALSE, pdFALSE,
                                               pdMS_TO_TICKS(15000));
        if (bits & GOT_IP) return ESP_OK;
        ESP_LOGW(TAG, "\"%s\" failed (reason %d), trying next", n->ssid,
                 (int)s_last_reason);
        if (s_fail_count < (int)(sizeof s_fails / sizeof s_fails[0])) {
            strlcpy(s_fails[s_fail_count].ssid, n->ssid,
                    sizeof s_fails[s_fail_count].ssid);
            s_fails[s_fail_count].reason = s_last_reason;
            ++s_fail_count;
        }
    }
    esp_wifi_stop();
    return ESP_ERR_WIFI_NOT_CONNECT;
}

bool tiny_wifi_is_up(void) { return s_up; }

extern "C" bool tiny_wifi_walk_failed(const char *ssid, int *reason) {
    if (!ssid || !ssid[0]) return false;
    for (int i = 0; i < s_fail_count; ++i) {
        if (strcmp(s_fails[i].ssid, ssid) == 0) {
            if (reason) *reason = (int)s_fails[i].reason;
            return true;
        }
    }
    return false;
}

// ---- scan (M10.2 settings Wi-Fi page) --------------------------------------
extern "C" esp_err_t tiny_wifi_scan(tiny_scan_ap_t *out, int max, int *count,
                                    int *total) {
    if (!out || max <= 0 || !count) return ESP_ERR_INVALID_ARG;
    *count = 0;
    if (total) *total = 0;
    // Blocking scan; safe while associated (esp_wifi pauses the connection).
    esp_err_t err = esp_wifi_scan_start(NULL, true);
    if (err != ESP_OK) { ESP_LOGW(TAG, "scan start: %s", esp_err_to_name(err)); return err; }
    uint16_t n = 0;
    esp_wifi_scan_get_ap_num(&n);
    if (n == 0) return ESP_OK;
    if (n > 24) n = 24;
    static wifi_ap_record_t recs[24];
    err = esp_wifi_scan_get_ap_records(&n, recs);
    if (err != ESP_OK) return err;
    // Strongest-first, dedupe by ssid (APs advertise per-band duplicates).
    for (int i = 0; i < (int)n && *count < max; ++i) {
        int best = -1;
        for (int j = 0; j < (int)n; ++j) {
            if (!recs[j].ssid[0]) continue;
            bool seen = false;
            for (int k = 0; k < *count; ++k)
                if (!strcmp(out[k].ssid, (const char *)recs[j].ssid)) { seen = true; break; }
            if (seen) continue;
            if (best < 0 || recs[j].rssi > recs[best].rssi) best = j;
        }
        if (best < 0) break;
        strlcpy(out[*count].ssid, (const char *)recs[best].ssid, sizeof out[*count].ssid);
        out[*count].rssi = recs[best].rssi;
        out[*count].open = recs[best].authmode == WIFI_AUTH_OPEN;
        // Consume EVERY record of this ssid, not just the strongest —
        // a dual-band AP leaves a sibling record behind otherwise, and the
        // hidden count would report "+1 more" when nothing was cut. The
        // honesty row must not need its own honesty row.
        for (int j = 0; j < (int)n; ++j)
            if (recs[j].ssid[0] &&
                !strcmp((const char *)recs[j].ssid, out[*count].ssid))
                recs[j].ssid[0] = 0;
        ++*count;
    }
    // The page must not pretend the window is the world. Count the
    // unique ssids that did NOT fit (remaining recs still carry per-band
    // duplicates, so dedupe among themselves before counting).
    int hidden = 0;
    for (int i = 0; i < (int)n; ++i) {
        if (!recs[i].ssid[0]) continue;
        bool dup = false;
        for (int j = 0; j < i; ++j)
            if (recs[j].ssid[0] &&
                !strcmp((const char *)recs[j].ssid, (const char *)recs[i].ssid))
                { dup = true; break; }
        if (!dup) ++hidden;
    }
    if (total) *total = *count + hidden;
    ESP_LOGI(TAG, "scan: %d unique APs shown, %d hidden", *count, hidden);
    return ESP_OK;
}
