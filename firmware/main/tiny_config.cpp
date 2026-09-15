// tiny_config — NVS-backed identity + wifi. Port of strands-nicla
// firmware/tiny_config.py: the whole config is ONE JSON blob (key "cfg" in
// namespace "tiny"), because that mirrors the Nicla's tiny.json file and
// keeps merge semantics identical to the Python reference.
#include "tiny/tiny_config.h"

#include <string.h>

#include "cJSON.h"
#include "esp_check.h"
#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "tiny_config";
static const char *NS = "tiny";
static const char *KEY = "cfg";
static const char *DEFAULT_API = "https://tiny.technology";

static esp_err_t nvs_ready(void) {
    static bool inited = false;
    if (inited) return ESP_OK;
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_RETURN_ON_ERROR(nvs_flash_erase(), TAG, "nvs erase");
        err = nvs_flash_init();
    }
    if (err == ESP_OK) inited = true;
    return err;
}

static void copy_str(char *dst, size_t cap, const cJSON *v) {
    if (cJSON_IsString(v) && v->valuestring) {
        strlcpy(dst, v->valuestring, cap);
    }
}

static void parse_into(tiny_config_t *c, const cJSON *root) {
    copy_str(c->device_id, sizeof c->device_id, cJSON_GetObjectItem(root, "device_id"));
    copy_str(c->token, sizeof c->token, cJSON_GetObjectItem(root, "token"));
    copy_str(c->api, sizeof c->api, cJSON_GetObjectItem(root, "api"));
    copy_str(c->name, sizeof c->name, cJSON_GetObjectItem(root, "name"));
    const cJSON *nets = cJSON_GetObjectItem(root, "networks");
    if (cJSON_IsArray(nets)) {
        c->network_count = 0;
        const cJSON *n = NULL;
        cJSON_ArrayForEach(n, nets) {
            if (c->network_count >= TINY_MAX_NETWORKS) break;
            const cJSON *ssid = cJSON_GetObjectItem(n, "ssid");
            if (!cJSON_IsString(ssid) || !ssid->valuestring[0]) continue;
            tiny_net_t *slot = &c->networks[c->network_count++];
            copy_str(slot->ssid, sizeof slot->ssid, ssid);
            // "key" is ours (Nicla schema); "password" is what the dashboard's
            // settings API sends — same field, both accepted.
            const cJSON *k = cJSON_GetObjectItem(n, "key");
            if (!cJSON_IsString(k)) k = cJSON_GetObjectItem(n, "password");
            copy_str(slot->key, sizeof slot->key, k);
        }
    }
}

static esp_err_t read_blob(char **out) {  // caller frees; NULL if absent
    *out = NULL;
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READONLY, &h);
    if (err == ESP_ERR_NVS_NOT_FOUND) return ESP_OK;
    ESP_RETURN_ON_ERROR(err, TAG, "open");
    size_t len = 0;
    err = nvs_get_str(h, KEY, NULL, &len);
    if (err == ESP_OK && len > 0) {
        *out = (char *)malloc(len);
        if (*out) err = nvs_get_str(h, KEY, *out, &len);
    } else if (err == ESP_ERR_NVS_NOT_FOUND) {
        err = ESP_OK;
    }
    nvs_close(h);
    return err;
}

static esp_err_t write_blob(const char *json) {
    nvs_handle_t h;
    ESP_RETURN_ON_ERROR(nvs_open(NS, NVS_READWRITE, &h), TAG, "open rw");
    esp_err_t err = nvs_set_str(h, KEY, json);
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    return err;
}

esp_err_t tiny_config_load(tiny_config_t *out) {
    memset(out, 0, sizeof *out);
    strlcpy(out->api, DEFAULT_API, sizeof out->api);
    ESP_RETURN_ON_ERROR(nvs_ready(), TAG, "nvs");
    char *blob = NULL;
    ESP_RETURN_ON_ERROR(read_blob(&blob), TAG, "read");
    if (!blob) return ESP_OK;  // fresh device: defaults
    cJSON *root = cJSON_Parse(blob);
    free(blob);
    if (!root) return ESP_ERR_INVALID_STATE;
    parse_into(out, root);
    cJSON_Delete(root);
    if (!out->api[0]) strlcpy(out->api, DEFAULT_API, sizeof out->api);
    return ESP_OK;
}

