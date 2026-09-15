// tiny_ota — fetch, verify, stage. NEVER judge: tiny_node marks the image valid.
//
// Single-file A/B: PUT /api/firmware/manifest {deviceId,token,channel}
// → {bundle:{version,url,sha256}} where url IS the app image (.bin on
// /api/media R2). Stream-download into the next OTA slot while hashing;
// refuse to set the boot partition unless the sha256 matches EXACTLY.
// Rollback safety: CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is on — the new
// image boots as PENDING_VERIFY and tiny_node marks it valid only after its
// first heartbeat 200 (a build that can't reach home rolls back by itself).
#include "tiny/tiny_ota.h"

#include <limits.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_check.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "mbedtls/sha256.h"
#include "tiny/tiny_config.h"
#include "tiny/tiny_version.h"

static const char *TAG = "tiny_ota";

// Artifact hosts pinned (API_CONTRACT.md): media lives on a DIFFERENT host
// than the api, so both are legitimate; nothing else is.
static bool host_allowed(const char *url) {
    return strncmp(url, "https://tiny.technology/", 24) == 0 ||
           strncmp(url, "https://plugin.tiny.technology/", 31) == 0;
}

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

// PUT the channel pointer, fill bundle fields. 200+bundle → ESP_OK.
static esp_err_t read_pointer(const char *channel, char *ver, size_t ver_cap,
                              char *url, size_t url_cap, char *sha, size_t sha_cap, bool *force) {
    tiny_config_t cfg;
    ESP_RETURN_ON_ERROR(tiny_config_load(&cfg), TAG, "cfg");

    cJSON *b = cJSON_CreateObject();
    cJSON_AddStringToObject(b, "deviceId", cfg.device_id);
    cJSON_AddStringToObject(b, "token", cfg.token);
    cJSON_AddStringToObject(b, "channel", channel);
    char *bs = cJSON_PrintUnformatted(b);
    cJSON_Delete(b);
    if (!bs) return ESP_ERR_NO_MEM;

    char full[160];
    snprintf(full, sizeof full, "%s/api/firmware/manifest", cfg.api);
    static char resp[1024];
    sink_t sink = { resp, 0, sizeof resp };
    resp[0] = 0;

    esp_http_client_config_t hc = {};
    hc.url = full;
    hc.method = HTTP_METHOD_PUT;
    hc.timeout_ms = 15000;
    hc.crt_bundle_attach = esp_crt_bundle_attach;
    hc.event_handler = evt;
    hc.user_data = &sink;
    esp_http_client_handle_t c = esp_http_client_init(&hc);
    if (!c) { free(bs); return ESP_FAIL; }
    esp_http_client_set_header(c, "Content-Type", "application/json");
    esp_http_client_set_post_field(c, bs, strlen(bs));
    esp_err_t err = esp_http_client_perform(c);
    int st = esp_http_client_get_status_code(c);
    esp_http_client_cleanup(c);
    free(bs);
    if (err != ESP_OK || st != 200) {
        ESP_LOGW(TAG, "pointer read: %s %d", esp_err_to_name(err), st);
        return err != ESP_OK ? err : ESP_FAIL;
    }

    cJSON *root = cJSON_Parse(resp);
    const cJSON *bundle = root ? cJSON_GetObjectItem(root, "bundle") : NULL;
    esp_err_t out = ESP_ERR_NOT_FOUND;  // null bundle = channel unset
    if (cJSON_IsObject(bundle)) {
        const cJSON *v = cJSON_GetObjectItem(bundle, "version");
        const cJSON *u = cJSON_GetObjectItem(bundle, "url");
        const cJSON *h = cJSON_GetObjectItem(bundle, "sha256");
        if (cJSON_IsString(v) && cJSON_IsString(u) && cJSON_IsString(h)) {
            strlcpy(ver, v->valuestring, ver_cap);
            strlcpy(url, u->valuestring, url_cap);
            strlcpy(sha, h->valuestring, sha_cap);
            // Optional downgrade override — publisher writes "force":"1"
            // (string, the platform's zod idiom) or true into the bundle.
            const cJSON *f = cJSON_GetObjectItem(bundle, "force");
            if (force) *force = (cJSON_IsString(f) && !strcmp(f->valuestring, "1")) ||
                                cJSON_IsTrue(f);
            out = ESP_OK;
        }
    }
    if (root) cJSON_Delete(root);
    return out;
}

// Parse "MAJOR.MINOR.PATCH[-mN]" into four numbers. Returns false when the
// string does not lead with digit.digit.digit — an unparseable version is
// INCOMPARABLE, and incomparable + no force = refuse (fail closed).
static bool ver_parse(const char *s, long n[4]) {
    n[0] = n[1] = n[2] = n[3] = 0;
    char *end;
    for (int i = 0; i < 3; ++i) {
        n[i] = strtol(s, &end, 10);
        if (end == s) return false;
        s = end;
        if (i < 2) { if (*s != '.') return false; ++s; }
    }
    const char *m = strstr(s, "-m");
    if (m) n[3] = strtol(m + 2, NULL, 10);
    return true;
}

