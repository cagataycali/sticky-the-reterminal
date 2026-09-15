// tiny_agent — active-universe-agent state (see tiny/tiny_agent.h).
//
// Storage shape (namespace "tinyagent"): "slug" string (absent/"" = owner's
// own tiny), "r0".."r7" roster strings, "rcount" u8. Same NVS idioms as
// tiny_askq: RAM mirror + mutex, lazy load, mirrored on every mutation so a
// pocket reboot keeps the selection ("reboot keeps the
// selection").
//
// Validation lives HERE, once: the backend clamps slugs at 64 chars and
// slugifies server-side, but a slug with spaces/quotes would also corrupt
// the JSON body and the roster keys — refuse locally with a real error
// instead of shipping garbage and rendering the backend's 404.

#include <string.h>
#include <ctype.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "nvs.h"
#include "tiny/tiny_agent.h"

static const char *TAG = "tiny_agent";
static const char *NS = "tinyagent";

static SemaphoreHandle_t s_mux;
static bool s_loaded = false;
static char s_slug[TINY_AGENT_SLUG_MAX + 1];
static char s_roster[TINY_AGENT_ROSTER_MAX][TINY_AGENT_SLUG_MAX + 1];
static int s_rcount = 0;

static void lock(void) {
    if (!s_mux) s_mux = xSemaphoreCreateMutex();
    xSemaphoreTake(s_mux, portMAX_DELAY);
}
static void unlock(void) { xSemaphoreGive(s_mux); }

// Trim + lowercase + validate into out (cap TINY_AGENT_SLUG_MAX+1).
// Empty result is VALID (means: clear back to owner default).
static esp_err_t normalize(const char *in, char *out) {
    out[0] = 0;
    if (!in) return ESP_OK;
    while (*in == ' ' || *in == '\t') ++in;
    size_t len = strlen(in);
    while (len && (in[len - 1] == ' ' || in[len - 1] == '\t' ||
                   in[len - 1] == '\r' || in[len - 1] == '\n'))
        --len;
    if (len > TINY_AGENT_SLUG_MAX) return ESP_ERR_INVALID_SIZE;
    for (size_t i = 0; i < len; ++i) {
        const char c = in[i];
        // The backend slugifies, but JSON-hostile bytes stop here.
        if (c == '"' || c == '\\' || c == ' ' || (unsigned char)c < 0x21)
            return ESP_ERR_INVALID_ARG;
        out[i] = (char)tolower((unsigned char)c);
    }
    out[len] = 0;
    return ESP_OK;
}

static void load_locked(void) {
    if (s_loaded) return;
    s_loaded = true;
    nvs_handle_t h;
    if (nvs_open(NS, NVS_READONLY, &h) != ESP_OK) {
        // First boot with this feature: seed the roster so the universe
        // card is never an empty page ("tiny" is the platform's own public
        // tiny — known-good, free, always resolvable).
        strlcpy(s_roster[0], "tiny", sizeof s_roster[0]);
        s_rcount = 1;
        return;
    }
    size_t len = sizeof s_slug;
    if (nvs_get_str(h, "slug", s_slug, &len) != ESP_OK) s_slug[0] = 0;
    uint8_t rc = 0;
    nvs_get_u8(h, "rcount", &rc);
    if (rc > TINY_AGENT_ROSTER_MAX) rc = TINY_AGENT_ROSTER_MAX;
    s_rcount = 0;
    for (int i = 0; i < rc; ++i) {
        char k[8];
        snprintf(k, sizeof k, "r%u", (unsigned)i & 7u);  // bounded: ROSTER_MAX=8
        len = sizeof s_roster[s_rcount];
        if (nvs_get_str(h, k, s_roster[s_rcount], &len) == ESP_OK &&
            s_roster[s_rcount][0])
            ++s_rcount;
    }
    nvs_close(h);
    if (s_rcount == 0) {
        strlcpy(s_roster[0], "tiny", sizeof s_roster[0]);
        s_rcount = 1;
    }
    if (s_slug[0])
        ESP_LOGI(TAG, "restored: talking to @%s (survives reboot by design)",
                 s_slug);
}

