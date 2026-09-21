// tiny_beacon — TinyBeacon v2 advertiser + Tiny Catalog GATT (NimBLE).
// Spec: tinyai-id docs/ble-mesh/SPEC.md (header §2, packet layout §2.4,
// catalog §3, privacy §4). A Sticky is a DEVICE, not a person: it advertises by
// default with kind/caps/tool-count/session; its display name rides only when
// `social` is set (the owner named it, so the name may carry the owner).
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    const char *name;        // display name (scan-response) — only when social
    const char *device_id;   // hashed (sha256[0..8] hex) into the identity JSON; NEVER on air
    const char *fw;          // firmware version string for identity JSON
    const char *const *tools; int tool_count;   // verbs served by the catalog (paged, 6/page)
    bool social;             // name + handle visible to strangers (default false)
    bool battery;            // flags.battery
    uint16_t caps;           // TinyBeaconV2.Caps bits (speaker|display|imu|wifi|env|touch|eink…)
    int8_t rssi1m;           // measured RSSI at 1 m for this board (Sticky bench: -61)
} tiny_beacon_cfg_t;

// Call once at boot BEFORE the first tiny_ble_scan (the GATT service must be
// registered before the NimBLE host starts). Draws a session id, starts
// advertising, rotates the session every 15 min. Idempotent.
esp_err_t tiny_beacon_start(const tiny_beacon_cfg_t *cfg);
esp_err_t tiny_beacon_stop(void);
bool      tiny_beacon_running(void);
uint16_t  tiny_beacon_session(void);

// Internal hooks used by tiny_ble.cpp (host bring-up order).
void tiny_beacon_gatt_register(void);   // ble_svc_gap/gatt init + catalog svc (before host start)
void tiny_beacon_on_sync(void);         // begin advertising once host<->controller synced

#ifdef __cplusplus
}
#endif
