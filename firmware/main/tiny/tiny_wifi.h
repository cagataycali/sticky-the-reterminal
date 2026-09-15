// tiny_wifi — STA bring-up from tiny_config networks[] (ordered roaming list,
// Nicla semantics: try each in order, first association wins).
#pragma once
#include "esp_err.h"
#include "tiny/tiny_config.h"
#ifdef __cplusplus
extern "C" {
#endif
esp_err_t tiny_wifi_connect(const tiny_config_t *cfg); // blocks <=15s/network
bool      tiny_wifi_is_up(void);
// Live AP scan for the settings Wi-Fi page (M10.2). Blocks 2-4s. Requires the
// STA to be initialized (i.e. we booted provisioned); returns the strongest
// `max` APs, deduped by ssid. open=true means no password needed.
typedef struct { char ssid[33]; int rssi; bool open; } tiny_scan_ap_t;
// total (optional) = unique APs actually in the air, so the page can say
// "+N more" instead of pretending the window is the world.
esp_err_t tiny_wifi_scan(tiny_scan_ap_t *out, int max, int *count, int *total);
// Did the LAST roaming walk try `ssid` and fail? reason = the final
// wifi disconnect reason (15/202/204 ~ wrong password, 201 = not found).
bool tiny_wifi_walk_failed(const char *ssid, int *reason);
#ifdef __cplusplus
}
#endif
