// tiny_config — NVS-backed identity + wifi, port of Nicla /flash/tiny.json.
// Schema (docs/API_CONTRACT.md): networks[] ordered roaming list, device_id,
// token (tind_, the ONLY credential on this device), api, name.
// merge() semantics ported verbatim: networks upsert by ssid, scalars overwrite,
// never store an account bearer.
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#ifdef __cplusplus
extern "C" {
#endif

#define TINY_MAX_NETWORKS 8
typedef struct { char ssid[33]; char key[65]; } tiny_net_t;
typedef struct {
    tiny_net_t networks[TINY_MAX_NETWORKS];
    int        network_count;
    char       device_id[48];
    char       token[80];
    char       api[96];        // default https://tiny.technology
    char       name[48];
} tiny_config_t;

esp_err_t tiny_config_load(tiny_config_t *out);          // absent fields -> defaults
esp_err_t tiny_config_merge_json(const char *json);      // POST /setup body -> NVS
bool      tiny_config_is_provisioned(const tiny_config_t *c); // id+token+>=1 network
esp_err_t tiny_config_forget_wifi(void);                 // reprovision: keep identity
esp_err_t tiny_config_wifi_promote(const char *ssid);    // one slot up the walk
// Silent mode: one u8 in the same namespace. get defaults to
// false when the key was never written (a fresh device chirps).
bool      tiny_config_silent_get(void);
esp_err_t tiny_config_silent_set(bool silent);
// rotation follow-gravity: absent = true (auto)
bool      tiny_config_rotauto_get(void);
esp_err_t tiny_config_rotauto_set(bool on);
// autosleep idle budget, minutes: 5/15/30/60, absent = 30
uint8_t   tiny_config_sleepidle_get(void);
esp_err_t tiny_config_sleepidle_set(uint8_t minutes);
// beep volume tier: absent = false (normal/full voice)
bool      tiny_config_beepsoft_get(void);
esp_err_t tiny_config_beepsoft_set(bool soft);
// gallery manifest: url slots survive reboot
esp_err_t tiny_config_gallery_save(const void *urls, size_t len,
                                   uint8_t count, uint8_t index);
esp_err_t tiny_config_gallery_save_index(uint8_t index);
esp_err_t tiny_config_gallery_load(void *urls, size_t cap,
                                   uint8_t *count, uint8_t *index);
esp_err_t tiny_config_forget_identity(void);             // heartbeat-401: wipe token
#ifdef __cplusplus
}
#endif
