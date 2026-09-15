// tiny_screenshot — the panel's current framebuffer as a hosted PNG.
// screenshot verb: encode (2-bit gray PNG, stored deflate) → /api/media →
// reply carries the URL. What the OWNER sees is what the PANEL shows —
// the render pipeline's ground truth, reachable from anywhere.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
#ifdef __cplusplus
extern "C" {
#endif
esp_err_t tiny_screenshot_png(uint8_t **out, size_t *out_len);  // caller frees
esp_err_t tiny_screenshot_upload(char *url, size_t url_cap);
#ifdef __cplusplus
}
#endif
