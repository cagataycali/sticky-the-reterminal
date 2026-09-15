// tiny_provision — first-boot softAP portal. Same /setup contract as the
// Nicla (JSON body {device_id, token, networks[]}) plus a two-form HTML page
// for a phone browser: Wi-Fi alone, or identity alone, each a valid step. SSID tiny-XXXX (FNV-1a over full MAC — serial-suffix
// tags collide, measured on Nicla lot), key "tinysetup", 192.168.4.1.
// GET /info -> identity JSON; POST /setup -> tiny_config_merge_json -> reboot.
// While portal is up: e-ink shows QR (WIFI:S:...;T:WPA;P:tinysetup;;) + steps.
#pragma once
#include "esp_err.h"
#ifdef __cplusplus
extern "C" {
#endif
esp_err_t tiny_provision_start(void);   // first boot, no networks: APSTA + portal (no card —
                                        // tiny_onboard paints the wi-fi page)
esp_err_t tiny_provision_portal_apsta(void);  // portal over a live STA, no card — the
                                              // onboarding link/pair steps' phone path
esp_err_t tiny_provision_start_rescue(void);  // APSTA overlay + "re-provision me" card —
                                              // the 401-revoked recovery path
const char *tiny_provision_ssid(void);  // "tiny-xxxx" (computed on first call)
const char *tiny_provision_key(void);   // the softAP passphrase
#ifdef __cplusplus
}
#endif
