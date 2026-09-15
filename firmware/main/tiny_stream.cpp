// tiny_stream — the frame player: walk a /api/frames manifest and blit.
// "Video" on e-ink is a frame cadence (partial refresh), the Nicla clip
// framing: it shows how the scene CHANGES, not smooth motion. The server did
// all the thinking (raster, dither, pack); this file is a metronome with a
// stop flag. Panel hygiene is DEVICE law and lives here: a full clean flash
// every kWipeEvery frames, whatever the manifest says — partial waveforms
// accumulate residue and the panel is ours to protect, not the server's.
#include "tiny/tiny_stream.h"

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>

#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "tiny/tiny_display.h"

static const char *TAG = "tiny_stream";

static const int    kWipeEvery      = 20;    // full flash cadence (residue law)
static const int    kMinIntervalMs  = 1500;  // partial refresh floor (panel physics)
static const int    kMaxFrames      = 120;   // mirrors the server cap
static const size_t kManifestCap    = 16384;

static std::atomic<bool> s_playing{false};
static std::atomic<bool> s_stop{false};
// The last thing the player has to say — readable over relay via `play
// status`, because check-3 proved silence-before-frame-0 reads as success.
static char s_verdict[160] = "never played this boot";
static void verdict(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    vsnprintf(s_verdict, sizeof s_verdict, fmt, ap);
    va_end(ap);
    ESP_LOGI(TAG, "verdict: %s", s_verdict);
}

// ---- manifest fetch (small JSON, not a frame) ----------------------------
static char *fetch_text(const char *url, size_t cap) {
    if (strncmp(url, "https://", 8) != 0) return nullptr;
    esp_http_client_config_t hc = {};
    hc.url = url;
    hc.timeout_ms = 10000;
    hc.crt_bundle_attach = esp_crt_bundle_attach;
    esp_http_client_handle_t c = esp_http_client_init(&hc);
    if (!c) return nullptr;
    esp_err_t oerr = esp_http_client_open(c, 0);
    if (oerr != ESP_OK) {
        verdict("manifest OPEN failed: %s (tls/dns layer)", esp_err_to_name(oerr));
        esp_http_client_cleanup(c);
        return nullptr;
    }
    int64_t clen = esp_http_client_fetch_headers(c);
    int status = esp_http_client_get_status_code(c);
    if (status != 200 || clen <= 0 || (size_t)clen > cap) {
        verdict("manifest refused: status=%d clen=%lld (need 200 + CL<=%u)",
                status, (long long)clen, (unsigned)cap);
        esp_http_client_cleanup(c);
        return nullptr;
    }
    char *buf = (char *)heap_caps_malloc((size_t)clen + 1, MALLOC_CAP_SPIRAM);
    if (!buf) { esp_http_client_cleanup(c); return nullptr; }
    size_t got = 0;
    while (got < (size_t)clen) {
        int r = esp_http_client_read(c, buf + got, (size_t)clen - got);
        if (r <= 0) break;
        got += (size_t)r;
    }
    esp_http_client_cleanup(c);
    if (got != (size_t)clen) { heap_caps_free(buf); return nullptr; }
    buf[got] = 0;
    return buf;
}

// ---- play task ------------------------------------------------------------
// Iter-32 conviction (hard): esp_http_client CONNECT fails from a freshly
// spawned task, always, both hosts, retries useless — and succeeds from the
// long-lived node task, always. So ALL HTTP now happens in the CALLER's
// context (tiny_stream_play runs in the node task, where connect is proven
// thousands of times a day), and this task is a pure metronome over frames
// already sitting in PSRAM. Prefetch-then-play also makes cadence immune to
// network jitter — the design the panel wanted anyway.
static const int kPrefetchMax = 24;  // 24 × 96KB worst case = 2.3MB of 8MB PSRAM

struct play_args {
    int      interval_ms;
    int      count;
    uint8_t *bufs[kPrefetchMax];
    size_t   lens[kPrefetchMax];
};

static void free_frames(play_args *a, int from) {
    for (int i = from; i < a->count; ++i)
        if (a->bufs[i]) { heap_caps_free(a->bufs[i]); a->bufs[i] = nullptr; }
}

static void play_task(void *arg) {
    play_args *a = (play_args *)arg;
    int shown = 0;
    for (int i = 0; i < a->count && !s_stop.load(); ++i) {
        TickType_t t0 = xTaskGetTickCount();
        // Hygiene: frame 0 and every kWipeEvery-th = full flash; rest partial.
        bool partial = (shown % kWipeEvery) != 0;
        esp_err_t err = tiny_display_blit_1bit(a->bufs[i], a->lens[i], partial);
        heap_caps_free(a->bufs[i]); a->bufs[i] = nullptr;
        if (err != ESP_OK) {
            verdict("blit %d FAILED: %s (%d shown)", i, esp_err_to_name(err), shown);
            break;
        }
        ++shown;
        TickType_t spent = xTaskGetTickCount() - t0;
        TickType_t want = pdMS_TO_TICKS(a->interval_ms);
        if (!s_stop.load() && i + 1 < a->count && spent < want)
            vTaskDelay(want - spent);
    }
    if (shown == a->count && a->count > 0)
        verdict("stream done: %d/%d frames", shown, a->count);
    else if (s_stop.load())
        verdict("stream stopped at %d/%d frames", shown, a->count);
    free_frames(a, 0);
    free(a);
    s_playing.store(false);
    vTaskDelete(nullptr);
}