static esp_err_t save_locked(void) {
    nvs_handle_t h;
    esp_err_t r = nvs_open(NS, NVS_READWRITE, &h);
    if (r != ESP_OK) return r;
    r = nvs_set_str(h, "slug", s_slug);
    if (r == ESP_OK) r = nvs_set_u8(h, "rcount", (uint8_t)s_rcount);
    for (int i = 0; i < s_rcount && r == ESP_OK; ++i) {
        char k[8];
        snprintf(k, sizeof k, "r%u", (unsigned)i & 7u);  // bounded: ROSTER_MAX=8
        r = nvs_set_str(h, k, s_roster[i]);
    }
    if (r == ESP_OK) r = nvs_commit(h);
    nvs_close(h);
    return r;
}

extern "C" const char *tiny_agent_current(void) {
    lock();
    load_locked();
    unlock();
    return s_slug;  // stable static storage; readers get a C string
}

static int roster_find_locked(const char *slug) {
    for (int i = 0; i < s_rcount; ++i)
        if (strcmp(s_roster[i], slug) == 0) return i;
    return -1;
}

static void roster_add_locked(const char *slug) {
    if (!slug[0] || roster_find_locked(slug) >= 0) return;
    if (s_rcount >= TINY_AGENT_ROSTER_MAX) {
        // Full: drop the OLDEST non-"tiny" entry (index 0 is usually the
        // seed; keep it reachable — switching home must never need typing).
        int drop = strcmp(s_roster[0], "tiny") == 0 && s_rcount > 1 ? 1 : 0;
        memmove(&s_roster[drop], &s_roster[drop + 1],
                sizeof s_roster[0] * (s_rcount - drop - 1));
        --s_rcount;
    }
    strlcpy(s_roster[s_rcount], slug, sizeof s_roster[0]);
    ++s_rcount;
}

extern "C" esp_err_t tiny_agent_set(const char *slug) {
    char norm[TINY_AGENT_SLUG_MAX + 1];
    esp_err_t r = normalize(slug, norm);
    if (r != ESP_OK) return r;
    // "tiny <owner's own>" and "clear" both mean default; the backend also
    // treats the owner's own slug as default but "" keeps the ask body
    // byte-identical, which is the stronger guarantee.
    if (strcmp(norm, "clear") == 0) norm[0] = 0;
    lock();
    load_locked();
    strlcpy(s_slug, norm, sizeof s_slug);
    if (norm[0]) roster_add_locked(norm);
    r = save_locked();
    unlock();
    ESP_LOGI(TAG, "active agent -> %s", norm[0] ? norm : "(own tiny)");
    return r;
}

extern "C" int tiny_agent_roster(char out[][TINY_AGENT_SLUG_MAX + 1], int max) {
    lock();
    load_locked();
    const int n = s_rcount < max ? s_rcount : max;
    for (int i = 0; i < n; ++i)
        strlcpy(out[i], s_roster[i], TINY_AGENT_SLUG_MAX + 1);
    const int total = s_rcount;
    unlock();
    (void)total;
    return n;
}

extern "C" esp_err_t tiny_agent_roster_add(const char *slug) {
    char norm[TINY_AGENT_SLUG_MAX + 1];
    esp_err_t r = normalize(slug, norm);
    if (r != ESP_OK) return r;
    if (!norm[0]) return ESP_ERR_INVALID_ARG;
    lock();
    load_locked();
    roster_add_locked(norm);
    r = save_locked();
    unlock();
    return r;
}

extern "C" esp_err_t tiny_agent_roster_remove(const char *slug) {
    char norm[TINY_AGENT_SLUG_MAX + 1];
    esp_err_t r = normalize(slug, norm);
    if (r != ESP_OK) return r;
    if (!norm[0]) return ESP_ERR_INVALID_ARG;
    lock();
    load_locked();
    const int i = roster_find_locked(norm);
    if (i < 0) {
        unlock();
        return ESP_ERR_NOT_FOUND;
    }
    memmove(&s_roster[i], &s_roster[i + 1],
            sizeof s_roster[0] * (s_rcount - i - 1));
    --s_rcount;
    // Removing the ACTIVE agent clears back to default — a roster entry
    // that no longer exists must not keep answering the asks.
    if (strcmp(s_slug, norm) == 0) s_slug[0] = 0;
    r = save_locked();
    unlock();
    return r;
}