// <0 pointer older, 0 equal, >0 pointer newer; INT_MIN = incomparable.
static int ver_cmp(const char *pointer, const char *current) {
    long a[4], b[4];
    if (!ver_parse(pointer, a) || !ver_parse(current, b)) return INT_MIN;
    for (int i = 0; i < 4; ++i)
        if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1;
    return 0;
}

extern "C" esp_err_t tiny_ota_check_and_stage(const char *channel) {
    char ver[48], url[256], sha_want[72];
    bool force = false;
    ESP_RETURN_ON_ERROR(
        read_pointer(channel && channel[0] ? channel : "sticky-dev",
                     ver, sizeof ver, url, sizeof url, sha_want, sizeof sha_want,
                     &force),
        TAG, "pointer");

    if (strcmp(ver, TINY_FW_VERSION) == 0) {
        ESP_LOGI(TAG, "already on %s", ver);
        return ESP_ERR_INVALID_VERSION;  // caller reads: up to date
    }
    // Direction check (check 16: the old trigger fired on strcmp DIFFERENCE,
    // so a stale/rolled-back pointer could silently downgrade the fleet —
    // integrity was verified, direction never was). Numeric compare; refuse
    // below or incomparable unless the bundle carries the explicit force flag.
    const int dir = ver_cmp(ver, TINY_FW_VERSION);
    if (dir != 1 && !force) {
        ESP_LOGE(TAG, "REFUSED: pointer %s is %s current %s and bundle has no "
                 "force flag — not staging", ver,
                 dir == INT_MIN ? "incomparable with" : "not newer than",
                 TINY_FW_VERSION);
        return ESP_ERR_NOT_SUPPORTED;  // caller reads: downgrade refused
    }
    if (dir != 1 && force)
        ESP_LOGW(TAG, "FORCED %s: pointer %s over current %s (bundle force)",
                 dir == INT_MIN ? "install" : "downgrade", ver, TINY_FW_VERSION);
    if (!host_allowed(url)) {
        ESP_LOGE(TAG, "artifact host not in allowlist: %s", url);
        return ESP_ERR_INVALID_ARG;
    }

    const esp_partition_t *slot = esp_ota_get_next_update_partition(NULL);
    if (!slot) return ESP_ERR_NOT_FOUND;
    ESP_LOGI(TAG, "staging %s -> %s (%s)", ver, slot->label, url);

    // Streamed download: open → read chunks → esp_ota_write + sha256.
    esp_http_client_config_t hc = {};
    hc.url = url;
    hc.timeout_ms = 30000;
    hc.crt_bundle_attach = esp_crt_bundle_attach;
    esp_http_client_handle_t c = esp_http_client_init(&hc);
    if (!c) return ESP_FAIL;
    esp_err_t err = esp_http_client_open(c, 0);
    if (err != ESP_OK) { esp_http_client_cleanup(c); return err; }
    int64_t total = esp_http_client_fetch_headers(c);
    int st = esp_http_client_get_status_code(c);
    if (st != 200 || total <= 0 || total > (int64_t)slot->size) {
        ESP_LOGE(TAG, "artifact fetch: status %d, len %lld (slot %lu)", st,
                 (long long)total, (unsigned long)slot->size);
        esp_http_client_cleanup(c);
        return ESP_FAIL;
    }

    esp_ota_handle_t ota = 0;
    err = esp_ota_begin(slot, total, &ota);
    if (err != ESP_OK) { esp_http_client_cleanup(c); return err; }

    mbedtls_sha256_context sha;
    mbedtls_sha256_init(&sha);
    mbedtls_sha256_starts(&sha, 0);

    static char chunk[4096];
    int64_t got = 0;
    while (got < total) {
        int n = esp_http_client_read(c, chunk, sizeof chunk);
        if (n <= 0) { err = ESP_FAIL; break; }
        err = esp_ota_write(ota, chunk, n);
        if (err != ESP_OK) break;
        mbedtls_sha256_update(&sha, (const unsigned char *)chunk, n);
        got += n;
    }
    esp_http_client_cleanup(c);

    unsigned char digest[32];
    mbedtls_sha256_finish(&sha, digest);
    mbedtls_sha256_free(&sha);

    if (err != ESP_OK || got != total) {
        esp_ota_abort(ota);
        ESP_LOGE(TAG, "download broke at %lld/%lld: %s", (long long)got,
                 (long long)total, esp_err_to_name(err));
        return err != ESP_OK ? err : ESP_FAIL;
    }

    char sha_got[65];
    for (int i = 0; i < 32; i++) sprintf(sha_got + 2 * i, "%02x", digest[i]);
    if (strcasecmp(sha_got, sha_want) != 0) {
        esp_ota_abort(ota);
        ESP_LOGE(TAG, "sha256 MISMATCH want %s got %s", sha_want, sha_got);
        return ESP_ERR_INVALID_CRC;
    }

    err = esp_ota_end(ota);
    if (err != ESP_OK) return err;
    err = esp_ota_set_boot_partition(slot);
    if (err != ESP_OK) return err;
    ESP_LOGI(TAG, "staged %s in %s, sha verified — reboot to apply", ver,
             slot->label);
    return ESP_OK;
}
