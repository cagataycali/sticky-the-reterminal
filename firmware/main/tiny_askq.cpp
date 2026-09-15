// tiny_askq — §16 law 2: nothing typed is lost. An NVS-backed FIFO of TEXT
// asks that could not reach the backend (transport failure after do_ask's
// single retry). Depth 8; the glance cluster wears qN while any wait; the
// node task drains ONE per successful heartbeat — in-order, and bounded so
// a drain can never occupy the loop for minutes (each ask is a full agent
// turn on the glass).
//
// TEXT ONLY, by design: a voice ask is a ≥48KB WAV whose upload is the very
// thing that failed — NVS is a config store, not a media spool. Voice
// offline fails honestly at the upload step with its own copy (do_ask).
//
// Storage shape (namespace "tinyaskq"): q0..q7 strings, head u8, count u8.
// Ring arithmetic in RAM, mirrored to NVS on every mutation — a reboot
// mid-queue loses nothing (law 2's whole point: the pocket reboots).

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "nvs.h"
#include "tiny/tiny_askq.h"

static const char *TAG = "tiny_askq";
static constexpr int kDepth = 8;
static SemaphoreHandle_t s_mux;
static bool s_loaded = false;
static uint8_t s_head = 0, s_count = 0;

static void lock(void) {
    if (!s_mux) s_mux = xSemaphoreCreateMutex();
    xSemaphoreTake(s_mux, portMAX_DELAY);
}
static void unlock(void) { xSemaphoreGive(s_mux); }

static void key_for(int slot, char *k, size_t cap) {
    snprintf(k, cap, "q%d", slot);
}

// Lazy load of head/count only — slot strings stay in NVS until peeked.
static void load_locked(void) {
    if (s_loaded) return;
    s_loaded = true;
    nvs_handle_t h;
    if (nvs_open("tinyaskq", NVS_READONLY, &h) != ESP_OK) return;  // empty
    nvs_get_u8(h, "head", &s_head);
    nvs_get_u8(h, "count", &s_count);
    nvs_close(h);
    if (s_head >= kDepth) s_head = 0;
    if (s_count > kDepth) s_count = kDepth;
    if (s_count)
        ESP_LOGI(TAG, "restored: %d queued ask(s) survive the reboot", s_count);
}

static esp_err_t save_meta(nvs_handle_t h) {
    esp_err_t r = nvs_set_u8(h, "head", s_head);
    if (r == ESP_OK) r = nvs_set_u8(h, "count", s_count);
    if (r == ESP_OK) r = nvs_commit(h);
    return r;
}

extern "C" int tiny_askq_count(void) {
    lock();
    load_locked();
    int n = s_count;
    unlock();
    return n;
}

extern "C" esp_err_t tiny_askq_push(const char *text) {
    if (!text || !text[0]) return ESP_ERR_INVALID_ARG;
    if (strlen(text) >= TINY_ASKQ_MAX_LEN) return ESP_ERR_INVALID_SIZE;
    lock();
    load_locked();
    if (s_count >= kDepth) { unlock(); return ESP_ERR_NO_MEM; }
    nvs_handle_t h;
    esp_err_t r = nvs_open("tinyaskq", NVS_READWRITE, &h);
    if (r != ESP_OK) { unlock(); return r; }
    char k[8];
    key_for((s_head + s_count) % kDepth, k, sizeof k);
    r = nvs_set_str(h, k, text);
    if (r == ESP_OK) { s_count++; r = save_meta(h); if (r != ESP_OK) s_count--; }
    nvs_close(h);
    unlock();
    if (r == ESP_OK)
        ESP_LOGI(TAG, "queued (%d/%d): %.60s", tiny_askq_count(), kDepth, text);
    return r;
}

extern "C" esp_err_t tiny_askq_peek(char *out, size_t cap) {
    if (!out || cap == 0) return ESP_ERR_INVALID_ARG;
    lock();
    load_locked();
    if (s_count == 0) { unlock(); return ESP_ERR_NOT_FOUND; }
    nvs_handle_t h;
    esp_err_t r = nvs_open("tinyaskq", NVS_READONLY, &h);
    if (r == ESP_OK) {
        char k[8];
        key_for(s_head, k, sizeof k);
        size_t len = cap;
        r = nvs_get_str(h, k, out, &len);
        nvs_close(h);
    }
    unlock();
    return r;
}

extern "C" esp_err_t tiny_askq_pop(void) {
    lock();
    load_locked();
    if (s_count == 0) { unlock(); return ESP_ERR_NOT_FOUND; }
    nvs_handle_t h;
    esp_err_t r = nvs_open("tinyaskq", NVS_READWRITE, &h);
    if (r == ESP_OK) {
        char k[8];
        key_for(s_head, k, sizeof k);
        nvs_erase_key(h, k);  // best-effort; meta is the truth
        s_head = (uint8_t)((s_head + 1) % kDepth);
        s_count--;
        r = save_meta(h);
        nvs_close(h);
    }
    unlock();
    return r;
}