// merge semantics (ported from tiny_config.py merge()): networks upsert by
// ssid (update key in place, else append), scalars overwrite when present.
esp_err_t tiny_config_merge_json(const char *json) {
    ESP_RETURN_ON_ERROR(nvs_ready(), TAG, "nvs");
    cJSON *incoming = cJSON_Parse(json);
    if (!incoming) return ESP_ERR_INVALID_ARG;

    tiny_config_t cur;
    esp_err_t err = tiny_config_load(&cur);
    if (err != ESP_OK) { cJSON_Delete(incoming); return err; }

    tiny_config_t merged = cur;
    // scalars overwrite
    parse_into(&merged, incoming);
    // networks: parse_into REPLACED the list if incoming had one; redo as upsert
    merged.network_count = cur.network_count;
    memcpy(merged.networks, cur.networks, sizeof merged.networks);
    const cJSON *nets = cJSON_GetObjectItem(incoming, "networks");
    if (cJSON_IsArray(nets)) {
        const cJSON *n = NULL;
        cJSON_ArrayForEach(n, nets) {
            const cJSON *ssid = cJSON_GetObjectItem(n, "ssid");
            if (!cJSON_IsString(ssid) || !ssid->valuestring[0]) continue;
            int idx = -1;
            for (int i = 0; i < merged.network_count; ++i)
                if (strcmp(merged.networks[i].ssid, ssid->valuestring) == 0) { idx = i; break; }
            // {"ssid":X,"forget":true} removes X (dashboard wifi_forget shape);
            // forgetting an unknown ssid is a no-op, not an error.
            if (cJSON_IsTrue(cJSON_GetObjectItem(n, "forget"))) {
                if (idx >= 0) {
                    for (int i = idx; i < merged.network_count - 1; ++i)
                        merged.networks[i] = merged.networks[i + 1];
                    memset(&merged.networks[--merged.network_count], 0,
                           sizeof merged.networks[0]);
                }
                continue;
            }
            if (idx < 0) {
                if (merged.network_count >= TINY_MAX_NETWORKS) continue;
                idx = merged.network_count++;
                strlcpy(merged.networks[idx].ssid, ssid->valuestring,
                        sizeof merged.networks[idx].ssid);
            }
            const cJSON *k = cJSON_GetObjectItem(n, "key");
            if (!cJSON_IsString(k)) k = cJSON_GetObjectItem(n, "password");
            copy_str(merged.networks[idx].key, sizeof merged.networks[idx].key,
                     k);
        }
    }
    cJSON_Delete(incoming);

    // serialize + persist
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "device_id", merged.device_id);
    cJSON_AddStringToObject(root, "token", merged.token);
    cJSON_AddStringToObject(root, "api", merged.api);
    cJSON_AddStringToObject(root, "name", merged.name);
    cJSON *arr = cJSON_AddArrayToObject(root, "networks");
    for (int i = 0; i < merged.network_count; ++i) {
        cJSON *n = cJSON_CreateObject();
        cJSON_AddStringToObject(n, "ssid", merged.networks[i].ssid);
        cJSON_AddStringToObject(n, "key", merged.networks[i].key);
        cJSON_AddItemToArray(arr, n);
    }
    char *out = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!out) return ESP_ERR_NO_MEM;
    err = write_blob(out);
    ESP_LOGI(TAG, "config merged: %d network(s), id=%s api=%s",
             merged.network_count,
             merged.device_id[0] ? merged.device_id : "(unset)", merged.api);
    free(out);
    return err;
}

bool tiny_config_is_provisioned(const tiny_config_t *c) {
    return c->device_id[0] && c->token[0] && c->network_count > 0;
}

// Serialize a whole config to the blob. Third caller arrived (wifi_promote),
// so the copy-pasted block became a function — merge() keeps its inline copy
// only because it writes `merged` with logging of its own.
static esp_err_t persist_config(const tiny_config_t *c) {
    cJSON *root = cJSON_CreateObject();
    if (!root) return ESP_ERR_NO_MEM;
    cJSON_AddStringToObject(root, "device_id", c->device_id);
    cJSON_AddStringToObject(root, "token", c->token);
    cJSON_AddStringToObject(root, "api", c->api);
    cJSON_AddStringToObject(root, "name", c->name);
    cJSON *arr = cJSON_AddArrayToObject(root, "networks");
    for (int i = 0; i < c->network_count; ++i) {
        cJSON *n = cJSON_CreateObject();
        cJSON_AddStringToObject(n, "ssid", c->networks[i].ssid);
        cJSON_AddStringToObject(n, "key", c->networks[i].key);
        cJSON_AddItemToArray(arr, n);
    }
    char *out = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!out) return ESP_ERR_NO_MEM;
    esp_err_t err = write_blob(out);
    free(out);
    return err;
}

