// tiny_ota — fetch, verify, stage. NEVER judge: tiny_node marks the image valid.
// PUT /api/firmware/manifest {deviceId,token,channel} -> {bundle:{url,sha256}}
// -> manifest -> sha256-checked download -> esp_ota to next slot -> reboot into
// trial (rollback-armed). Artifact hosts pinned: tiny.technology,
// plugin.tiny.technology (measured: media lives on a DIFFERENT host than api).
#pragma once
#include "esp_err.h"
#ifdef __cplusplus
extern "C" {
#endif
esp_err_t tiny_ota_check_and_stage(const char *channel);  // returns after staging; reboot is caller's call
#ifdef __cplusplus
}
#endif
