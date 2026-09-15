// tiny_upload — POST /api/media, device-token auth IN BODY (API_CONTRACT.md).
// Whole-body buffering in PSRAM: b64 of a 15s WAV (~480KB) is ~640KB — fine
// against 8MB PSRAM, and esp_http_client sets Content-Length for us.
#include "tiny/tiny_upload.h"

#include <string.h>

#include "cJSON.h"
#include "esp_check.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "mbedtls/base64.h"
#include "tiny/tiny_config.h"

static const char *TAG = "tiny_upload";

typedef struct { char *buf; int len, cap; } sink_t;

static esp_err_t evt(esp_http_client_event_t *e) {
    if (e->event_id == HTTP_EVENT_ON_DATA) {
        sink_t *s = (sink_t *)e->user_data;
        if (s && s->len + e->data_len < s->cap) {
            memcpy(s->buf + s->len, e->data, e->data_len);
            s->len += e->data_len;
            s->buf[s->len] = 0;
        }
    }
    return ESP_OK;
}

extern "C" esp_err_t tiny_upload_media(const uint8_t *data, size_t len,
                                       const char *content_type,
                                       char *out_url, size_t out_url_cap) {
    if (!data || !len || !out_url || out_url_cap < 8) return ESP_ERR_INVALID_ARG;
    out_url[0] = 0;

    // PRE-FLIGHT (P0 hunt, 2026-08-26): a fresh TLS session peaks ~40KB on
    // the INTERNAL rail, and it may race heartbeat/relay TLS. On 0.23.x we
    // measured the floor at ~41KB — and some esp-tls/lwip alloc-fail paths
    // abort() instead of returning NO_MEM. Refusing here turns a suspected
    // panic into a polite, retryable error the caller can report in words.
    {
        const size_t ifree =
            heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        if (ifree < 60 * 1024) {
            ESP_LOGW(TAG,
                     "upload refused: internal_free=%u < 60KB — TLS session "
                     "would risk the rail; retry when quieter", (unsigned)ifree);
            return ESP_ERR_NO_MEM;
        }
    }

    tiny_config_t cfg;
    ESP_RETURN_ON_ERROR(tiny_config_load(&cfg), TAG, "cfg");
    if (!cfg.device_id[0] || !cfg.token[0]) return ESP_ERR_INVALID_STATE;

    // b64 in PSRAM.
    size_t b64_cap = 4 * ((len + 2) / 3) + 4, b64_len = 0;
    unsigned char *b64 =
        (unsigned char *)heap_caps_malloc(b64_cap, MALLOC_CAP_SPIRAM);
    if (!b64) return ESP_ERR_NO_MEM;
    if (mbedtls_base64_encode(b64, b64_cap, &b64_len, data, len) != 0) {
        free(b64);
        return ESP_FAIL;
    }

    // Body: hand-assembled so the payload stays in PSRAM (cJSON would copy).
    const char *fmt = "{\"deviceId\":\"%s\",\"token\":\"%s\",\"contentType\":\"%s\",\"data\":\"";
    size_t head_cap = 256 + strlen(content_type);
    size_t body_cap = head_cap + b64_len + 4;
    char *body = (char *)heap_caps_malloc(body_cap, MALLOC_CAP_SPIRAM);
    if (!body) { free(b64); return ESP_ERR_NO_MEM; }
    int off = snprintf(body, head_cap, fmt, cfg.device_id, cfg.token, content_type);
    // snprintf returns the length it WANTED, not the length it wrote, and `off`
    // is used as a memcpy destination offset on the very next line. A truncated
    // prefix therefore lands b64_len bytes past the end of body, which is only
    // head_cap + b64_len + 4 long — a silent PSRAM heap corruption whose crash
    // would surface somewhere else entirely.
    //
    // It does not fire today, and I am not going to pretend it does: the
    // format's literals are 51 B, and the inputs are bounded by device_id[48] +
    // token[80] + the longest content_type any caller passes ("image/png"),
    // i.e. 186 B into a head_cap of 265. This is a trap, not a live overflow.
    // It arms itself the day token[] grows, api credentials get longer, or a
    // caller passes a content_type from the network. The check costs nothing and
    // removes the whole class, which is the point — this is the third instance
    // of unchecked-snprintf-as-offset found in this codebase.
    if (off < 0 || (size_t)off >= head_cap) {
        ESP_LOGE(TAG, "media prefix wants %d B but head_cap is %u B — refusing "
                      "the upload rather than memcpy past the buffer",
                 off, (unsigned)head_cap);
        free(body);
        free(b64);
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(body + off, b64, b64_len);
    off += (int)b64_len;
    body[off++] = '"'; body[off++] = '}'; body[off] = 0;
    free(b64);

    char url[160];
    snprintf(url, sizeof url, "%s/api/media", cfg.api);
    static char resp[1024];
    sink_t sink = { resp, 0, sizeof resp };
    resp[0] = 0;

    esp_http_client_config_t hc = {};
    hc.url = url;
    hc.method = HTTP_METHOD_POST;
    hc.timeout_ms = 30000;
    hc.crt_bundle_attach = esp_crt_bundle_attach;
    hc.event_handler = evt;
    hc.user_data = &sink;
    esp_http_client_handle_t c = esp_http_client_init(&hc);
    if (!c) { free(body); return ESP_FAIL; }
    esp_http_client_set_header(c, "Content-Type", "application/json");
    esp_http_client_set_post_field(c, body, off);
    esp_err_t err = esp_http_client_perform(c);
    int st = esp_http_client_get_status_code(c);
    esp_http_client_cleanup(c);
    free(body);
    ESP_LOGI(TAG, "media %u bytes (%s) -> %s %d", (unsigned)len, content_type,
             esp_err_to_name(err), st);
    if (err != ESP_OK || st != 200) return err != ESP_OK ? err : ESP_FAIL;

    cJSON *root = cJSON_Parse(resp);
    const cJSON *u = root ? cJSON_GetObjectItem(root, "url") : NULL;
    if (cJSON_IsString(u)) strlcpy(out_url, u->valuestring, out_url_cap);
    if (root) cJSON_Delete(root);
    return out_url[0] ? ESP_OK : ESP_FAIL;
}