static esp_err_t rewrite_without(bool wipe_wifi, bool wipe_identity) {
    tiny_config_t c;
    ESP_RETURN_ON_ERROR(tiny_config_load(&c), TAG, "load");
    if (wipe_wifi) c.network_count = 0;
    if (wipe_identity) { c.device_id[0] = 0; c.token[0] = 0; }
    return persist_config(&c);
}

// move-up: swap `ssid` with the entry above it — one honest step
// per tap, repaint shows the new walk order. Already-first is ESP_OK (a
// no-op the UI should not offer, but never an error if it does).
esp_err_t tiny_config_wifi_promote(const char *ssid) {
    if (!ssid || !ssid[0]) return ESP_ERR_INVALID_ARG;
    tiny_config_t c;
    ESP_RETURN_ON_ERROR(tiny_config_load(&c), TAG, "load");
    int idx = -1;
    for (int i = 0; i < c.network_count; ++i)
        if (strcmp(c.networks[i].ssid, ssid) == 0) { idx = i; break; }
    if (idx < 0) return ESP_ERR_NOT_FOUND;
    if (idx == 0) return ESP_OK;
    tiny_net_t tmp = c.networks[idx - 1];
    c.networks[idx - 1] = c.networks[idx];
    c.networks[idx] = tmp;
    ESP_LOGI(TAG, "wifi promote: \"%s\" %d -> %d (of %d)", ssid, idx, idx - 1,
             c.network_count);
    return persist_config(&c);
}

esp_err_t tiny_config_forget_wifi(void) { return rewrite_without(true, false); }
esp_err_t tiny_config_forget_identity(void) { return rewrite_without(false, true); }

// ---- silent mode (UI_SPEC §7) -------------------------------------
// One u8 next to the cfg blob. Absent key = audible: a fresh device keeps its
// feedback vocabulary until the owner says otherwise.
bool tiny_config_silent_get(void) {
    if (nvs_ready() != ESP_OK) return false;
    nvs_handle_t h;
    if (nvs_open(NS, NVS_READONLY, &h) != ESP_OK) return false;
    uint8_t v = 0;
    if (nvs_get_u8(h, "silent", &v) != ESP_OK) v = 0;
    nvs_close(h);
    return v != 0;
}

esp_err_t tiny_config_silent_set(bool silent) {
    ESP_RETURN_ON_ERROR(nvs_ready(), TAG, "nvs");
    nvs_handle_t h;
    ESP_RETURN_ON_ERROR(nvs_open(NS, NVS_READWRITE, &h), TAG, "open rw");
    esp_err_t err = nvs_set_u8(h, "silent", silent ? 1 : 0);
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    return err;
}

// ---- rotation follow-gravity (display settings) ----------------------------
// Absent key = auto: a fresh device turns with the hand, per tiny_orient's
// charter. Locked survives reboot for the same reason silent does — a wall
// mount that un-locks itself every power cycle is a setting that lies.
bool tiny_config_rotauto_get(void) {
    if (nvs_ready() != ESP_OK) return true;
    nvs_handle_t h;
    if (nvs_open(NS, NVS_READONLY, &h) != ESP_OK) return true;
    uint8_t v = 1;
    if (nvs_get_u8(h, "rotauto", &v) != ESP_OK) v = 1;
    nvs_close(h);
    return v != 0;
}

esp_err_t tiny_config_rotauto_set(bool on) {
    ESP_RETURN_ON_ERROR(nvs_ready(), TAG, "nvs");
    nvs_handle_t h;
    ESP_RETURN_ON_ERROR(nvs_open(NS, NVS_READWRITE, &h), TAG, "open rw");
    esp_err_t err = nvs_set_u8(h, "rotauto", on ? 1 : 0);
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    return err;
}

// ---- autosleep idle budget, minutes (display settings) ---------------------
// Absent = 30 (the pocket-use default). Stored raw;
// the CONSUMER validates against the offered set — NVS holding a value a
// future firmware stopped offering must degrade to default, not brick sleep.
uint8_t tiny_config_sleepidle_get(void) {
    if (nvs_ready() != ESP_OK) return 30;
    nvs_handle_t h;
    if (nvs_open(NS, NVS_READONLY, &h) != ESP_OK) return 30;
    uint8_t v = 30;
    if (nvs_get_u8(h, "sleepidle", &v) != ESP_OK) v = 30;
    nvs_close(h);
    return (v == 5 || v == 15 || v == 30 || v == 60) ? v : 30;
}