extern "C" esp_err_t tiny_stream_image(const char *url, bool gray4) {
    (void)gray4;  // length picks the format — the flag is legacy header shape
    char card[420];
    snprintf(card, sizeof card,
             "{\"type\":\"image\",\"card_id\":\"stream\",\"url\":\"%s\"}", url);
    return tiny_display_render_card(card);
}

extern "C" esp_err_t tiny_stream_play(const char *manifest_url, int interval_s,
                                      int max_frames) {
    // Runs in the CALLER's task (node task) on purpose — see play_task header.
    // Everything network happens here; the spawned task only ever touches RAM.
    if (!manifest_url || strncmp(manifest_url, "https://", 8) != 0)
        return ESP_ERR_INVALID_ARG;
    if (s_playing.load()) return ESP_ERR_INVALID_STATE;  // one stream at a time

    char *mjson = fetch_text(manifest_url, kManifestCap);
    if (!mjson) return ESP_FAIL;  // verdict already names the layer
    cJSON *root = cJSON_Parse(mjson);
    const cJSON *frames = root ? cJSON_GetObjectItem(root, "frames") : nullptr;
    int total = cJSON_IsArray(frames) ? cJSON_GetArraySize(frames) : 0;
    if (max_frames > 0 && total > max_frames) total = max_frames;
    if (total > kPrefetchMax) total = kPrefetchMax;
    if (!root) { verdict("manifest json parse FAILED pre-frame-0"); }
    else if (total <= 0) { verdict("manifest has no frames pre-frame-0"); }
    if (total <= 0) {
        if (root) cJSON_Delete(root);
        heap_caps_free(mjson);
        return ESP_FAIL;
    }
    const cJSON *jb = cJSON_GetObjectItem(root, "frame_budget_ms");
    int interval = interval_s > 0 ? interval_s * 1000 : kMinIntervalMs;
    if (cJSON_IsNumber(jb) && (int)jb->valuedouble > interval)
        interval = (int)jb->valuedouble;  // server asked for slower: honor it
    if (interval < kMinIntervalMs) interval = kMinIntervalMs;

    // Frame URLs may be origin-relative; the origin is the manifest's.
    char origin[128] = {0};
    const char *path = strchr(manifest_url + 8, '/');
    if (path && (size_t)(path - manifest_url) < sizeof origin)
        memcpy(origin, manifest_url, (size_t)(path - manifest_url));

    play_args *a = (play_args *)calloc(1, sizeof(play_args));
    if (!a) { cJSON_Delete(root); heap_caps_free(mjson); return ESP_ERR_NO_MEM; }
    a->interval_ms = interval;
    for (int i = 0; i < total; ++i) {
        const cJSON *jf = cJSON_GetArrayItem(frames, i);
        if (!cJSON_IsString(jf)) continue;
        char url[384];
        if (jf->valuestring[0] == '/')
            snprintf(url, sizeof url, "%s%s", origin, jf->valuestring);
        else
            snprintf(url, sizeof url, "%s", jf->valuestring);
        uint8_t *buf = nullptr; size_t len = 0;
        if (tiny_display_fetch_raw(url, &buf, &len) != ESP_OK) {
            verdict("prefetch frame %d FAILED (%d cached) — stream refused whole",
                    i, a->count);
            free_frames(a, 0); free(a);
            cJSON_Delete(root); heap_caps_free(mjson);
            return ESP_FAIL;  // all-or-nothing beats a stutter shown as art
        }
        a->bufs[a->count] = buf; a->lens[a->count] = len; ++a->count;
    }
    cJSON_Delete(root);
    heap_caps_free(mjson);
    if (a->count <= 0) { free(a); return ESP_FAIL; }

    s_stop.store(false);
    s_playing.store(true);
    verdict("prefetched %d frames (%d ms cadence) — playing", a->count, interval);
    // 12K not 4K: the task owns no sockets, but tiny_display_refresh_* runs
    // IN this task and the panel driver's stack appetite is not ours to
    // assume — iter-34's reboot ~30s into playback (play acked, prefetch
    // proven by the ack, then cold boot) has 4K-overflow as prime suspect.
    // 12K is cheap; a crashed premiere is not.
    if (xTaskCreate(play_task, "stream_play", 12288, a, 4, nullptr) != pdPASS) {
        free_frames(a, 0); free(a);
        s_playing.store(false);
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

extern "C" void tiny_stream_stop(void) { s_stop.store(true); }

extern "C" const char *tiny_stream_verdict(void) { return s_verdict; }

extern "C" bool tiny_stream_playing(void) { return s_playing.load(); }
