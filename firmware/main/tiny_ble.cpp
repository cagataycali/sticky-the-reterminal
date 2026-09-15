// tiny_ble — NimBLE observer, scan-only. See tiny_ble.h.
#include "tiny/tiny_ble.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "host/ble_gap.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"

static const char *TAG = "tiny_ble";

static bool s_started = false;
static bool s_synced = false;
static SemaphoreHandle_t s_done = NULL;

// Results table filled by the GAP event callback (NimBLE host task context),
// read by the caller after the scan-complete semaphore fires.
static tiny_ble_dev_t s_devs[16];
static int s_dev_count = 0;

static void on_sync(void) { s_synced = true; }
static void host_task(void *) {
    nimble_port_run();
    nimble_port_freertos_deinit();
}

static void record_adv(const struct ble_gap_disc_desc *d) {
    char addr[18];
    snprintf(addr, sizeof addr, "%02x:%02x:%02x:%02x:%02x:%02x",
             d->addr.val[5], d->addr.val[4], d->addr.val[3],
             d->addr.val[2], d->addr.val[1], d->addr.val[0]);
    // dedupe by address, keep the strongest sighting
    for (int i = 0; i < s_dev_count; ++i) {
        if (strcmp(s_devs[i].addr, addr) == 0) {
            if (d->rssi > s_devs[i].rssi) s_devs[i].rssi = d->rssi;
            // a later ADV may carry the name an earlier one lacked
            if (!s_devs[i].name[0]) {
                struct ble_hs_adv_fields f;
                if (ble_hs_adv_parse_fields(&f, d->data, d->length_data) == 0 &&
                    f.name && f.name_len) {
                    int n = f.name_len < 23 ? f.name_len : 23;
                    memcpy(s_devs[i].name, f.name, n);
                    s_devs[i].name[n] = 0;
                }
            }
            return;
        }
    }
    if (s_dev_count >= (int)(sizeof s_devs / sizeof *s_devs)) return;
    tiny_ble_dev_t *dev = &s_devs[s_dev_count];
    memset(dev, 0, sizeof *dev);
    strcpy(dev->addr, addr);
    dev->rssi = d->rssi;
    struct ble_hs_adv_fields f;
    if (ble_hs_adv_parse_fields(&f, d->data, d->length_data) == 0 &&
        f.name && f.name_len) {
        int n = f.name_len < 23 ? f.name_len : 23;
        memcpy(dev->name, f.name, n);
        dev->name[n] = 0;
    }
    ++s_dev_count;
}

static int gap_event(struct ble_gap_event *ev, void *) {
    switch (ev->type) {
        case BLE_GAP_EVENT_DISC:
            record_adv(&ev->disc);
            return 0;
        case BLE_GAP_EVENT_DISC_COMPLETE:
            if (s_done) xSemaphoreGive(s_done);
            return 0;
        default:
            return 0;
    }
}

static esp_err_t ensure_started(void) {
    if (s_started) return ESP_OK;
    esp_err_t err = nimble_port_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nimble_port_init: %s", esp_err_to_name(err));
        return err;
    }
    ble_hs_cfg.sync_cb = on_sync;
    nimble_port_freertos_init(host_task);
    // wait for host<->controller sync (fast; give it 3s)
    for (int i = 0; i < 300 && !s_synced; ++i) vTaskDelay(pdMS_TO_TICKS(10));
    if (!s_synced) return ESP_ERR_TIMEOUT;
    s_done = xSemaphoreCreateBinary();
    if (!s_done) return ESP_ERR_NO_MEM;
    s_started = true;
    ESP_LOGI(TAG, "NimBLE up (observer)");
    return ESP_OK;
}

extern "C" esp_err_t tiny_ble_scan(tiny_ble_dev_t *out, int max, int *count,
                                   int scan_ms) {
    if (!out || max <= 0 || !count) return ESP_ERR_INVALID_ARG;
    *count = 0;
    esp_err_t err = ensure_started();
    if (err != ESP_OK) return err;
    s_dev_count = 0;

    struct ble_gap_disc_params p = {};
    p.passive = 1;             // observe ADVs; never send scan requests
    p.filter_duplicates = 0;   // we dedupe ourselves (want strongest rssi)
    uint8_t own_addr_type;
    ble_hs_util_ensure_addr(0);
    ble_hs_id_infer_auto(0, &own_addr_type);
    int rc = ble_gap_disc(own_addr_type, scan_ms > 0 ? scan_ms : 4000, &p,
                          gap_event, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "ble_gap_disc rc=%d", rc);
        return ESP_FAIL;
    }
    // wait for DISC_COMPLETE (+1s margin)
    xSemaphoreTake(s_done, pdMS_TO_TICKS((scan_ms > 0 ? scan_ms : 4000) + 1000));

    // strongest-first into the caller's table
    for (int k = 0; k < max; ++k) {
        int best = -1;
        for (int i = 0; i < s_dev_count; ++i) {
            if (s_devs[i].addr[0] == 0) continue;  // consumed
            if (best < 0 || s_devs[i].rssi > s_devs[best].rssi) best = i;
        }
        if (best < 0) break;
        out[*count] = s_devs[best];
        s_devs[best].addr[0] = 0;
        ++*count;
    }
    ESP_LOGI(TAG, "scan: %d devices", *count);
    return ESP_OK;
}
