// host shim: nvs_flash_init is a no-op — the file-backed store in nvs.h is
// always "initialized". Exists so modules that self-init NVS (tiny_bootmark's
// stage-1-before-config path) compile against the shim unchanged.
#pragma once
#include "esp_err.h"
static inline esp_err_t nvs_flash_init(void) { return ESP_OK; }
