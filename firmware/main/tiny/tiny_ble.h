// tiny_ble — BLE observer for the settings Bluetooth page (M10.3).
// SCAN-ONLY v1 by the honesty rule: the page promises a list, not pairing.
// NimBLE host, observer role only (no central/peripheral compiled in).
// Later (#775): this grows into the iOS-app proximity channel.
#pragma once
#include "esp_err.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    char name[24];   // advertised name, or "" when the device sends none
    char addr[18];   // aa:bb:cc:dd:ee:ff
    int  rssi;
} tiny_ble_dev_t;
// Blocking scan (~scan_ms, default 4000 when 0). Strongest-first, deduped by
// address. First call brings the NimBLE stack up (~1s extra, once).
esp_err_t tiny_ble_scan(tiny_ble_dev_t *out, int max, int *count, int scan_ms);
#ifdef __cplusplus
}
#endif