esp_err_t tiny_config_sleepidle_set(uint8_t minutes) {
    if (minutes != 5 && minutes != 15 && minutes != 30 && minutes != 60)
        return ESP_ERR_INVALID_ARG;
    ESP_RETURN_ON_ERROR(nvs_ready(), TAG, "nvs");
    nvs_handle_t h;
    ESP_RETURN_ON_ERROR(nvs_open(NS, NVS_READWRITE, &h), TAG, "open rw");
    esp_err_t err = nvs_set_u8(h, "sleepidle", minutes);
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    return err;
}

// ---- beep volume tier (sounds settings) ------------------------------------
// Absent = normal: the feedback vocabulary ships at full voice.
bool tiny_config_beepsoft_get(void) {
    if (nvs_ready() != ESP_OK) return false;
    nvs_handle_t h;
    if (nvs_open(NS, NVS_READONLY, &h) != ESP_OK) return false;
    uint8_t v = 0;
    if (nvs_get_u8(h, "beepsoft", &v) != ESP_OK) v = 0;
    nvs_close(h);
    return v != 0;
}

esp_err_t tiny_config_beepsoft_set(bool soft) {
    ESP_RETURN_ON_ERROR(nvs_ready(), TAG, "nvs");
    nvs_handle_t h;
    ESP_RETURN_ON_ERROR(nvs_open(NS, NVS_READWRITE, &h), TAG, "open rw");
    esp_err_t err = nvs_set_u8(h, "beepsoft", soft ? 1 : 0);
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    return err;
}

// ---- gallery manifest ("the last-sent photo set survives") -----------------
// The photos themselves live on the media host; what survives a reboot is
// the MANIFEST — url slots + count + last-viewed index. Blob layout is the
// display layer's own array (count * slot bytes), stored verbatim so a load
// is a single memcpy back into it. ~2.5KB worst case: NVS-friendly.
esp_err_t tiny_config_gallery_save(const void *urls, size_t len,
                                   uint8_t count, uint8_t index) {
    ESP_RETURN_ON_ERROR(nvs_ready(), TAG, "nvs");
    nvs_handle_t h;
    ESP_RETURN_ON_ERROR(nvs_open(NS, NVS_READWRITE, &h), TAG, "open rw");
    esp_err_t err = nvs_set_blob(h, "gal_urls", urls, len);
    if (err == ESP_OK) err = nvs_set_u8(h, "gal_n", count);
    if (err == ESP_OK) err = nvs_set_u8(h, "gal_i", index);
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    return err;
}

// Index alone (page flip): u8 write, no blob churn — flash wear stays
// proportional to what actually changed.
esp_err_t tiny_config_gallery_save_index(uint8_t index) {
    ESP_RETURN_ON_ERROR(nvs_ready(), TAG, "nvs");
    nvs_handle_t h;
    ESP_RETURN_ON_ERROR(nvs_open(NS, NVS_READWRITE, &h), TAG, "open rw");
    esp_err_t err = nvs_set_u8(h, "gal_i", index);
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    return err;
}

esp_err_t tiny_config_gallery_load(void *urls, size_t cap,
                                   uint8_t *count, uint8_t *index) {
    ESP_RETURN_ON_ERROR(nvs_ready(), TAG, "nvs");
    nvs_handle_t h;
    ESP_RETURN_ON_ERROR(nvs_open(NS, NVS_READONLY, &h), TAG, "open ro");
    size_t len = cap;
    esp_err_t err = nvs_get_blob(h, "gal_urls", urls, &len);
    uint8_t n = 0, i = 0;
    if (err == ESP_OK) err = nvs_get_u8(h, "gal_n", &n);
    if (err == ESP_OK && nvs_get_u8(h, "gal_i", &i) != ESP_OK) i = 0;
    nvs_close(h);
    if (err != ESP_OK) return err;
    // A manifest whose count disagrees with the blob's geometry is corrupt
    // (interrupted write, or a future layout change) — refuse it whole
    // rather than let slot n read another slot's bytes. The blob must split
    // into exactly n equal slots.
    if (n == 0 || len % n != 0) return ESP_ERR_INVALID_SIZE;
    if (count) *count = n;
    if (index) *index = (i < n) ? i : 0;
    return ESP_OK;
}
