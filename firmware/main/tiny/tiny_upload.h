// tiny_upload — POST /api/media with device-token auth (in body, part of
// Content-Length). PSRAM-buffered b64 via esp_http_client; 30s timeout.
#pragma once
#include "esp_err.h"
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
// Returns hosted URL into out_url (empty string on failure, never garbage).
esp_err_t tiny_upload_media(const uint8_t *data, size_t len, const char *content_type,
                            char *out_url, size_t out_url_cap);
#ifdef __cplusplus
}
#endif
