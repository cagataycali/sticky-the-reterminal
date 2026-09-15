// tiny_node — the device loop: heartbeat 30s, relay poll 5s, dispatch, reply.
// Wire shapes per docs/API_CONTRACT.md (read from ~/tinyai-id source).
// EVERY request: 15s timeout. 401 on heartbeat/poll -> STRIKE, not wipe;
// reboot (fresh boot lands in unprovisioned path -> portal once M3b lands).
#include "tiny/tiny_node.h"
#include "tiny/tiny_askq.h"
#include "tiny/tiny_agent.h"
#include "tiny/tiny_lock.h"

#include "tiny/tiny_provision.h"

#include <errno.h>   // transport-failure diagnostics on the ask retry path
#include <dirent.h>  // sd ls verb
#include <unistd.h>  // unlink — sd probe verb
#include <sys/stat.h>
#include <string.h>
#include <time.h>

#include "cJSON.h"
#include "esp_check.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "esp_attr.h"  // EXT_RAM_BSS_ATTR: big buffers off the internal rail
#include "esp_system.h"
#include "esp_sleep.h"
#include "sticky_power.h"
#include "sticky_sdcard.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "tiny/tiny_config.h"
#include "tiny/tiny_version.h"
#if __has_include("tiny_commit.h")
#include "tiny_commit.h"  // build-generated: TINY_FW_COMMIT = git describe
#else
#define TINY_FW_COMMIT "nogit"
#endif
#include "tiny/tiny_display.h"
#include "sticky_buzzer.h"
#include "tiny/tiny_stream.h"
#include "tiny/tiny_orient.h"
#include "tiny/tiny_touch.h"
#include "tiny/tiny_wifi.h"
#include "tiny/tiny_audio.h"
#include "tiny/tiny_upload.h"
#include "tiny/tiny_ota.h"
#include "esp_ota_ops.h"
#include "esp_heap_caps.h"
#include "sticky_battery.h"
#include "tiny/tiny_screenshot.h"
#include "tiny/tiny_sensors.h"
#include "tiny/tiny_shell.h"
#include "tiny/tiny_onboard.h"
#include "esp_timer.h"

extern "C" int tiny_bootmark_prev(void);
extern "C" void tiny_bootmark_stage(uint8_t stage);
static const char *TAG = "tiny_node";
static TaskHandle_t s_task = NULL;
static tiny_config_t s_cfg;
static volatile bool s_stop = false;

// ---------- event queue (producer: touch/button tasks; consumer: node loop) ----
// POST /api/devices/event allowlists kind (worker: nicla_wake, nicla_sentry,
// nicla_transcript, device_note) — ui_tap ships as device_note until the
// allowlist grows. detail is capped server-side at 300 chars.
typedef struct { char kind[24]; char detail[160]; } tiny_event_t;
#define EVENT_QUEUE_LEN 8
static QueueHandle_t s_events = NULL;

#define HTTP_TIMEOUT_MS 15000
#define REPLY_MAX 8000

// ---------- HTTP helper: POST/PUT/PATCH json, collect body ----------
// `dropped` counts the bytes this sink refused. It exists because the refusal
// used to be silent: an over-cap response was quietly cut, the caller handed the
// fragment to cJSON_Parse, got NULL, and returned — so a poll carrying five
// envelopes could lose all five with nothing in the log and no ack to the
// sender. The operator's experience was "my command vanished", which is the
// worst possible failure report because it names nothing.
typedef struct { char *buf; int len, cap, dropped; } sink_t;

static esp_err_t http_evt(esp_http_client_event_t *e) {
    if (e->event_id == HTTP_EVENT_ON_DATA) {
        sink_t *s = (sink_t *)e->user_data;
        if (!s || !s->buf) return ESP_OK;
        if (s->len + e->data_len < s->cap) {
            memcpy(s->buf + s->len, e->data, e->data_len);
            s->len += e->data_len;
            s->buf[s->len] = 0;
        } else {
            s->dropped += e->data_len;
        }
    }
    return ESP_OK;
}

static int http_json_t(esp_http_client_method_t method, const char *path,
                       const char *body, char *resp, int resp_cap,
                       int timeout_ms) {
    char url[160];
    snprintf(url, sizeof url, "%s%s", s_cfg.api, path);
    sink_t sink = { resp, 0, resp_cap, 0 };
    if (resp && resp_cap > 0) resp[0] = 0;
    esp_http_client_config_t hc = {};
    hc.url = url;
    hc.method = method;
    hc.timeout_ms = timeout_ms;
    hc.crt_bundle_attach = esp_crt_bundle_attach;
    hc.event_handler = http_evt;
    hc.user_data = &sink;
    esp_http_client_handle_t c = esp_http_client_init(&hc);
    if (!c) return -1;
    esp_http_client_set_header(c, "Content-Type", "application/json");
    esp_http_client_set_post_field(c, body, strlen(body));
    esp_err_t err = esp_http_client_perform(c);
    int status = (err == ESP_OK) ? esp_http_client_get_status_code(c) : -1;
    esp_http_client_cleanup(c);
    // Say it here, where both numbers are known. The caller will only see a
    // fragment that fails to parse, which looks like a backend fault; this line
    // names the real cause and the size that would have fixed it.
    if (sink.dropped)
        ESP_LOGE(TAG, "%s: response truncated — kept %d B, DROPPED %d B "
                      "(needed %d, buffer %d). Data was lost, not delayed.",
                 path, sink.len, sink.dropped, sink.len + sink.dropped + 1,
                 resp_cap);
    return status;
}

static int http_json(esp_http_client_method_t method, const char *path,
                     const char *body, char *resp, int resp_cap) {
    return http_json_t(method, path, body, resp, resp_cap, HTTP_TIMEOUT_MS);
}

// ---------- auth'd bodies ----------
static cJSON *base_body(void) {
    cJSON *b = cJSON_CreateObject();
    cJSON_AddStringToObject(b, "deviceId", s_cfg.device_id);
    cJSON_AddStringToObject(b, "token", s_cfg.token);
    return b;
}

static int post_cjson(esp_http_client_method_t m, const char *path, cJSON *b,
                      char *resp, int cap) {
    char *s = cJSON_PrintUnformatted(b);
    cJSON_Delete(b);
    if (!s) return -1;
    int st = http_json(m, path, s, resp, cap);
    free(s);
    return st;
}

// ---------- auth failure policy (NET-P0-1 hotfix) ----------
// This used to be: one 401 -> tiny_config_forget_identity() -> esp_restart().
// With no provisioning portal implemented, that turns a single backend auth
// hiccup into a device only a serial cable can recover — a brick on someone's
// fridge. New policy: NEVER erase NVS here. Count CONSECUTIVE 401s; only after
// kAuthFailMin of them spanning kAuthFailWindowUs do we treat it as revocation,
// and then we merely stop the fast loop and say so on the panel. We keep
// retrying slowly, because a 401 that clears is a backend blip, not a verdict.
#define AUTH_FAIL_MIN 3
#define AUTH_FAIL_WINDOW_US (5LL * 60 * 1000 * 1000)
static int s_auth_401 = 0;
static int64_t s_first_401_us = 0;
static bool s_auth_halted = false;

static void auth_ok(void) {
    if (s_auth_401 || s_auth_halted) {
        ESP_LOGW(TAG, "auth recovered after %d x 401 — resuming", s_auth_401);
        if (s_auth_halted)
            tiny_display_render_card(
                "{\"type\":\"text\",\"title\":\"back online\",\"card_id\":\"authok\","
                "\"body\":\"Auth recovered. tiny is listening again.\"}");
    }
    s_auth_401 = 0;
    s_first_401_us = 0;
    s_auth_halted = false;
}

// Returns true when the caller should back off (revocation suspected).
static bool auth_401(const char *where) {
    const int64_t now = esp_timer_get_time();
    if (s_auth_401 == 0) s_first_401_us = now;
    ++s_auth_401;
    ESP_LOGE(TAG, "%s 401 (#%d, %llds in window) — NOT wiping identity", where,
             s_auth_401, (long long)((now - s_first_401_us) / 1000000));
    if (s_auth_401 < AUTH_FAIL_MIN) return false;
    if (now - s_first_401_us < AUTH_FAIL_WINDOW_US) return false;
    if (!s_auth_halted) {
        s_auth_halted = true;
        ESP_LOGE(TAG, "auth failing for >5min — halting fast loop, keeping NVS");
        // Rescue portal renders its own card (SSID + steps). Fallback card
        // only if APSTA could not come up — never leave the panel silent.
        if (tiny_provision_start_rescue() != ESP_OK)
            tiny_display_render_card(
                "{\"type\":\"text\",\"title\":\"re-provision me\","
                "\"card_id\":\"authfail\","
                "\"body\":\"My token is being refused. I have NOT erased it. "
                "Open tiny.technology > devices > sticky to re-issue it.\","
                "\"footer\":\"retrying every 5 minutes\"}");
    }
    return true;
}

// ---------- heartbeat ----------
// Last unread count the platform reported. -1 = never told (render nothing);
// an ABSENT field in a beat reply keeps the last value — never clears it
// (spec, check 14: a worker that omits the field is not saying "zero").
static int s_unread = -1;
// §12: ms from reset to the home card on glass, and whether that boot was a
// deep-sleep wake (the pocket-glance path) or a cold/ota boot (splash path).
static int s_first_paint_ms = -1;
static bool s_glance_wake = false;
extern "C" void tiny_node_set_first_paint(int ms, bool glance_wake) {
    s_first_paint_ms = ms;
    s_glance_wake = glance_wake;
}
// §12 budget breakdown: board_init end, display_init duration (ms).
static int s_paint_board_ms = -1, s_paint_disp_ms = -1;
extern "C" void tiny_node_set_paint_breakdown(int board_ms, int disp_ms) {
    s_paint_board_ms = board_ms;
    s_paint_disp_ms = disp_ms;
}

extern "C" int tiny_node_unread(void) { return s_unread; }

static int heartbeat(void) {
    cJSON *b = base_body();
    // Advertise only verbs dispatch() actually implements — a capability the
    // platform calls and gets nothing back from is worse than a missing one.
    const char *caps[] = { "render_ui", "status", "sensors", "say", "ask", "play",
                           "voice", "screenshot", "miccheck", "page", "rotate", "glance",
                           "tap", "swipe", "scroll", "lock", "unlock",
                           "sleep", "ota", "messages", "config", "agent", "sd" };
    cJSON *arr = cJSON_AddArrayToObject(b, "capabilities");
    for (size_t i = 0; i < sizeof caps / sizeof *caps; ++i)
        cJSON_AddItemToArray(arr, cJSON_CreateString(caps[i]));
    // Opt-in DM badge (worker b553660d): reply gains {"unread":n}. STRING "1"
    // — the edge zod schema string lesson (messages_call comment) applies.
    cJSON_AddStringToObject(b, "wantUnread", "1");
    char resp[192] = "";
    const int st = post_cjson(HTTP_METHOD_POST, "/api/devices/heartbeat", b,
                              resp, sizeof resp);
    if (st == 200 && resp[0]) {
        cJSON *o = cJSON_Parse(resp);
        if (o) {
            const cJSON *u = cJSON_GetObjectItem(o, "unread");
            if (cJSON_IsNumber(u) && u->valueint >= 0 &&
                u->valueint != s_unread) {
                s_unread = u->valueint;
                ESP_LOGI(TAG, "unread -> %d", s_unread);
                // Live badge: only the home card shows it, so only home
                // re-renders — one e-ink refresh per CHANGE, none per beat.
                if (!strcmp(tiny_display_current_card_id(), "home"))
                    tiny_shell_home();
            }
            cJSON_Delete(o);
        }
    }
    return st;
}

// ---------- reply ----------
static void reply(const char *envelope_id, const char *result_text) {
    // payload is a STRING containing JSON (Nicla serializer is the reference)
    cJSON *inner = cJSON_CreateObject();
    cJSON_AddStringToObject(inner, "result", result_text);
    char *inner_s = cJSON_PrintUnformatted(inner);
    cJSON_Delete(inner);
    if (!inner_s) return;
    if (strlen(inner_s) > REPLY_MAX) inner_s[REPLY_MAX] = 0;
    cJSON *b = base_body();
    cJSON_AddStringToObject(b, "inReplyTo", envelope_id);
    cJSON_AddStringToObject(b, "payload", inner_s);
    free(inner_s);
    int st = post_cjson(HTTP_METHOD_PATCH, "/api/devices/relay", b, NULL, 0);
    ESP_LOGI(TAG, "reply %s -> %d", envelope_id, st);
}

// ---------- streaming ask transport ----------------
// POST the ask body and, when the backend answers `text/event-stream`, type
// the answer onto the glass live through the tiny_display stream API
// (partial refresh <=2Hz, caret at the tail, closing full wipe). When the
// backend answers plain JSON — an older worker that ignored `stream:"1"` —
// the body is buffered into `resp` and the caller's render-once path runs
// unchanged. ONE firmware, both backends; the stream is an enhancement.
typedef struct { bool sse; } ask_hdr_t;

static esp_err_t ask_hdr_evt(esp_http_client_event_t *e) {
    if (e->event_id == HTTP_EVENT_ON_HEADER && e->user_data &&
        e->header_key && e->header_value &&
        strcasecmp(e->header_key, "Content-Type") == 0 &&
        strstr(e->header_value, "text/event-stream"))
        ((ask_hdr_t *)e->user_data)->sse = true;
    return ESP_OK;
}

// One SSE `data:` payload -> the glass. Returns true when the stream is done
// ([DONE]). `card_out` receives a malloc'd final-card JSON when one arrives.
static bool ask_sse_line(const char *payload, char **card_out,
                         char *sum, size_t sum_cap) {
    if (strcmp(payload, "[DONE]") == 0) return true;
    cJSON *o = cJSON_Parse(payload);
    if (!o) return false;   // a torn frame: skip, the next delta carries on
    const cJSON *t = cJSON_GetObjectItem(o, "t");
    const cJSON *card = cJSON_GetObjectItem(o, "card");
    const cJSON *err = cJSON_GetObjectItem(o, "error");
    if (cJSON_IsString(t) && t->valuestring[0]) {
        tiny_display_stream_append(t->valuestring);
        tiny_display_stream_commit();   // self-limited to >=400ms; extras coalesce
        // Bounded prefix into the relay-reply summary as the words arrive.
        const size_t have = strlen(sum);
        if (have + 1 < sum_cap)
            strlcpy(sum + have, t->valuestring, sum_cap - have);
    }
    if (cJSON_IsObject(card) && card_out && !*card_out)
        *card_out = cJSON_PrintUnformatted(card);
    if (cJSON_IsString(err) && err->valuestring[0]) {
        char e[160];
        snprintf(e, sizeof e, "\n[error: %.120s]", err->valuestring);
        tiny_display_stream_append(e);
    }
    cJSON_Delete(o);
    return false;
}

// Returns HTTP status (-1 = transport failure). *streamed true = the answer
// was rendered live (resp untouched); false = resp holds the buffered JSON
// body for the classic path. `sum` accumulates the text for the relay reply.
static int ask_stream_http(const char *body, char *resp, int resp_cap,
                           bool *streamed, char *sum, size_t sum_cap,
                           const char *question) {
    *streamed = false;
    if (sum && sum_cap) sum[0] = 0;
    char url[160];
    snprintf(url, sizeof url, "%s/api/devices/ask", s_cfg.api);
    ask_hdr_t hdr = { false };
    esp_http_client_config_t hc = {};
    hc.url = url;
    hc.method = HTTP_METHOD_POST;
    // Per-READ stall bound, not whole-turn: an agent turn takes up to ~80s
    // but its deltas keep arriving; 30s of SILENCE mid-stream is a stall
    // (tools can hold the token tap that long — 15s cut real answers).
    hc.timeout_ms = 30000;
    hc.crt_bundle_attach = esp_crt_bundle_attach;
    hc.event_handler = ask_hdr_evt;
    hc.user_data = &hdr;
    esp_http_client_handle_t c = esp_http_client_init(&hc);
    if (!c) return -1;
    esp_http_client_set_header(c, "Content-Type", "application/json");
    esp_http_client_set_header(c, "Accept", "text/event-stream, application/json");
    const int blen = (int)strlen(body);
    if (esp_http_client_open(c, blen) != ESP_OK ||
        esp_http_client_write(c, body, blen) != blen) {
        esp_http_client_cleanup(c);
        return -1;
    }
    esp_http_client_fetch_headers(c);
    const int st = esp_http_client_get_status_code(c);

    if (st != 200 || !hdr.sse) {
        // Old backend / error body: buffer it whole for the classic path.
        int got = 0;
        while (got < resp_cap - 1) {
            const int n = esp_http_client_read(c, resp + got, resp_cap - 1 - got);
            if (n <= 0) break;
            got += n;
        }
        resp[got] = 0;
        esp_http_client_cleanup(c);
        return st;
    }

    // ---- SSE: type the answer onto the glass ----
    *streamed = true;
    // Text ask -> chat card (question as the user bubble, iOS shape);
    // voice ask -> plain text card (the device never sees its transcript,
    // and an invented echo would be a guess wearing quotes).
    // The ask streams ON the home surface and stays there.
    // v11: the header names WHO IS TYPING — a universe persona's words must
    // never wear the owner's tiny's name.
    char stream_title[TINY_AGENT_SLUG_MAX + 2] = "tiny";
    const char *ag = tiny_agent_current();
    if (ag[0]) snprintf(stream_title, sizeof stream_title, "@%s", ag);
    tiny_display_stream_begin(stream_title, "home", question);
    tiny_display_stream_note("(( thinking ))");
    EXT_RAM_BSS_ATTR static char line[1024];  // ask task only; one stream at a time (PSRAM .bss — P0 gate 0)
    size_t lpos = 0;
    char *final_card = NULL;
    bool done = false, cut = false;
    char chunk[512];
    while (!done) {
        const int n = esp_http_client_read(c, chunk, sizeof chunk);
        if (n <= 0) {             // closed or 30s of silence — same verdict
            cut = !done;
            break;
        }
        for (int i = 0; i < n && !done; ++i) {
            const char ch = chunk[i];
            if (ch == '\r') continue;
            if (ch != '\n') {
                if (lpos < sizeof line - 1) line[lpos++] = ch;
                continue;         // overlong line: head kept, tail dropped
            }
            line[lpos] = 0;
            lpos = 0;
            if (strncmp(line, "data:", 5) == 0) {
                const char *p = line + 5;
                while (*p == ' ') ++p;
                if (*p) done = ask_sse_line(p, &final_card, sum, sum_cap);
            }
        }
    }
    esp_http_client_cleanup(c);
    if (cut) {
        // A cut answer must say it is cut — a silent tail reads as complete.
        tiny_display_stream_append(" [stream cut]");
        if (sum && sum_cap) {
            const size_t have = strlen(sum);
            if (have + 14 < sum_cap) strlcpy(sum + have, " [stream cut]", sum_cap - have);
        }
    }
    tiny_display_stream_end(final_card);   // closing full wipe (or the card)
    if (final_card) free(final_card);
    return st;
}

// ---------- the ask engine (dispatch verb AND the AI button use this) ------
// text!=NULL → text ask. Otherwise records voice_secs of mic audio first.
// Renders any returned card; writes a human-readable summary into out.

// Last completed answer, first ~92 chars — the home card's preview line.
// One writer (the ask paths below), readers on the shell task; a
// mutex keeps a mid-write render from showing half of one answer glued to
// half of another.
static char s_last_answer[96] = "";
// Static-ctor init, not lazy: the relay `ask` verb and the button worker are
// two independent writers, and a lazy "if (!lock) create" is itself a race.
static SemaphoreHandle_t s_ans_lock = xSemaphoreCreateMutex();
static void remember_answer(const char *s) {
    if (!s || !*s) return;
    if (!s_ans_lock) return;                 // no memory: skip, never block
    xSemaphoreTake(s_ans_lock, portMAX_DELAY);
    strlcpy(s_last_answer, s, sizeof s_last_answer);
    // The relay summary may end with our own " [streamed to glass]" marker —
    // a preview quoting our plumbing back at the human is noise, drop it.
    char *m = strstr(s_last_answer, " [streamed to glass]");
    if (m) *m = 0;
    xSemaphoreGive(s_ans_lock);
}
extern "C" esp_err_t tiny_node_last_answer(char *out, size_t cap) {
    if (!out || !cap) return ESP_ERR_INVALID_ARG;
    if (!s_ans_lock || !s_last_answer[0]) { out[0] = 0; return ESP_ERR_NOT_FOUND; }
    xSemaphoreTake(s_ans_lock, portMAX_DELAY);
    strlcpy(out, s_last_answer, cap);
    xSemaphoreGive(s_ans_lock);
    return ESP_OK;
}

// ONE ask at a time, across ALL callers. The AI button already single-flights
// itself through its queue, but the relay dispatch runs on the node task —
// a ghost button press racing a relay ask made both write the same static
// buffers and the same display stream, and the streamed summary came back
// with the two answers' words INTERLEAVED (seen on glass in 0.15.2). The panel is one surface and the mic is one mic: a second
// concurrent ask has no correct output, so it is refused, not queued.
static SemaphoreHandle_t s_ask_mux = NULL;

// §16 drain context. The node task's drain re-uses do_ask, but a drained ask
// must NOT re-enqueue on transport failure (the text is STILL AT THE HEAD —
// pushing it again at the tail turns one question into two answers, the exact
// property the retry rule forbids) and must not paint the "couldn't reach
// tiny / ask again" card over whatever the owner is viewing (the question
// remains queued; there is nothing for them to do). s_ask_backend_spoke lets
// the drain honor "st > 0 is NEVER retried": if the backend answered — even
// with an error — the queued ask is spent, not retried forever.
static bool s_askq_draining = false;
static bool s_ask_backend_spoke = false;

extern "C" esp_err_t tiny_node_do_ask(const char *text, int voice_secs,
                                      char *out, size_t out_cap) {
    if (!s_ask_mux) s_ask_mux = xSemaphoreCreateMutex();
    if (!s_ask_mux || xSemaphoreTake(s_ask_mux, 0) != pdTRUE) {
        snprintf(out, out_cap, "ask already in flight - one panel, one answer");
        return ESP_ERR_INVALID_STATE;
    }
    // RAII release: this function has six return paths and a manual give on
    // each would eventually miss one (that is how locks die).
    struct AskGuard { ~AskGuard() { xSemaphoreGive(s_ask_mux); } } guard;
    s_ask_backend_spoke = false;  // §16 drain context — set true when st > 0
    static char audio_url[256];
    audio_url[0] = 0;
    if (!text) {
        uint8_t *wav = NULL; size_t wlen = 0;
        // The mic tap's visible answer is HOME changing state, not a new
        // card (owner: "the microphone box" must not appear). Paint the
        // listening frame first — the chime that follows marks capture
        // start, as before.
        tiny_display_stream_note("(( listening ))");
        tiny_display_stream_begin("tiny", "home", NULL);
        tiny_audio_chime("ask");
        esp_err_t r = tiny_audio_record_wav(voice_secs, &wav, &wlen);
        if (r != ESP_OK) {
            // §11 copy: name the reason, not the
            // errno — the entry-point gate returns NOT_ALLOWED only for lock.
            if (r == ESP_ERR_NOT_ALLOWED)
                snprintf(out, out_cap, "mic refused: device is pocket-locked "
                         "(S11) - unlock (UP+DOWN 1s) or send unlock");
            else
                snprintf(out, out_cap, "record failed: %s", esp_err_to_name(r));
            return r;
        }
        tiny_audio_chime("ack");  // "heard you" — upload+think takes a while
        tiny_display_stream_note("(( thinking ))");
        r = tiny_upload_media(wav, wlen, "audio/wav", audio_url, sizeof audio_url);
        free(wav);
        if (r != ESP_OK) { snprintf(out, out_cap, "media upload failed"); return r; }
    }
    cJSON *b = base_body();
    if (audio_url[0]) cJSON_AddStringToObject(b, "audioUrl", audio_url);
    else              cJSON_AddStringToObject(b, "text", text ? text : "");
    // SSE opt-in ): a backend that knows the flag answers
    // text/event-stream and the words type onto the glass live; one that
    // does not simply ignores the field and the classic path below runs.
    cJSON_AddStringToObject(b, "stream", "1");
    // THE UNIVERSE (grammar v11): a non-empty active slug rides the ask as
    // `tiny:"<slug>"` and the turn runs AS that public tiny (backend contract
    // 61b404f0). Empty slug adds NOTHING — the owner-default body stays
    // byte-identical to pre-universe firmware (backend regression-pinned).
    // Both ask paths (typed AND voice) funnel through here, so both carry it.
    const char *agent = tiny_agent_current();
    if (agent[0]) cJSON_AddStringToObject(b, "tiny", agent);
    char *bs = cJSON_PrintUnformatted(b);
    cJSON_Delete(b);
    EXT_RAM_BSS_ATTR static char ask_resp[8192];  // PSRAM .bss
    EXT_RAM_BSS_ATTR static char stream_sum[448];  // PSRAM .bss
    bool streamed = false;
    int st = bs ? ask_stream_http(bs, ask_resp, sizeof ask_resp, &streamed,
                                  stream_sum, sizeof stream_sum, text) : -1;
    ESP_LOGI(TAG, "ask -> %d%s", st, streamed ? " (streamed)" : "");
    // ONE retry, and only for a transport failure. `st <= 0` is
    // ask_stream_http's sentinel for "the request never left the device" —
    // client init failed, or the TLS open/write did (see its three return -1
    // paths). Nothing was delivered, so re-sending cannot produce a second
    // answer to one question, which is the property that makes a retry safe
    // here and unsafe anywhere else.
    //
    // The owner hit this with a typed "hey" and was told
    // "Backend said -1:" with an empty body. One -1 on a device whose OTA path
    // moves a megabyte over this same TLS stack is a transient, not a broken
    // configuration — most likely heap pressure at handshake time, which is why
    // the log line below carries heap_free.
    //
    // Bounded to ONE on purpose. A loop against a genuinely-down backend would
    // hammer it while holding the ask mutex, with the panel showing nothing —
    // a hang dressed as diligence. And st > 0 is NEVER retried: the backend
    // spoke, and repeating a question it already answered or rejected is how
    // one question becomes two answers.
    if (bs && st <= 0 && !streamed) {
        ESP_LOGW(TAG, "ask transport failed (%d) — heap_free %lu, errno %d; "
                      "retrying once",
                 st, (unsigned long)esp_get_free_heap_size(), errno);
        vTaskDelay(pdMS_TO_TICKS(400));  // let a DNS/socket blip clear
        ask_resp[0] = 0;
        stream_sum[0] = 0;
        streamed = false;
        st = ask_stream_http(bs, ask_resp, sizeof ask_resp, &streamed,
                             stream_sum, sizeof stream_sum, text);
        ESP_LOGI(TAG, "ask retry -> %d%s", st, streamed ? " (streamed)" : "");
    }
    if (bs) free(bs);
    if (st > 0) s_ask_backend_spoke = true;  // the backend SAW this question
    if (streamed) {
        // Already on the glass, typed live. The relay reply carries the text.
        snprintf(out, out_cap, "%s [streamed to glass]",
                 stream_sum[0] ? stream_sum : "(no text)");
        remember_answer(stream_sum);  // home card preview
        if (audio_url[0]) {
            char note[300];
            snprintf(note, sizeof note, "voice ask ok: %s", audio_url);
            tiny_node_post_event("device_note", note);
        }
        return ESP_OK;
    }
    if (st != 200) {
        // The 422 investigation: keep the backend's own
        // words. The body says which field it hated; a bare status code says
        // nothing anyone can act on.
        ESP_LOGE(TAG, "ask %d body: %.512s", st, ask_resp);
        if (st <= 0 && text && !s_askq_draining) {
            // §16 law 2: nothing TYPED is lost. The request never left the
            // device (st<=0 is the never-delivered sentinel, same property
            // that made the retry safe) — queue it in NVS and say so. The
            // node task drains one per successful heartbeat, in order.
            esp_err_t qr = tiny_askq_push(text);
            if (qr == ESP_OK)
                snprintf(out, out_cap,
                         "offline - question saved (%d/8), sends when the "
                         "network returns", tiny_askq_count());
            else if (qr == ESP_ERR_NO_MEM)
                snprintf(out, out_cap,
                         "offline - queue full (8/8): oldest questions are "
                         "still waiting for the network");
            else
                snprintf(out, out_cap,
                         "ask NOT SENT (transport %d) and the queue refused "
                         "(%s) - retype it when the network is back", st,
                         esp_err_to_name(qr));
        } else if (st <= 0)
            snprintf(out, out_cap,
                     "ask NOT SENT (transport %d, heap %lu): the request never "
                     "left the device, and the one retry failed too", st,
                     (unsigned long)esp_get_free_heap_size());
        else
            snprintf(out, out_cap, "ask failed (%d): %.256s", st, ask_resp);
        // A failed DRAIN paints nothing: nobody is standing at a question
        // they just asked — the ask stays queued (transport) or is spent
        // (backend spoke, logged), and an error card here would shout over
        // an unrelated page the owner is reading.
        if (s_askq_draining) {
            ESP_LOGW(TAG, "askq drain failed (st %d, backend_spoke %d) - %s",
                     st, (int)s_ask_backend_spoke,
                     s_ask_backend_spoke ? "spent" : "stays queued");
            return ESP_FAIL;
        }
        // And the panel must say something honest: a button ask that fails
        // silently reads as a dead device to the person standing there.
        cJSON *ec = cJSON_CreateObject();
        cJSON_AddStringToObject(ec, "type", "text");
        cJSON_AddStringToObject(ec, "card_id", "askfail");
        if (st <= 0 && text && strstr(out, "question saved")) {
            // Queued is not a failure — the card says what actually happens
            // next, and the glance cluster wears qN until it does.
            cJSON_AddStringToObject(ec, "title", "saved for later");
            cJSON_AddStringToObject(ec, "body", out);
        } else if (st == 422 && audio_url[0]) {
            cJSON_AddStringToObject(ec, "title", "didn't catch that");
            cJSON_AddStringToObject(ec, "body",
                "I recorded, but the backend couldn't use it - probably no "
                "speech in the clip. Hold the AI button and talk to me.");
        } else if (st <= 0) {
            // "Backend said -1:" attributed to the backend a sentence it never
            // said — the request never reached it. That is not a rounding error
            // in the wording, it points the person at the wrong system: they go
            // looking at tiny while the fault is a socket on this desk. The
            // honesty rule is that a refusal names its real reason, and "your
            // question was not sent" is something a person can act on where a
            // fabricated backend quote is not. Note it says NOT sent, present
            // tense and unambiguous, because the one thing the owner needs to
            // know is whether to retype it.
            cJSON_AddStringToObject(ec, "title", "couldn't reach tiny");
            cJSON_AddStringToObject(ec, "body",
                "Your question never left the device - a network hiccup or low "
                "memory. It was NOT sent, and the one automatic retry failed "
                "too. Tap Type and ask me again.");
        } else {
            // v11: the backend's refusal sentences (unknown/private slug 404,
            // priced 402 — contract 61b404f0) are answers, not wreckage.
            // {"error":"<sentence>"} renders as normal answer text under the
            // asked persona's name; only a body with no sentence in it gets
            // the raw-status treatment below.
            cJSON *eo = cJSON_Parse(ask_resp);
            const cJSON *es = eo ? cJSON_GetObjectItem(eo, "error") : NULL;
            if (cJSON_IsString(es) && es->valuestring[0]) {
                const char *agent2 = tiny_agent_current();
                char et[TINY_AGENT_SLUG_MAX + 2] = "tiny";
                if (agent2[0]) snprintf(et, sizeof et, "@%s", agent2);
                cJSON_ReplaceItemInObject(ec, "card_id",
                                          cJSON_CreateString("ask"));
                cJSON_AddStringToObject(ec, "title", et);
                cJSON_AddStringToObject(ec, "body", es->valuestring);
                snprintf(out, out_cap, "%.400s", es->valuestring);
            } else {
                cJSON_AddStringToObject(ec, "title", "ask failed");
                char eb[192];
                snprintf(eb, sizeof eb, "Backend said %d: %.120s", st, ask_resp);
                cJSON_AddStringToObject(ec, "body", eb);
            }
            if (eo) cJSON_Delete(eo);
        }
        char ef[80];
        if (st <= 0)
            // Free heap on the glass, because heap pressure at TLS-handshake
            // time is the leading suspect and the footer is the only place the
            // owner can read it without a cable.
            snprintf(ef, sizeof ef, "not sent (%d) - heap %lu B - tiny sticky",
                     st, (unsigned long)esp_get_free_heap_size());
        else
            snprintf(ef, sizeof ef, "http %d - tiny sticky", st);
        cJSON_AddStringToObject(ec, "footer", ef);
        char *ecs = cJSON_PrintUnformatted(ec);
        cJSON_Delete(ec);
        if (ecs) { tiny_display_render_card(ecs); free(ecs); }
        return ESP_FAIL;
    }
    cJSON *ar = cJSON_Parse(ask_resp);
    const cJSON *txt = ar ? cJSON_GetObjectItem(ar, "text") : NULL;
    const cJSON *card = ar ? cJSON_GetObjectItem(ar, "card") : NULL;
    bool rendered = false;
    if (cJSON_IsObject(card)) {
        char *cs = cJSON_PrintUnformatted(card);
        if (cs) { rendered = tiny_display_render_card(cs) == ESP_OK; free(cs); }
    } else if (cJSON_IsString(txt) && txt->valuestring[0]) {
        // No card in the answer — wrap the prose so the panel always shows
        // SOMETHING after a button ask (an answer nobody can see didn't happen).
        cJSON *tc = cJSON_CreateObject();
        cJSON_AddStringToObject(tc, "type", "text");
        cJSON_AddStringToObject(tc, "title", "tiny");
        cJSON_AddStringToObject(tc, "body", txt->valuestring);
        char *tcs = cJSON_PrintUnformatted(tc);
        cJSON_Delete(tc);
        if (tcs) { rendered = tiny_display_render_card(tcs) == ESP_OK; free(tcs); }
    }
    snprintf(out, out_cap, "%s%s",
             cJSON_IsString(txt) ? txt->valuestring : "(no text)",
             rendered ? " [card rendered]" : "");
    if (cJSON_IsString(txt)) remember_answer(txt->valuestring);  // home card preview
    if (ar) cJSON_Delete(ar);
    if (audio_url[0]) {
        char note[300];
        snprintf(note, sizeof note, "voice ask ok: %s", audio_url);
        tiny_node_post_event("device_note", note);
    }
    return ESP_OK;
}

// Defined with the relay poll it governs (~line 1060), read here so `status`
// can report the cadence it is actually running. Forward-declared rather than
// moved: the definition belongs next to the loop it times. (0.14.14 shipped
// without this line and HEAD did not compile — 'cadence_idle' was not declared
// in this scope — which blocks every build at once, so it is fixed in place.)
static bool cadence_idle(void);
// Age of the activity window AT the last envelope's arrival, pre-snap —
// the one number the probe cannot pollute by asking.
// Defined here (not with the cadence statics below) because build_status
// reads it; ACTIVE_WINDOW_US rides along for the same reason.
static int s_idle_at_envelope_s = 0;
#define ACTIVE_WINDOW_US (2 * 60 * 1000000LL)

// ---------- status: ONE source of truth ----------
// The dashboard parses these fields; the `status` verb replies with this exact
// object (plus a `summary` sentence so a human reading the relay transcript
// still gets a sentence). Unknown values are JSON null — never a guess: a
// fabricated battery percentage is worse than an empty one.
void tiny_node_crumb_boot_capture(void);   // black box (defined near dispatch)
const char *tiny_node_died_doing(void);

extern "C" esp_err_t tiny_node_status_json(char *out, size_t cap) {
    if (!out || cap == 0) return ESP_ERR_INVALID_ARG;
    BatteryDetail bd = {};
    const bool have_batt = sticky_battery_read_detail(bd) == ESP_OK;
    BatteryReading br;
    br.percent = bd.percent;
    const bool wifi_up = tiny_wifi_is_up();
    wifi_ap_record_t ap = {};
    const bool have_ap = wifi_up && esp_wifi_sta_get_ap_info(&ap) == ESP_OK;
    const unsigned long heap = (unsigned long)esp_get_free_heap_size();
    const long long up_s = esp_timer_get_time() / 1000000;

    cJSON *s = cJSON_CreateObject();
    cJSON_AddStringToObject(s, "fw", TINY_FW_VERSION);
    cJSON_AddStringToObject(s, "fw_commit", TINY_FW_COMMIT);
    cJSON_AddNumberToObject(s, "grammar_version", TINY_GRAMMAR_VERSION);
    // v11: who answers the asks. "" = the owner's own tiny (default). The
    // dashboard/reviewer verify agent switching without eyes on the glass.
    cJSON_AddStringToObject(s, "active_agent", tiny_agent_current());
    // v12: where the first-run flow stands ("done" = home owns the glass).
    cJSON_AddStringToObject(s, "onboard", tiny_onboard_step_name(tiny_onboard_step()));
    // Verification hook (UI_SPEC §7): reviewers have no ears.
    cJSON_AddBoolToObject(s, "silent", sticky_buzzer_silent());
    // Instrumentation: the RAM gate and its NVS shadow, side by side.
    // They must agree on every boot path — a divergence here convicts the
    // restore ordering without a single glass op.
    cJSON_AddBoolToObject(s, "silent_nvs", tiny_config_silent_get());
    cJSON_AddStringToObject(s, "poll_cadence", cadence_idle() ? "idle-60s" : "active-5s");
    // Observer effect: poll_cadence above is ALWAYS active-5s to a probe (the
    // probe's own envelope snapped it — observer effect). These two speak
    // from BEFORE the snap: what the cadence actually was when this status
    // request arrived. idle_for_s >= 120 proves the backoff engaged.
    cJSON_AddNumberToObject(s, "idle_for_s", s_idle_at_envelope_s);
    cJSON_AddStringToObject(s, "cadence_at_poll",
                            s_idle_at_envelope_s > ACTIVE_WINDOW_US / 1000000
                                ? "idle-60s" : "active-5s");
    // P0-BOUNCE flight recorder: the stage the PREVIOUS boot reached (9 =
    // clean through validity mark; anything less names where it died).
    cJSON_AddNumberToObject(s, "boot_mark_prev", tiny_bootmark_prev());
    // §12 receipt: how fast this boot reached the glance-bearing home card.
    if (s_first_paint_ms >= 0) {
        cJSON_AddNumberToObject(s, "first_paint_ms", s_first_paint_ms);
        cJSON_AddStringToObject(s, "boot_path",
                                s_glance_wake ? "glance-wake" : "cold-splash");
        if (s_paint_board_ms >= 0) {
            // board_ms = reset->board_init done; display_ms = SSD1677 init;
            // the remainder to first_paint_ms is shell render + e-ink flash.
            cJSON *pb = cJSON_CreateObject();
            cJSON_AddNumberToObject(pb, "board_ms", s_paint_board_ms);
            cJSON_AddNumberToObject(pb, "display_init_ms", s_paint_disp_ms);
            cJSON_AddItemToObject(s, "paint_breakdown", pb);
        }
    }
    cJSON_AddStringToObject(s, "device_id", s_cfg.device_id);
    if (have_batt) cJSON_AddNumberToObject(s, "battery_pct", br.percent);
    else           cJSON_AddNullToObject(s, "battery_pct");
    // charging comes from the BQ27220 DSG status bit; still null (not false)
    // when the gauge did not answer, because "not charging" is a claim.
    if (have_batt) cJSON_AddBoolToObject(s, "charging", bd.charging);
    else           cJSON_AddNullToObject(s, "charging");
    cJSON_AddBoolToObject(s, "wifi", wifi_up);
    if (have_ap) {
        cJSON_AddNumberToObject(s, "rssi_dbm", ap.rssi);
        cJSON_AddStringToObject(s, "ssid", (const char *)ap.ssid);
    } else {
        cJSON_AddNullToObject(s, "rssi_dbm");
    }
    cJSON_AddNumberToObject(s, "heap_free", (double)heap);
    cJSON_AddNumberToObject(
        s, "psram_free", (double)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    // P0 telemetry (0.23.0 serial autopsy: TLS alloc-fail loop at t+4min).
    // heap_free is dominated by PSRAM and LIES about the allocation that
    // kills us: TLS/lwip need INTERNAL DMA-capable RAM. internal_free is the
    // rail that dies; internal_min_free is its lowest-ever watermark (the
    // leak's slope, readable from two status calls); last_boot names how the
    // previous life ended (panic/task-wdt/brownout arrive labeled, not as
    // anonymous "cold-splash").
    cJSON_AddNumberToObject(s, "internal_free",
        (double)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    cJSON_AddNumberToObject(s, "internal_min_free",
        (double)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    // P0 lesson (stack overflow convicted by serial 2026-08-26): the node
    // task's own stack headroom, lowest-ever. The overflow gave no heap
    // symptom at all — heap watermarks can't see a stack death. This can.
    cJSON_AddNumberToObject(s, "node_stack_min_free",
        (double)uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t));
    // SD card: present = the physical detect switch, mounted = FAT is
    // live at /sdcard. Sizes only when mounted (a guess is worse than null).
    {
        cJSON *sd = cJSON_CreateObject();
        cJSON_AddBoolToObject(sd, "present", sticky_sdcard_present());
        cJSON_AddBoolToObject(sd, "mounted", sticky_sdcard_mounted());
        uint64_t sd_total = 0, sd_free = 0;
        if (sticky_sdcard_mounted() &&
            sticky_sdcard_info(&sd_total, &sd_free) == ESP_OK) {
            cJSON_AddNumberToObject(sd, "size_gb",
                (double)(uint64_t)((sd_total / 1e9) * 10 + 0.5) / 10.0);
            cJSON_AddNumberToObject(sd, "free_gb",
                (double)(uint64_t)((sd_free / 1e9) * 10 + 0.5) / 10.0);
        } else {
            cJSON_AddNullToObject(sd, "size_gb");
            cJSON_AddNullToObject(sd, "free_gb");
        }
        cJSON_AddItemToObject(s, "sd", sd);
    }
    {
        const char *rr = "unknown";
        switch (esp_reset_reason()) {
            case ESP_RST_POWERON:   rr = "poweron";   break;
            case ESP_RST_SW:        rr = "sw-reset";  break;  // OTA reboot lands here
            case ESP_RST_PANIC:     rr = "panic";     break;
            case ESP_RST_INT_WDT:   rr = "int-wdt";   break;
            case ESP_RST_TASK_WDT:  rr = "task-wdt";  break;
            case ESP_RST_WDT:       rr = "other-wdt"; break;
            case ESP_RST_DEEPSLEEP: rr = "deepsleep-wake"; break;
            case ESP_RST_BROWNOUT:  rr = "brownout";  break;
            case ESP_RST_USB:       rr = "usb";       break;
            case ESP_RST_JTAG:      rr = "jtag";      break;
            default: break;
        }
        cJSON_AddStringToObject(s, "last_boot", rr);
        // black box: only present when the previous life ended abnormally.
        if (tiny_node_died_doing()[0])
            cJSON_AddStringToObject(s, "died_doing", tiny_node_died_doing());
    }
    cJSON_AddNumberToObject(s, "uptime_s", (double)up_s);
    cJSON_AddBoolToObject(s, "sleeping", false);  // deep sleep lands in M7b
    // §11 spec-mandated truth surface: the lock state is a status field.
    cJSON_AddBoolToObject(s, "locked", tiny_lock_is_locked());
    cJSON_AddStringToObject(s, "display", "800x480 4-gray");
    cJSON_AddStringToObject(s, "card_id", tiny_display_current_card_id());
    cJSON_AddBoolToObject(s, "auth_halted", s_auth_halted);
    char summary[224];
    if (have_batt)
        snprintf(summary, sizeof summary,
                 "sticky online: fw %s, wifi %s, heap %lu, battery %d%%, "
                 "up %llds, display 800x480 ready",
                 TINY_FW_VERSION, wifi_up ? "up" : "down", heap, br.percent,
                 up_s);
    else
        snprintf(summary, sizeof summary,
                 "sticky online: fw %s, wifi %s, heap %lu, battery n/a, "
                 "up %llds, display 800x480 ready",
                 TINY_FW_VERSION, wifi_up ? "up" : "down", heap, up_s);
    cJSON_AddStringToObject(s, "summary", summary);

    char *js = cJSON_PrintUnformatted(s);
    cJSON_Delete(s);
    if (!js) return ESP_ERR_NO_MEM;
    // Same discipline as tiny_sensors_json, and for the same reason: strlcpy
    // reports the length it WANTED and this call used to throw that away, so a
    // status object one field too big would reply JSON cut mid-token while
    // returning ESP_OK — invisible on the device, a parse error at the
    // dashboard. `sensors` shipped exactly that defect on 2026-08-26; this verb
    // is called far more often and had ~111 bytes of headroom left (measured:
    // ~529 B worst case into 640), i.e. one float away. A refusal that names
    // the byte count is the only reply that stays useful when it happens.
    const size_t need = strlcpy(out, js, cap);
    free(js);
    if (need >= cap) {
        ESP_LOGE(TAG, "status json needs %u bytes, buffer is %u — refusing to "
                      "return truncated JSON", (unsigned)need + 1, (unsigned)cap);
        snprintf(out, cap,
                 "{\"error\":\"status json truncated\",\"need_bytes\":%u,"
                 "\"buffer_bytes\":%u}", (unsigned)need + 1, (unsigned)cap);
        return ESP_ERR_INVALID_SIZE;
    }
    return ESP_OK;
}

// The "Status" screen button renders this WITHOUT the network: a tap has to
// work when the backend is down, or the button is a lie.
extern "C" esp_err_t tiny_node_render_status_card(void) {
    BatteryReading br;
    const bool have_batt = sticky_battery_read(br) == ESP_OK;
    char batt[16];
    if (have_batt) snprintf(batt, sizeof batt, "%d%%", br.percent);
    else           strlcpy(batt, "n/a", sizeof batt);
    wifi_ap_record_t ap = {};
    const bool have_ap =
        tiny_wifi_is_up() && esp_wifi_sta_get_ap_info(&ap) == ESP_OK;
    // ssid and rssi are two facts — fused they overflow the portrait
    // value column into an indented orphan line. Each gets its own row.
    char net[40], sig[16] = "";
    if (have_ap) {
        snprintf(net, sizeof net, "%s", (const char *)ap.ssid);
        snprintf(sig, sizeof sig, "%d dBm", ap.rssi);
    } else {
        strlcpy(net, tiny_wifi_is_up() ? "up" : "down", sizeof net);
    }
    const long long up_s = esp_timer_get_time() / 1000000;

    cJSON *c = cJSON_CreateObject();
    cJSON_AddStringToObject(c, "type", "kv");
    cJSON_AddStringToObject(c, "title", "sticky");
    cJSON_AddStringToObject(c, "card_id", "status");
    cJSON *rows = cJSON_AddObjectToObject(c, "rows");
    cJSON_AddStringToObject(rows, "firmware", TINY_FW_VERSION);
    cJSON_AddStringToObject(rows, "battery", batt);
    cJSON_AddStringToObject(rows, "wifi", net);
    if (sig[0]) cJSON_AddStringToObject(rows, "signal", sig);
    char tmp[48];
    snprintf(tmp, sizeof tmp, "%lluh %llum", up_s / 3600, (up_s % 3600) / 60);
    cJSON_AddStringToObject(rows, "uptime", tmp);
    snprintf(tmp, sizeof tmp, "%lu KB", (unsigned long)(esp_get_free_heap_size() / 1024));
    cJSON_AddStringToObject(rows, "free heap", tmp);
    // Reviewer note on the silent queue: a pocket owner checking why the
    // device is quiet must find the answer HERE, not only in a JSON verb.
    cJSON_AddStringToObject(rows, "sound",
                            sticky_buzzer_silent() ? "SILENT" : "on");
    cJSON *btns = cJSON_AddArrayToObject(c, "buttons");
    cJSON *b1 = cJSON_CreateObject();
    cJSON_AddStringToObject(b1, "id", "ask");
    cJSON_AddStringToObject(b1, "label", "Ask tiny");
    cJSON_AddItemToArray(btns, b1);
    cJSON *b2 = cJSON_CreateObject();
    cJSON_AddStringToObject(b2, "id", "status");
    cJSON_AddStringToObject(b2, "label", "Refresh");
    cJSON_AddItemToArray(btns, b2);
    // Navigation law (UI_SPEC §1): every root page carries [Home].
    cJSON *b3 = cJSON_CreateObject();
    cJSON_AddStringToObject(b3, "id", "home");
    cJSON_AddStringToObject(b3, "label", "Home");
    cJSON_AddItemToArray(btns, b3);
    char *cs = cJSON_PrintUnformatted(c);
    cJSON_Delete(c);
    if (!cs) return ESP_ERR_NO_MEM;
    esp_err_t r = tiny_display_render_card(cs);
    free(cs);
    return r;
}

// ---------- messages: the device reads its owner's mail with its OWN token ----
// POST /api/devices/messages {deviceId, token, op, with?, limit?} — the edge
// route shipped 2026-08-26 (docs/research/MESSAGES_TRANSPORT.md option B). Ops:
// unread | inbox | thread | send. Before this the panel could only show messages
// an owner-side process pushed to it; now the app lives here.
//
// `limit` MUST be a STRING. The edge zod schema rejects a number outright — I
// probed the live route first and got 400 {"expected":"string","received":
// "number"}, which is a build cycle this comment saves the next person.
//
// The reply is big (a thread carries whole message bodies), so these calls get
// their own PSRAM buffer instead of the node task's 4 KB `resp`: the HTTP sink
// truncates silently at the cap, and a half-JSON reply parses to nothing.
#define MSG_BUF_BYTES 12288

static char *msg_buf_alloc(void) {
    char *b = (char *)heap_caps_malloc(MSG_BUF_BYTES, MALLOC_CAP_SPIRAM);
    if (!b) b = (char *)malloc(MSG_BUF_BYTES);
    if (b) b[0] = 0;
    return b;
}

static int messages_call(const char *op, const char *with, const char *limit,
                         const char *body_text, char *resp, int cap) {
    cJSON *b = base_body();
    cJSON_AddStringToObject(b, "op", op);
    // BUG-1 (2026-08-26, demo dm-err): the worker's send contract is
    // `to` (recipient), while thread/inbox use `with` (peer). This function
    // put the login in `with` for BOTH — so every device DM send arrived
    // without a recipient and 400'd. Verified against DeviceMessagesCall's
    // schema (chatgpt-plugin-tinyai/src/messages.ts: "op=send + to, body").
    if (with && *with)
        cJSON_AddStringToObject(b, strcmp(op, "send") == 0 ? "to" : "with", with);
    if (limit && *limit) cJSON_AddStringToObject(b, "limit", limit);
    if (body_text && *body_text) cJSON_AddStringToObject(b, "body", body_text);
    return post_cjson(HTTP_METHOD_POST, "/api/devices/messages", b, resp, cap);
}

// "7d" / "3h" / "12m" / "now" — a mail list without ages is a list of strangers.
static void age_str(double at, char *out, size_t cap) {
    const time_t now = time(NULL);
    long d = (at > 0 && now > 0) ? (long)((double)now - at) : -1;
    if (d < 0)          snprintf(out, cap, "?");
    else if (d < 90)    snprintf(out, cap, "now");
    else if (d < 5400)  snprintf(out, cap, "%ldm", d / 60);
    else if (d < 172800)snprintf(out, cap, "%ldh", d / 3600);
    else                snprintf(out, cap, "%ldd", d / 86400);
}

static const char *jstr(cJSON *o, const char *k) {
    cJSON *v = cJSON_GetObjectItem(o, k);
    return (v && cJSON_IsString(v)) ? v->valuestring : "";
}

// BUG-2 (2026-08-26, "K�rg�l" in the event feed): ascii_fold maps Turkish
// codepoints to PRIVATE font-bank bytes 0x80..0x8B so the GLASS shows real
// glyphs (ı ğ ş...). Those bytes are a device-internal encoding — on the wire
// they are invalid UTF-8 and the edge decodes each to U+FFFD. Every string
// that leaves the device (event details, tap-reply labels) must fold the bank
// back to ASCII transliteration first. In-place: bank chars are 1 byte and so
// are their substitutes. Table order matches kCpFolds' bank assignments.
static void wire_fold_bank(char *s) {
    static const char kBankAscii[12] = { 'c','C','g','G','i','I',
                                         'o','O','s','S','u','U' };
    if (!s) return;
    for (; *s; ++s) {
        const unsigned char ch = (unsigned char)*s;
        if (ch >= 0x80 && ch <= 0x8B) *s = kBankAscii[ch - 0x80];
    }
}

// Clip on a UTF-8 boundary: cutting mid-sequence leaves a dangling lead byte
// that ascii_fold can only render as '?', so a truncated Turkish name would end
// in punctuation-looking garbage.
static void clip_utf8(char *s, size_t max_bytes) {
    if (!s || strlen(s) <= max_bytes) return;
    size_t n = max_bytes;
    while (n > 0 && ((unsigned char)s[n] & 0xC0) == 0x80) --n;
    s[n] = 0;
}

// Inbox -> `menu` card. Row ids are "m:t:<login>" so the TOUCH task can open the
// thread on its own (like the shell's "k:"/"w:" ids) — the panel is an app here,
// not a screen someone else drives. Labels are other people's words, which is
// safe as of 0.14.6-m12: action_for() only sniffs a label when no id was given.
static esp_err_t render_inbox(const char *json, char *sum, size_t scap) {
    cJSON *root = cJSON_Parse(json);
    if (!root) { snprintf(sum, scap, "inbox reply did not parse"); return ESP_FAIL; }
    cJSON *threads = cJSON_GetObjectItem(root, "threads");
    if (!cJSON_IsArray(threads)) {
        cJSON_Delete(root);
        snprintf(sum, scap, "inbox reply had no threads array");
        return ESP_FAIL;
    }
    cJSON *card = cJSON_CreateObject();
    cJSON_AddStringToObject(card, "type", "menu");
    cJSON_AddStringToObject(card, "card_id", "dm-inbox");
    cJSON_AddStringToObject(card, "title", "messages");  // lowercase voice
    cJSON *items = cJSON_AddArrayToObject(card, "items");
    int n = 0, unread_total = 0;
    cJSON *t = NULL;
    cJSON_ArrayForEach(t, threads) {
        if (n >= 8) break;  // 8 rows scroll comfortably in a 258 px body box
        const char *login = jstr(t, "login");
        const char *name = jstr(t, "name");
        if (!*name) name = login;
        cJSON *un = cJSON_GetObjectItem(t, "unread");
        const int unread = cJSON_IsNumber(un) ? un->valueint : 0;
        unread_total += unread;
        cJSON *la = cJSON_GetObjectItem(t, "lastAt");
        char age[12];
        age_str(cJSON_IsNumber(la) ? la->valuedouble : 0, age, sizeof age);

        char label[96];
        snprintf(label, sizeof label, "%s - %s", name, jstr(t, "lastBody"));
        clip_utf8(label, 56);
        // 48, not 64, and CHECKED. A 64-byte producer feeding 48-byte consumers
        // (render_menu_card's id[48], then stage_region's r.id[48]) is worse than
        // a small buffer: this snprintf succeeds, and the clipping happens later,
        // silently, in a strlcpy — so a login over 43 chars produced a row that
        // opens somebody else's conversation with nothing logged. Its two
        // siblings on this same data already refuse (rid[56] omits [Reply],
        // tid[48] omits the Thread button); this was the last one still guessing.
        char note[24], id[48];
        snprintf(note, sizeof note, unread ? "%s +%d" : "%s", age, unread);
        const int idn = snprintf(id, sizeof id, "m:t:%s", login);

        cJSON *row = cJSON_CreateObject();
        cJSON_AddStringToObject(row, "label", label);
        cJSON_AddStringToObject(row, "note", note);
        // An inert row beats a row that opens the wrong thread. "noop" is the
        // documented do-nothing id, and the label still shows who wrote — the
        // human sees the conversation exists, just cannot open it from here.
        if (idn > 0 && (size_t)idn < sizeof id) {
            cJSON_AddStringToObject(row, "id", id);
        } else {
            cJSON_AddStringToObject(row, "id", "noop");
            ESP_LOGE(TAG, "inbox row: login %d B does not fit a %u B button id — "
                          "row rendered INERT rather than pointing at the wrong "
                          "conversation", (int)strlen(login), (unsigned)sizeof id);
        }
        cJSON_AddItemToArray(items, row);
        ++n;
    }
    if (!n) {
        cJSON *row = cJSON_CreateObject();
        cJSON_AddStringToObject(row, "label", "no conversations yet");
        cJSON_AddStringToObject(row, "id", "noop");
        cJSON_AddItemToArray(items, row);
    }
    char footer[128];
    snprintf(footer, sizeof footer, "%d conversations - %d unread - tap a row",
             cJSON_GetArraySize(threads), unread_total);
    cJSON_AddStringToObject(card, "footer", footer);
    cJSON *btns = cJSON_AddArrayToObject(card, "buttons");
    cJSON *b1 = cJSON_CreateObject();
    cJSON_AddStringToObject(b1, "id", "m:inbox");
    cJSON_AddStringToObject(b1, "label", "Reload");
    cJSON_AddItemToArray(btns, b1);
    cJSON *b2 = cJSON_CreateObject();
    cJSON_AddStringToObject(b2, "id", "home");
    cJSON_AddStringToObject(b2, "label", "Home");
    cJSON_AddItemToArray(btns, b2);

    char *cs = cJSON_PrintUnformatted(card);
    cJSON_Delete(card);
    cJSON_Delete(root);
    if (!cs) { snprintf(sum, scap, "inbox card out of memory"); return ESP_ERR_NO_MEM; }
    esp_err_t r = tiny_display_render_card(cs);
    free(cs);
    snprintf(sum, scap, "inbox: %d rows, %d unread", n, unread_total);
    return r;
}

// One conversation -> `list` card. Oldest-first is the platform's own order.
// `older` (0|1) picks the window — 0 = newest 6, 1 = the 6 BEFORE the
// newest 6, cut from the SAME limit-12 fetch (a wider fetch risks the silent
// MSG_BUF truncation documented above; depth-1 doubles reach at zero new
// network risk). Beyond message 12 the footer stays honest and the phone
// exists.
static esp_err_t render_thread(const char *json, char *sum, size_t scap, int older) {
    cJSON *root = cJSON_Parse(json);
    if (!root) { snprintf(sum, scap, "thread reply did not parse"); return ESP_FAIL; }
    cJSON *peer = cJSON_GetObjectItem(root, "peer");
    cJSON *msgs = cJSON_GetObjectItem(root, "messages");
    if (!cJSON_IsArray(msgs)) {
        cJSON_Delete(root);
        snprintf(sum, scap, "thread reply had no messages array");
        return ESP_FAIL;
    }
    const char *login = peer ? jstr(peer, "login") : "";
    const char *pname = peer ? jstr(peer, "name") : "";
    if (!*pname) pname = login;
    char them[32];
    snprintf(them, sizeof them, "%s", pname);
    for (char *p = them; *p; ++p) if (*p == ' ') { *p = 0; break; }  // first name

    cJSON *card = cJSON_CreateObject();
    cJSON_AddStringToObject(card, "type", "list");
    char cid[32];
    snprintf(cid, sizeof cid, "dm-th-%s", login);
    cid[23] = 0;  // s_card_id is 24 bytes; a longer id would be cut anyway
    cJSON_AddStringToObject(card, "card_id", cid);
    char title[64];
    snprintf(title, sizeof title, "%s", pname);
    clip_utf8(title, 40);
    cJSON_AddStringToObject(card, "title", title);

    cJSON *items = cJSON_AddArrayToObject(card, "items");
    const int total = cJSON_GetArraySize(msgs);
    const int page = 6;
    // Window: the newest `page`, or (older view) the `page` before those.
    const int avail = older ? (total - page < 0 ? 0 : total - page) : total;
    const int shown = avail < page ? avail : page;
    const int skip  = avail - shown;  // oldest messages to pass over
    int i = 0, added = 0;
    cJSON *m = NULL;
    cJSON_ArrayForEach(m, msgs) {
        const int idx = i++;
        if (idx < skip || idx >= skip + shown) continue;
        const char *dir = jstr(m, "direction");
        cJSON *cr = cJSON_GetObjectItem(m, "created");
        char clock[8] = "--:--";
        if (cJSON_IsNumber(cr)) {
            const time_t ts = (time_t)cr->valuedouble;
            struct tm tmv;
            // Suffix Z — tiny_time pins TZ=UTC0, so a bare HH:MM is
            // UTC wearing local clothes (4-5h wrong for the owner). Same
            // rule the glance cluster already states: an unlabeled
            // wrong-zone clock is a lie. Retires when the owner-tz question
            // (QUESTIONS.md) is answered and setenv'd.
            if (localtime_r(&ts, &tmv))
                snprintf(clock, sizeof clock, "%02d:%02dZ", tmv.tm_hour, tmv.tm_min);
        }
        char line[240];
        snprintf(line, sizeof line, "%s %s: %s", clock,
                 strcmp(dir, "sent") == 0 ? "me" : them, jstr(m, "body"));
        clip_utf8(line, 150);
        cJSON_AddItemToArray(items, cJSON_CreateString(line));
        ++added;
    }
    if (!added) cJSON_AddItemToArray(items, cJSON_CreateString("(no messages)"));
    char footer[128];
    // "loaded", not "shown": only ~2 of these messages fit the 258 px body box
    // at once (measured on glass — 834 px of content), and a footer that claims
    // six are visible when two are is the same class of lie as a status field
    // that misreports its own provenance. The rest are one swipe away.
    if (older)
        snprintf(footer, sizeof footer, "@%s - %d in thread, older %d loaded - swipe",
                 login, total, added);
    else
        snprintf(footer, sizeof footer, "@%s - %d in thread, newest %d loaded - swipe",
                 login, total, added);
    cJSON_AddStringToObject(card, "footer", footer);
    cJSON *btns = cJSON_AddArrayToObject(card, "buttons");
    cJSON *b0 = cJSON_CreateObject();
    // 56, not 40: this id IS the destination of whatever gets typed next, and
    // "m:c:" + a 39-char login is 43 bytes. A 40-byte buffer clipped it — and a
    // clipped address does not fail, it composes a reply TO SOMEBODY ELSE.
    // tiny_display.h sizes button ids at 48 for exactly this reason; the
    // producer had never been sized to match the contract it feeds.
    char rid[56];
    const int rn = snprintf(rid, sizeof rid, "m:c:%s", login);
    if (rn > 0 && (size_t)rn < sizeof rid) {
        cJSON_AddStringToObject(b0, "id", rid);
        cJSON_AddStringToObject(b0, "label", "Reply");
        cJSON_AddItemToArray(btns, b0);
    } else {
        // No button at all beats a button aimed at the wrong human. The thread
        // still renders and still scrolls; only the reply affordance is absent.
        ESP_LOGE(TAG, "login %d B too long for a reply id — omitting [Reply]",
                 (int)strlen(login));
        cJSON_Delete(b0);
    }
    cJSON *b1 = cJSON_CreateObject();
    cJSON_AddStringToObject(b1, "id", "m:inbox");
    cJSON_AddStringToObject(b1, "label", "Inbox");
    cJSON_AddItemToArray(btns, b1);
    // A path to messages older than the newest 6. [Older] re-renders
    // the 7-12 window from the same fetch ("m:o:<login>"); the older view
    // offers [Newer] back ("m:t:<login>"). One level deep by design — see the
    // window comment at render_thread. Both ids reuse the 56-byte producer
    // sizing and the omit-on-overflow rule from [Reply] above.
    {
        char nid[56];
        const int nn = snprintf(nid, sizeof nid, "m:%s:%s",
                                older ? "t" : "o", login);
        if (nn > 0 && (size_t)nn < sizeof nid && (older || total > page)) {
            cJSON *bo = cJSON_CreateObject();
            cJSON_AddStringToObject(bo, "id", nid);
            cJSON_AddStringToObject(bo, "label", older ? "Newer" : "Older");
            cJSON_AddItemToArray(btns, bo);
        }
    }
    cJSON *b2 = cJSON_CreateObject();
    cJSON_AddStringToObject(b2, "id", "home");
    cJSON_AddStringToObject(b2, "label", "Home");
    cJSON_AddItemToArray(btns, b2);

    char *cs = cJSON_PrintUnformatted(card);
    cJSON_Delete(card);
    cJSON_Delete(root);
    if (!cs) { snprintf(sum, scap, "thread card out of memory"); return ESP_ERR_NO_MEM; }
    esp_err_t r = tiny_display_render_card(cs);
    free(cs);
    // Land on the NEWEST message, the way every messaging app does. Rows are
    // chronological, so a fresh render shows the oldest of the batch — opening a
    // conversation and reading a message from last week is the wrong feeling.
    // The delta is clamped by the display layer, so "huge" means "to the end";
    // it costs one extra partial refresh and only when the body overflows.
    if (r == ESP_OK && tiny_display_can_scroll()) tiny_display_scroll(100000);
    snprintf(sum, scap, "thread @%s: newest %d of %d loaded", login, added, total);
    return r;
}

// The touch task's entry point. `what` is "inbox" or "t:<login>" — i.e. exactly
// the button id minus its "m:" prefix, so a row carries its own destination and
// nothing has to remember which card is on the glass.
extern "C" esp_err_t tiny_node_messages_open(const char *what) {
    if (!what) return ESP_ERR_INVALID_ARG;
    // "c:<login>" — compose. Pure UI, no network: hand off to the shell's
    // keyboard and let its "ok" call tiny_node_messages_send.
    if (strncmp(what, "c:", 2) == 0) return tiny_shell_compose(what + 2);
    if (!tiny_wifi_is_up()) return ESP_ERR_INVALID_STATE;
    char *buf = msg_buf_alloc();
    if (!buf) return ESP_ERR_NO_MEM;
    char sum[96] = "";
    esp_err_t r;
    int st;
    if (strncmp(what, "t:", 2) == 0 || strncmp(what, "o:", 2) == 0) {
        // "o:<login>" = the older window (messages 7-12), same fetch.
        const int older = (what[0] == 'o');
        st = messages_call("thread", what + 2, "12", NULL, buf, MSG_BUF_BYTES);
        r = (st == 200) ? render_thread(buf, sum, sizeof sum, older) : ESP_FAIL;
    } else {
        st = messages_call("inbox", NULL, "8", NULL, buf, MSG_BUF_BYTES);
        r = (st == 200) ? render_inbox(buf, sum, sizeof sum) : ESP_FAIL;
    }
    ESP_LOGI(TAG, "messages %s -> HTTP %d, %s (%s)", what, st,
             *sum ? sum : "no summary", esp_err_to_name(r));
    free(buf);
    return r;
}

// Send `body` to @login with the DEVICE token, then re-open the thread — the
// reply appearing in the conversation IS the receipt (no separate toast). On
// failure paint an honest text card: a swallowed 4xx after typing on e-ink at
// 2 keys/sec is the cruelest possible bug. Blocks on HTTPS + refresh: call
// off the touch task (the act worker satisfies this — kKey runs there).
extern "C" esp_err_t tiny_node_messages_send(const char *login,
                                             const char *body) {
    if (!login || !*login || !body || !*body) return ESP_ERR_INVALID_ARG;
    if (!tiny_wifi_is_up()) return ESP_ERR_INVALID_STATE;
    char *buf = msg_buf_alloc();
    if (!buf) return ESP_ERR_NO_MEM;
    const int st = messages_call("send", login, NULL, body, buf, MSG_BUF_BYTES);
    ESP_LOGI(TAG, "messages send @%s (%d B) -> HTTP %d", login,
             (int)strlen(body), st);
    if (st != 200) {
        // BUG-1 postmortem rule: the demo's dm-err card told the OWNER at the
        // glass, but nothing told the FEED — the failure's HTTP status was
        // unrecoverable an hour later. One event line makes the next such bug
        // a 30-second read instead of a forensic session. Body text stays off
        // the wire (it is a private draft); status + recipient + size suffice.
        char note[120];
        snprintf(note, sizeof note, "dm send FAILED: @%.48s HTTP %d (%d B body)",
                 login, st, (int)strlen(body));
        tiny_node_post_event("device_note", note);
    }
    free(buf);
    if (st == 200) {
        // 52: "t:" + a 39-char login is 41, which a 40-byte buffer clipped —
        // and this value re-opens a conversation BY NAME, so a clipped one
        // shows a different person's thread as the receipt for a message you
        // just sent. Refuse rather than reopen the wrong one.
        char t[52];
        const int tn = snprintf(t, sizeof t, "t:%s", login);
        if (tn > 0 && (size_t)tn < sizeof t) return tiny_node_messages_open(t);
        ESP_LOGE(TAG, "login %d B too long to re-open the thread after send",
                 (int)strlen(login));
        return ESP_OK;  // the send itself DID succeed; only the receipt is missing
    }
    // Built with cJSON, not snprintf. This card exists for exactly one moment —
    // the human typed on e-ink at 2 keys/sec and it did not arrive — so it is the
    // last card allowed to break. Hand-built it pasted the login in TWICE,
    // unescaped, into 288 bytes: a quote or a backslash in a login produced
    // invalid JSON and the renderer, correctly, drew nothing. A blank panel is
    // the one answer worse than "HTTP 400".
    cJSON *card = cJSON_CreateObject();
    if (!card) {
        tiny_display_render_card(
            "{\"type\":\"text\",\"card_id\":\"dm-err\",\"title\":\"send failed\","
            "\"body\":\"your text was not delivered - out of memory to say more\"}");
        return ESP_FAIL;
    }
    cJSON_AddStringToObject(card, "type", "text");
    cJSON_AddStringToObject(card, "card_id", "dm-err");
    cJSON_AddStringToObject(card, "title", "send failed");
    // `errbody`, not `body`: the parameter `body` (the user's draft) is alive
    // in this scope and shadowing it did not compile (-Werror=shadow) — nor
    // should it: the one thing this card must never do is confuse the error
    // text with the text that failed to send.
    char errbody[128];
    snprintf(errbody, sizeof errbody,
             "HTTP %d - your text was not delivered to @%.48s", st, login);
    cJSON_AddStringToObject(card, "body", errbody);  // cJSON escapes it
    cJSON *btns = cJSON_AddArrayToObject(card, "buttons");
    // 48 = the button-id contract in tiny_display.h. "m:t:" costs 4 of it, so a
    // 44-char login no longer fits even though the composer accepted it. Same
    // rule as everywhere else in this path: omit the button rather than offer one
    // aimed at whoever's name happens to be that prefix.
    char tid[48];
    const int tn = snprintf(tid, sizeof tid, "m:t:%s", login);
    if (tn > 0 && (size_t)tn < sizeof tid) {
        cJSON *b = cJSON_CreateObject();
        cJSON_AddStringToObject(b, "id", tid);
        cJSON_AddStringToObject(b, "label", "Thread");
        cJSON_AddItemToArray(btns, b);
    } else {
        ESP_LOGE(TAG, "login %d B too long for a 'm:t:' button id — send-failure "
                      "card ships without a Thread button rather than a wrong one",
                 (int)strlen(login));
    }
    cJSON *hb = cJSON_CreateObject();
    cJSON_AddStringToObject(hb, "id", "home");
    cJSON_AddStringToObject(hb, "label", "Home");
    cJSON_AddItemToArray(btns, hb);
    char *cs = cJSON_PrintUnformatted(card);
    cJSON_Delete(card);
    if (cs) {
        tiny_display_render_card(cs);
        free(cs);
    } else {
        // Print failed: say the one thing that matters, with no interpolation.
        tiny_display_render_card(
            "{\"type\":\"text\",\"card_id\":\"dm-err\",\"title\":\"send failed\","
            "\"body\":\"your text was not delivered - see the console log\"}");
    }
    return ESP_FAIL;
}

// ---------- dispatch ----------
static void dispatch_inner(const char *envelope_id, const char *prompt) {
    // config bodies carry Wi-Fi keys: the entry log never prints them.
    ESP_LOGI(TAG, "envelope %s: %.80s", envelope_id,
             strncmp(prompt, "config ", 7) == 0 ? "config <redacted>" : prompt);
    // command grammar: "<verb> [json]" — verbs: render_ui, status, say
    // Any glass-claiming verb ends a running frame stream first — the
    // same "new card outranks the stream" law the typer already obeys.
    if (tiny_stream_playing() &&
        (strncmp(prompt, "render_ui ", 10) == 0 ||
         strncmp(prompt, "say ", 4) == 0 || strncmp(prompt, "page ", 5) == 0 ||
         strncmp(prompt, "ask ", 4) == 0))
        tiny_stream_stop();

    if (strncmp(prompt, "render_ui ", 10) == 0) {
        const char *spec = prompt + 10;
        esp_err_t r = tiny_display_render_card(spec);
        if (r == ESP_OK) {
            // Echo the card_id the panel actually committed. The dashboard's
            // clickable mirror can then claim "this IS what is on the glass"
            // instead of "this is the last card I sent" (docs/QUESTIONS.md).
            char out[64];
            snprintf(out, sizeof out, "rendered card_id=%s",
                     tiny_display_current_card_id());
            reply(envelope_id, out);
        } else {
            reply(envelope_id, esp_err_to_name(r));
        }
    } else if (strncmp(prompt, "config ", 7) == 0) {
        // config {"networks":[{"ssid":..,"password"|"key":..}|{"ssid":..,
        // "forget":true}, ...]} — the dashboard settings API contract.
        // ONLY the networks array is accepted: the same NVS blob holds
        // device_id/token, and a merge that could rewrite identity from a
        // relay envelope would let one malformed config brick enrollment.
        // Keys are never echoed — reply carries counts, not contents.
        cJSON *in = cJSON_Parse(prompt + 7);
        if (!in) {
            reply(envelope_id, "{\"error\":\"config: body must be json\"}");
        } else {
            cJSON *nets = cJSON_DetachItemFromObject(in, "networks");
            cJSON_Delete(in);
            if (!cJSON_IsArray(nets)) {
                if (nets) cJSON_Delete(nets);
                reply(envelope_id,
                      "{\"error\":\"config: only {\\\"networks\\\":[...]} is "
                      "accepted (identity fields refused by design)\"}");
            } else {
                const int n_in = cJSON_GetArraySize(nets);
                cJSON *wrap = cJSON_CreateObject();
                cJSON_AddItemToObject(wrap, "networks", nets);
                char *sj = cJSON_PrintUnformatted(wrap);
                cJSON_Delete(wrap);
                esp_err_t r = sj ? tiny_config_merge_json(sj) : ESP_ERR_NO_MEM;
                if (sj) { memset(sj, 0, strlen(sj)); free(sj); }
                tiny_config_t cfg;
                const int total =
                    tiny_config_load(&cfg) == ESP_OK ? cfg.network_count : -1;
                char out[224];
                snprintf(out, sizeof out,
                         "{\"config\":\"%s\",\"networks_in\":%d,"
                         "\"networks_saved\":%d,\"note\":\"keys never echoed; "
                         "list is walked on boot and by the 30s re-roam task "
                         "whenever the link drops\"}",
                         r == ESP_OK ? "merged" : esp_err_to_name(r), n_in,
                         total);
                reply(envelope_id, out);
            }
        }
    } else if (strncmp(prompt, "sensors", 7) == 0) {
        // sensors [card] — every onboard sensor, read live over I2C1. Bare verb
        // replies JSON (the dashboard's Sensors card); `sensors card` also
        // paints it on the glass. No network either way.
        // 1536, not 768: at 0.14.11 the real reply is ~810 bytes (13 battery
        // keys, 3 sensor groups, an errors[] array whose entries are free text)
        // and 768 cut it mid-token. Sized for roughly double today's content
        // because errors[] grows when hardware misbehaves — the moment the reply
        // matters most is the moment it is longest. Still far under the 4 KB
        // HTTP response sink and the 8 KB relay cap.
        EXT_RAM_BSS_ATTR static char sj[1536];  // PSRAM .bss
        esp_err_t r = tiny_sensors_json(sj, sizeof sj);
        // Only overwrite when the writer left nothing usable: on a size refusal
        // it puts an informative JSON error in sj (need_bytes/buffer_bytes) and
        // clobbering that with just the errno name would throw away the one
        // number needed to fix it.
        if (r != ESP_OK && sj[0] != '{')
            snprintf(sj, sizeof sj, "{\"error\":\"%s\"}", esp_err_to_name(r));
        if (strstr(prompt, "card")) {
            const esp_err_t cr = tiny_sensors_render_card();
            char out[sizeof sj + 32];
            snprintf(out, sizeof out, "%s [card %s]", sj,
                     cr == ESP_OK ? "rendered" : esp_err_to_name(cr));
            reply(envelope_id, out);
        } else {
            reply(envelope_id, sj);
        }
    } else if (strncmp(prompt, "tap ", 4) == 0) {
        // tap <x> <y> — synthetic tap in PANEL coordinates (the screenshot's own
        // coordinate system), through the same resolver a finger uses. This is
        // how a rotation gets proven without a human hand: tap where the
        // screenshot shows the button and see which button answers.
        int x = -1, y = -1;
        if (sscanf(prompt + 4, "%d %d", &x, &y) != 2) {
            reply(envelope_id, "{\"error\":\"usage: tap <x> <y>\"}");
        } else {
            // Route-time receipt — same contract as swipe: wait
            // for the act task to finish the route, then report display-time
            // card_id. A tap can trigger a blocking route (BLE scan tap ≈ 8s),
            // so the wait is 12s; "routed":false means still running, not lost.
            const uint32_t seq0 = tiny_touch_route_seq();
            const esp_err_t r = tiny_touch_inject_tap(x, y);
            bool routed = false;
            if (r == ESP_OK) {
                for (int i = 0; i < 120; ++i) {
                    if ((routed = tiny_touch_route_seq() != seq0)) break;
                    // 300ms grace, then: act idle + seq unchanged = the tap
                    // resolved to NOTHING (no button there) — nothing to wait
                    // for. Keeps miss-probing fast instead of a 12s stall.
                    if (i >= 3 && tiny_touch_act_busy_ms() == 0 &&
                        tiny_touch_route_seq() == seq0) break;
                    vTaskDelay(pdMS_TO_TICKS(100));
                }
            }
            const char *route_v =
                routed ? esp_err_to_name(tiny_touch_route_result()) : "none";
            // Name the routed region — a receipt reader must never
            // need three glass ops to learn WHAT a tap resolved to.
            char rid[48] = "", rlab[32] = "";
            if (routed) tiny_touch_route_region(rid, sizeof rid,
                                                rlab, sizeof rlab);
            wire_fold_bank(rlab);  // BUG-2: bank bytes never leave the device
            char out[480];  // inert-hit sentence pushed worst-case past 400
            snprintf(out, sizeof out,
                     "{\"tap\":[%d,%d],\"rotation\":%d,\"accepted\":\"%s\","
                     "\"routed\":%s,\"route\":\"%s\",\"region\":\"%s\","
                     "\"region_label\":\"%s\",\"card_id\":\"%s\","
                     "\"summary\":\"tapped %d,%d "
                     "at %d deg -> %s, %s; card_id is display-time\"}",
                     x, y, tiny_display_rotation(), esp_err_to_name(r),
                     routed ? "true" : "false", route_v, rid, rlab,
                     tiny_display_current_card_id(),
                     x, y, tiny_display_rotation(), esp_err_to_name(r),
                     r != ESP_OK ? "refused at resolver"
                     // Inert hit ≠ miss (probe nit, 2026-08-26): a noop region
                     // routes through the act task doing nothing — the receipt
                     // names the region and says "inert", because "nothing
                     // under tap" once cost two contradicting probe rounds.
                     : routed && !strcmp(rid, "noop")
                                 ? "hit an inert region (noop id) - drawn, "
                                   "tappable, no local action"
                     : routed    ? "route completed"
                     : tiny_touch_act_busy_ms() > 0
                                 ? "route still running at reply time"
                                 : "no route dispatched (nothing under tap)");
            reply(envelope_id, out);
        }
    } else if (strncmp(prompt, "swipe ", 6) == 0) {
        // swipe <x0> <y0> <x1> <y1> — synthetic swipe in PANEL coordinates,
        // through the same release classifier a finger uses (D-UX0). This is
        // how the gesture grammar in UX_SPEC §2 gets verified over the relay:
        // inject the vector, read the receipt, screenshot the consequence.
        // INVALID_SIZE = travel within tap slop, refused rather than silently
        // downgraded to a tap.
        int x0 = -1, y0 = -1, x1 = -1, y1 = -1;
        if (sscanf(prompt + 6, "%d %d %d %d", &x0, &y0, &x1, &y1) != 4) {
            reply(envelope_id,
                  "{\"error\":\"usage: swipe <x0> <y0> <x1> <y1> (panel coords, "
                  "travel must exceed 24 px tap slop)\"}");
        } else {
            // Route-time receipts: a swipe's consequence is
            // computed on the act task AFTER this call returns, so a receipt
            // written at inject time was reporting a stale card_id and calling
            // an accepted-but-dead-end gesture "ESP_OK". Now: capture the route
            // counter, inject, wait for the route to FINISH (6s covers a full
            // e-ink render; BLE-tap routes are no longer reachable by swipe),
            // then report both stages by name.
            const uint32_t seq0 = tiny_touch_route_seq();
            const esp_err_t r = tiny_touch_inject_swipe(x0, y0, x1, y1);
            bool routed = false;
            if (r == ESP_OK) {  // accepted by the classifier — await the route
                for (int i = 0; i < 60; ++i) {
                    if ((routed = tiny_touch_route_seq() != seq0)) break;
                    if (i >= 3 && tiny_touch_act_busy_ms() == 0 &&
                        tiny_touch_route_seq() == seq0) break;  // nothing queued
                    vTaskDelay(pdMS_TO_TICKS(100));
                }
            }
            // R-6b: routed:true only says the route RAN — thread the route's
            // own verdict so a dead-end (NOT_ALLOWED) and a nav (OK) stop
            // returning identical receipts.
            const char *route_v =
                routed ? esp_err_to_name(tiny_touch_route_result()) : "none";
            // Postmortem: name the outcome. "route completed" cost two
            // debugging passes because a correct clamp, a dead rail and a wrong
            // axis all read identically. The router's own words disambiguate.
            const char *gest = routed ? tiny_display_last_gesture() : "none";
            char out[480];
            snprintf(out, sizeof out,
                     "{\"swipe\":[%d,%d,%d,%d],\"rotation\":%d,\"accepted\":\"%s\","
                     "\"routed\":%s,\"route\":\"%s\",\"gesture\":\"%s\","
                     "\"card_id\":\"%s\","
                     "\"summary\":\"swiped "
                     "(%d,%d)->(%d,%d) at %d deg -> %s, %s: %s; card_id is "
                     "display-time\"}",
                     x0, y0, x1, y1, tiny_display_rotation(), esp_err_to_name(r),
                     routed ? "true" : "false", route_v, gest,
                     tiny_display_current_card_id(),
                     x0, y0, x1, y1, tiny_display_rotation(), esp_err_to_name(r),
                     r != ESP_OK ? "refused at classifier"
                     : routed    ? "route completed"
                     : tiny_touch_act_busy_ms() > 0
                                 ? "route still running at reply time"
                                 : "no route dispatched",
                     gest);
            reply(envelope_id, out);
        }
    } else if (strncmp(prompt, "messages", 8) == 0) {
        // messages                -> inbox card on the glass
        // messages unread         -> badge counts only, nothing rendered
        // messages thread <login> -> that conversation on the glass
        // The device reads these with its OWN token (option B), so this works
        // with no owner-side process running anywhere.
        const char *a = prompt + 8;
        while (*a == ' ') ++a;
        char out[512];
        if (strncmp(a, "unread", 6) == 0) {
            char *buf = msg_buf_alloc();
            if (!buf) { reply(envelope_id, "{\"error\":\"out of memory\"}"); }
            else {
                int st = messages_call("unread", NULL, NULL, NULL, buf, MSG_BUF_BYTES);
                const bool body_ok = (st == 200 && buf[0] == '{');
                // `buf` is the 12 KB PSRAM response sink and its contents are
                // the BACKEND's choice, not ours. Measured on the wire today:
                // {"unread":0,"from":[]} — but `from` is a list of logins that
                // grows one entry per unread sender, so this reply pasted an
                // unbounded network string into 512 bytes and discarded
                // snprintf's return. Past ~440 B that is invalid JSON carrying
                // "http":200 — the sensors defect again, this time remotely
                // triggered without a firmware change.
                EXT_RAM_BSS_ATTR static char ur[1024];  // PSRAM .bss
                const int n = snprintf(ur, sizeof ur,
                         "{\"op\":\"unread\",\"http\":%d,\"body\":%s,"
                         "\"summary\":\"unread badge via device token\"}",
                         st, body_ok ? buf : "null");
                if (n < 0 || (size_t)n >= sizeof ur) {
                    // Too many senders to echo verbatim: answer with the two
                    // numbers a badge actually needs, PARSED rather than
                    // pasted, and say the body was dropped. A small true answer
                    // beats a large broken one; `null` means "could not read
                    // it", never zero.
                    char un[16] = "null", fc[16] = "null";
                    cJSON *b2 = body_ok ? cJSON_Parse(buf) : NULL;
                    if (b2) {
                        cJSON *u = cJSON_GetObjectItem(b2, "unread");
                        if (cJSON_IsNumber(u))
                            snprintf(un, sizeof un, "%d", u->valueint);
                        cJSON *f = cJSON_GetObjectItem(b2, "from");
                        if (cJSON_IsArray(f))
                            snprintf(fc, sizeof fc, "%d", cJSON_GetArraySize(f));
                        cJSON_Delete(b2);
                    }
                    ESP_LOGW(TAG, "unread body %d B > %u reply buffer — echoing "
                                  "counts only", n, (unsigned)sizeof ur);
                    snprintf(ur, sizeof ur,
                             "{\"op\":\"unread\",\"http\":%d,\"unread\":%s,"
                             "\"from_count\":%s,\"body_bytes\":%d,"
                             "\"body_dropped\":true,\"summary\":\"unread badge "
                             "(body too large to echo verbatim)\"}",
                             st, un, fc, n);
                }
                free(buf);
                reply(envelope_id, ur);
            }
        } else {
            char what[64];
            if (strncmp(a, "thread", 6) == 0) {
                const char *who = a + 6;
                while (*who == ' ') ++who;
                snprintf(what, sizeof what, "t:%s", who);
            } else {
                snprintf(what, sizeof what, "inbox");
            }
            esp_err_t r = tiny_node_messages_open(what);
            snprintf(out, sizeof out,
                     "{\"op\":\"%s\",\"result\":\"%s\",\"card_id\":\"%s\","
                     "\"summary\":\"messages %s -> %s\"}",
                     what, esp_err_to_name(r), tiny_display_current_card_id(),
                     what, esp_err_to_name(r));
            reply(envelope_id, out);
        }
    } else if (strncmp(prompt, "scroll", 6) == 0) {
        // scroll            -> report offset/content_h/view_h (probe-readable)
        // scroll down|up    -> one page step (2/3 view), the buttons' twin
        // scroll top        -> back to the start
        // scroll <±px>      -> exact pixels — probe-precise
        // Restored 00:20Z: 474aa51's HEAD repair dropped this branch while the
        // heartbeat kept advertising it (docs-loop caught the gap).
        const char *a = prompt + 6;
        while (*a == ' ') ++a;
        esp_err_t r = ESP_OK;
        if (!strncmp(a, "down", 4))     r = tiny_display_scroll_page(+1);
        else if (!strncmp(a, "up", 2))  r = tiny_display_scroll_page(-1);
        else if (!strncmp(a, "top", 3)) r = tiny_display_scroll(-(1 << 28));
        else if (*a)                    r = tiny_display_scroll(atoi(a));
        int off = 0, content = 0, view = 0;
        tiny_display_scroll_state(&off, &content, &view);
        char out[256];
        snprintf(out, sizeof out,
                 "{\"offset\":%d,\"content_h\":%d,\"view_h\":%d,"
                 "\"scrollable\":%s,\"card_id\":\"%s\",\"result\":\"%s\","
                 "\"summary\":\"scroll %d/%d px of %d content (%s)\"}",
                 off, content, view,
                 tiny_display_can_scroll() ? "true" : "false",
                 tiny_display_current_card_id(), esp_err_to_name(r),
                 off, content > view ? content - view : 0, content,
                 esp_err_to_name(r));
        reply(envelope_id, out);
    } else if (strncmp(prompt, "glance", 6) == 0) {
        // §12: refresh the always-drawn title-bar cluster (time, battery,
        // wifi, unread, lock) via the PARTIAL path — the acceptance's
        // "no full-panel flash" is the whole point of the verb.
        const esp_err_t g = tiny_display_glance();
        if (g == ESP_OK) {
            char out[192];
            BatteryReading batt = {};
            sticky_battery_read(batt);
            snprintf(out, sizeof out,
                     "{\"refresh\":\"partial\",\"battery_pct\":%d,"
                     "\"wifi\":%s,\"unread\":%d,\"locked\":%s}",
                     batt.percent, tiny_wifi_is_up() ? "true" : "false",
                     tiny_node_unread(), tiny_lock_is_locked() ? "true" : "false");
            reply(envelope_id, out);
        } else {
            reply(envelope_id, "glance failed: no card on the glass yet");
        }
    } else if (strncmp(prompt, "rotate", 6) == 0) {
        // rotate                -> report
        // rotate 0|90|180|270    -> set, and hold it (manual)
        // rotate auto            -> hand the decision back to gravity
        // The layout turns; the framebuffer, screenshot and touch coordinates
        // stay in panel space, so a tap aimed at a screenshot pixel still lands.
        const char *arg = prompt + 6;
        while (*arg == ' ') ++arg;
        esp_err_t r = ESP_OK;
        if (strncmp(arg, "test", 4) == 0) {
            // The P0 calibration bug's evidence gate: an arrow that must point
            // at the SKY in all four orientations, plus one button so touch
            // can be proven to rotate in lockstep (an upside-down fix that
            // leaves taps mirrored trades one bug for a worse one). The arrow
            // is drawn by the normal card path — if orientation is right, the
            // title bar side is the sky side and the arrow agrees with it.
            r = tiny_display_render_card(
                "{\"type\":\"text\",\"card_id\":\"rotate-test\","
                "\"title\":\"SKY IS THIS WAY\","
                "\"body\":\"/\\\\  the title bar and this arrow point UP. "
                "Turn me all four ways - I must keep pointing at the sky. "
                "Then tap the button: it must land where your finger is.\","
                "\"buttons\":[{\"id\":\"rot-ok\",\"label\":\"lands under my finger\"}]}");
        } else if (strncmp(arg, "auto", 4) == 0) {
            tiny_orient_set_auto(true);
        } else if (strncmp(arg, "report", 6) == 0) {
            // Explicit no-op: `report` is advertised in the help string below, and
            // it must MEAN report. It used to fall through to the setter, where
            // atoi("report") is 0 — so asking the device which way it was facing
            // turned auto-rotate OFF and pinned the panel to 0 degrees. Measured
            // live on 0.14.23 at 02:0xZ, seconds after the owner confirmed the P0
            // rotation fix: `rotate report` answered "rotation":0,"mode":"manual".
            // A read that writes is the same class of lie as a status field that
            // reports something it did not measure.
        } else if (*arg) {
            // Only a real degree value may change state. atoi() cannot say "that
            // was not a number" — it answers 0 for "report", "debug", "banana"
            // and "0" alike — so parse strictly and refuse everything else
            // instead of silently obeying a word as if it were an angle.
            const bool numeric = (arg[0] >= '0' && arg[0] <= '9');
            const int deg = numeric ? atoi(arg) : -1;
            if (deg == 0 || deg == 90 || deg == 180 || deg == 270) {
                tiny_orient_set_auto(false);   // a human's choice outranks gravity
                r = tiny_display_set_rotation(deg);
            } else {
                // Refuse loudly and CHANGE NOTHING. The reply still carries the
                // full state below, so an unknown argument now behaves exactly
                // like the report the caller probably meant.
                ESP_LOGE(TAG, "rotate: '%s' is not 0|90|180|270|auto|report|test "
                              "— ignoring it, state unchanged", arg);
                r = ESP_ERR_INVALID_ARG;
            }
        }
        char dbg[192];
        tiny_orient_debug_json(dbg, sizeof dbg);
        char out[480];
        snprintf(out, sizeof out,
                 "{\"rotation\":%d,\"mode\":\"%s\",\"imu_last\":\"%s\","
                 "\"imu_last_deg\":%d,\"result\":\"%s\",\"imu\":%s,"
                 "\"summary\":\"rotation %d deg (%s), imu says %s\"}",
                 tiny_display_rotation(), tiny_orient_auto() ? "auto" : "manual",
                 tiny_orient_last_name(), tiny_orient_last_degrees(),
                 esp_err_to_name(r), dbg, tiny_display_rotation(),
                 tiny_orient_auto() ? "auto" : "manual", tiny_orient_last_name());
        reply(envelope_id, out);
    } else if (strncmp(prompt, "status", 6) == 0) {
        // Machine-readable: the dashboard parses these fields directly. The
        // `summary` field inside carries the sentence a human wants to read, so
        // one verb serves both readers instead of two that can disagree.
        // 1024, matching tiny_shell.cpp's settings-page caller: 640 left ~111
        // bytes of headroom (measured worst case ~529 B with a 32-char SSID and
        // a full summary) and two callers of the same writer disagreeing on the
        // buffer size means one of them breaks first, quietly.
        EXT_RAM_BSS_ATTR static char st[1024];  // PSRAM .bss
        // The writer now REFUSES rather than truncating, and its refusal is
        // parseable JSON naming the byte count it needed — so do not clobber it
        // with a generic sentence. Only substitute when nothing usable came back.
        if (tiny_node_status_json(st, sizeof st) != ESP_OK && st[0] != '{')
            strlcpy(st, "{\"fw\":\"" TINY_FW_VERSION "\",\"error\":\"status build failed\"}",
                    sizeof st);
        reply(envelope_id, st);
    } else if (strncmp(prompt, "sd", 2) == 0 &&
               (prompt[2] == 0 || prompt[2] == ' ')) {
        // sd [status|df|ls [path]] — the card's own verb. status/df share one
        // shape with the `status` verb's sd object so no two readers disagree.
        const char *arg = prompt[2] == ' ' ? prompt + 3 : "";
        while (*arg == ' ') arg++;
        if (strncmp(arg, "format", 6) == 0) {
            // DESTRUCTIVE — demands the literal confirmation word. Exists
            // because factory 128GB SDXC = exFAT, unreadable by FATFS.
            if (strcmp(arg, "format yes") != 0) {
                reply(envelope_id, "{\"error\":\"confirmation required\","
                      "\"summary\":\"sd format WIPES the card (FAT32). Send "
                      "exactly: sd format yes\"}");
            } else {
                const esp_err_t r = sticky_sdcard_format();
                uint64_t t = 0, f = 0;
                if (r == ESP_OK) sticky_sdcard_info(&t, &f);
                char out[192];
                snprintf(out, sizeof out,
                         "{\"formatted\":%s,\"result\":\"%s\","
                         "\"free_gb\":%.1f,\"summary\":\"format %s\"}",
                         r == ESP_OK ? "true" : "false", esp_err_to_name(r),
                         (double)f / 1e9,
                         r == ESP_OK ? "done: FAT32, mounted" : "failed");
                reply(envelope_id, out);
            }
        } else if (strcmp(arg, "probe") == 0) {
            // Non-destructive receipt: write a small file, read it back,
            // compare, unlink. Proves the card end-to-end (SPI bus + FATFS +
            // VFS), which mount alone does not.
            if (sticky_sdcard_ensure() != ESP_OK) {
                reply(envelope_id, "{\"error\":\"no card\",\"summary\":\"no sd "
                      "card present (or mount failed)\"}");
            } else {
                static const char *kPath = STICKY_SD_MOUNT_POINT "/.probe.txt";
                char pay[96];
                snprintf(pay, sizeof pay, "sticky sd probe uptime=%lu heap=%u",
                         (unsigned long)(esp_timer_get_time() / 1000000ULL),
                         (unsigned)esp_get_free_heap_size());
                bool wr_ok = false, rd_ok = false, match = false;
                FILE *f = fopen(kPath, "w");
                if (f) {
                    wr_ok = fputs(pay, f) >= 0;
                    fclose(f);
                }
                char back[96] = {0};
                if (wr_ok && (f = fopen(kPath, "r")) != NULL) {
                    rd_ok = fgets(back, sizeof back, f) != NULL;
                    fclose(f);
                    match = rd_ok && strcmp(back, pay) == 0;
                }
                unlink(kPath);
                char out[224];
                snprintf(out, sizeof out,
                         "{\"write\":%s,\"read\":%s,\"match\":%s,"
                         "\"bytes\":%u,\"summary\":\"sd probe %s\"}",
                         wr_ok ? "true" : "false", rd_ok ? "true" : "false",
                         match ? "true" : "false", (unsigned)strlen(pay),
                         match ? "OK: write+read-back verified"
                               : "FAILED");
                reply(envelope_id, out);
            }
        } else if (strncmp(arg, "ls", 2) == 0) {
            const char *sub = arg[2] == ' ' ? arg + 3 : "";
            while (*sub == ' ') sub++;
            char dirpath[192];
            if (sub[0] == '/' ) snprintf(dirpath, sizeof dirpath, "%s", sub);
            else if (sub[0])    snprintf(dirpath, sizeof dirpath,
                                         STICKY_SD_MOUNT_POINT "/%s", sub);
            else                snprintf(dirpath, sizeof dirpath,
                                         STICKY_SD_MOUNT_POINT);
            if (sticky_sdcard_ensure() != ESP_OK) {
                reply(envelope_id, "{\"error\":\"no card\",\"summary\":\"no sd "
                      "card present (or mount failed)\"}");
            } else {
                DIR *d = opendir(dirpath);
                if (!d) {
                    char out[256];
                    snprintf(out, sizeof out,
                             "{\"error\":\"opendir failed\",\"path\":\"%s\","
                             "\"errno\":%d}", dirpath, errno);
                    reply(envelope_id, out);
                } else {
                    cJSON *root = cJSON_CreateObject();
                    cJSON_AddStringToObject(root, "path", dirpath);
                    cJSON *ents = cJSON_AddArrayToObject(root, "entries");
                    struct dirent *de;
                    int n = 0;
                    while ((de = readdir(d)) != NULL && n < 64) {
                        cJSON *e = cJSON_CreateObject();
                        cJSON_AddStringToObject(e, "name", de->d_name);
                        if (de->d_type == DT_DIR) {
                            cJSON_AddStringToObject(e, "type", "dir");
                        } else {
                            char fp[448];
                            struct stat st_f;
                            snprintf(fp, sizeof fp, "%.191s/%.250s", dirpath,
                                     de->d_name);
                            cJSON_AddStringToObject(e, "type", "file");
                            if (stat(fp, &st_f) == 0)
                                cJSON_AddNumberToObject(e, "size",
                                                        (double)st_f.st_size);
                        }
                        cJSON_AddItemToArray(ents, e);
                        n++;
                    }
                    closedir(d);
                    cJSON_AddNumberToObject(root, "count", n);
                    char *cs = cJSON_PrintUnformatted(root);
                    cJSON_Delete(root);
                    reply(envelope_id, cs ? cs : "{\"error\":\"oom\"}");
                    if (cs) free(cs);
                }
            }
        } else {  // "", "status", "df"
            sticky_sdcard_ensure();  // reconcile hot-plug before reporting
            cJSON *root = cJSON_CreateObject();
            const bool present = sticky_sdcard_present();
            const bool mounted = sticky_sdcard_mounted();
            cJSON_AddBoolToObject(root, "present", present);
            cJSON_AddBoolToObject(root, "mounted", mounted);
            uint64_t sd_total = 0, sd_free = 0;
            char sum[128];
            if (mounted && sticky_sdcard_info(&sd_total, &sd_free) == ESP_OK) {
                const double tg = (double)(uint64_t)((sd_total / 1e9) * 10 + 0.5) / 10.0;
                const double fg = (double)(uint64_t)((sd_free / 1e9) * 10 + 0.5) / 10.0;
                cJSON_AddNumberToObject(root, "size_gb", tg);
                cJSON_AddNumberToObject(root, "free_gb", fg);
                snprintf(sum, sizeof sum,
                         "sd mounted: %.1f GB free of %.1f GB", fg, tg);
            } else {
                cJSON_AddNullToObject(root, "size_gb");
                cJSON_AddNullToObject(root, "free_gb");
                strlcpy(sum, present ? "card present but not mounted (exFAT "
                                       "factory card? -> sd format yes)"
                                     : "no sd card in the slot", sizeof sum);
            }
            cJSON_AddStringToObject(root, "summary", sum);
            char *cs = cJSON_PrintUnformatted(root);
            cJSON_Delete(root);
            reply(envelope_id, cs ? cs : "{\"error\":\"oom\"}");
            if (cs) free(cs);
        }
    } else if (strncmp(prompt, "agent", 5) == 0 &&
               (prompt[5] == 0 || prompt[5] == ' ')) {
        // agent                — who answers the asks now
        // agent <slug>         — switch (persists across reboot, NVS)
        // agent clear          — back to the owner's own tiny
        // agent list           — the roster, active one marked
        // agent add/rm <slug>  — edit the roster (universe card rows)
        // THE UNIVERSE ON THE GLASS (grammar v11): the slug rides every
        // subsequent ask as tiny:"<slug>" (backend 61b404f0 resolves
        // public/private/unknown/priced and refuses in honest sentences).
        const char *a = prompt + 5;
        while (*a == ' ') ++a;
        char out[512];
        if (!*a || strcmp(a, "list") == 0) {
            char roster[TINY_AGENT_ROSTER_MAX][TINY_AGENT_SLUG_MAX + 1];
            const int n = tiny_agent_roster(roster, TINY_AGENT_ROSTER_MAX);
            const char *cur = tiny_agent_current();
            cJSON *o = cJSON_CreateObject();
            cJSON_AddStringToObject(o, "active", cur);
            cJSON *arr = cJSON_AddArrayToObject(o, "roster");
            for (int i = 0; i < n; ++i)
                cJSON_AddItemToArray(arr, cJSON_CreateString(roster[i]));
            char sum[160];
            snprintf(sum, sizeof sum, "asks go to %s%s (%d in roster)",
                     cur[0] ? "@" : "", cur[0] ? cur : "your own tiny", n);
            cJSON_AddStringToObject(o, "summary", sum);
            char *os = cJSON_PrintUnformatted(o);
            cJSON_Delete(o);
            reply(envelope_id, os ? os : "{\"error\":\"oom\"}");
            if (os) free(os);
            return;
        }
        if (strncmp(a, "add ", 4) == 0 || strncmp(a, "rm ", 3) == 0) {
            const bool add = a[0] == 'a';
            const char *slug = a + (add ? 4 : 3);
            esp_err_t r = add ? tiny_agent_roster_add(slug)
                              : tiny_agent_roster_remove(slug);
            snprintf(out, sizeof out, "roster %s %.64s -> %s",
                     add ? "add" : "rm", slug, esp_err_to_name(r));
            reply(envelope_id, out);
            return;
        }
        // switch (or clear — tiny_agent_set treats "clear" as "")
        esp_err_t r = tiny_agent_set(a);
        if (r == ESP_OK) {
            const char *cur = tiny_agent_current();
            snprintf(out, sizeof out,
                     "asks now go to %s%s (persists across reboot)%s",
                     cur[0] ? "@" : "", cur[0] ? cur : "your own tiny",
                     cur[0] ? " - an unknown/private slug will answer with "
                              "the backend's refusal sentence" : "");
            // The glass must say who is answering (owner rule: never let a
            // persona's words read as your own tiny's) — repaint home so
            // the @slug badge appears/disappears with the switch.
            tiny_shell_home();
        } else if (r == ESP_ERR_INVALID_SIZE)
            snprintf(out, sizeof out, "agent: slug too long (max %d chars)",
                     TINY_AGENT_SLUG_MAX);
        else
            snprintf(out, sizeof out, "agent: bad slug (%s) - lowercase "
                     "slug like \"tiny\", no spaces/quotes", esp_err_to_name(r));
        reply(envelope_id, out);
    } else if (strncmp(prompt, "say ", 4) == 0) {
        // say <text> — put words on the fridge, no agent turn, no network wait.
        // Advertised in heartbeat capabilities since M3; it finally exists.
        const char *text = prompt + 4;
        cJSON *c = cJSON_CreateObject();
        cJSON_AddStringToObject(c, "type", "text");
        cJSON_AddStringToObject(c, "title", "tiny");
        cJSON_AddStringToObject(c, "card_id", "say");
        cJSON_AddStringToObject(c, "body", text);
        char *cs = cJSON_PrintUnformatted(c);
        cJSON_Delete(c);
        esp_err_t r = cs ? tiny_display_render_card(cs) : ESP_ERR_NO_MEM;
        if (cs) free(cs);
        if (r == ESP_OK) tiny_audio_chime("ack");  // a new card deserves a sound
        reply(envelope_id, r == ESP_OK ? "said (card on the panel)"
                                       : esp_err_to_name(r));
    } else if (strncmp(prompt, "lock", 4) == 0 &&
               (prompt[4] == 0 || prompt[4] == ' ')) {
        // §11 manual entry (and the designer's acceptance-chain test verb).
        tiny_lock_set(true, "relay lock verb");
        reply(envelope_id, "{\"locked\":true,\"summary\":\"pocket lockout "
              "engaged: touch+buttons+mic dead, relay answers, unlock = "
              "hold UP+DOWN 1s or unlock verb\"}");
    } else if (strncmp(prompt, "unlock", 6) == 0 &&
               (prompt[6] == 0 || prompt[6] == ' ')) {
        tiny_lock_set(false, "relay unlock verb");
        reply(envelope_id, "{\"locked\":false,\"summary\":\"unlocked: "
              "inputs live, last card re-rendered\"}");
    } else if (strncmp(prompt, "ask ", 4) == 0 || strncmp(prompt, "voice", 5) == 0) {
        // §11: a remote-triggered recording from a pocket is the same privacy
        // incident with extra steps. Text asks carry no microphone — allowed.
        if (strncmp(prompt, "voice", 5) == 0 && tiny_lock_is_locked()) {
            reply(envelope_id, "voice refused: device is pocket-locked (S11) "
                  "- the mic stays dead until the owner unlocks (UP+DOWN 1s) "
                  "or sends unlock");
            return;
        }
        // ask <text>  — owner-scoped agent turn via /api/devices/ask.
        // voice [sec] — record PDM mic, upload WAV, then ask with audioUrl.
        int secs = 0;
        const char *text = NULL;
        if (strncmp(prompt, "voice", 5) == 0) {
            secs = atoi(prompt + 5);
            if (secs <= 0) secs = 5;
        } else {
            text = prompt + 4;
        }
        char out[512];
        tiny_node_do_ask(text, secs, out, sizeof out);
        reply(envelope_id, out);
    } else if (strncmp(prompt, "screenshot", 10) == 0) {
        // What the panel shows, as a hosted PNG url — render ground truth.
        // NOT while the player owns the panel: premieres 4+5 both died on
        // screenshot-mid-play (dashboard's untouched ceremony then proved
        // playback alone finishes 8/8 clean — the interaction is the killer:
        // this task queues behind consecutive 2s blits holding the display
        // lock, then uploads while the metronome repaints). An honest busy
        // beats a corpse; the ceremony order is play → done → screenshot.
        if (tiny_stream_playing()) {
            reply(envelope_id,
                  "screenshot refused: stream playing (panel is the player's; "
                  "retry after 'play status' says done, or 'play stop')");
            return;
        }
        static char shot_url[256];
        esp_err_t r = tiny_screenshot_upload(shot_url, sizeof shot_url);
        if (r == ESP_OK) {
            char out[300];
            snprintf(out, sizeof out, "screenshot: %s", shot_url);
            reply(envelope_id, out);
        } else if (r == ESP_ERR_NO_MEM) {
            // The upload pre-flight refused rather than risk the internal
            // rail (P0 hunt) — say so in words, with the retry hint.
            char out[160];
            snprintf(out, sizeof out,
                     "screenshot deferred: internal RAM low (%u KB free) — "
                     "retry in a few seconds",
                     (unsigned)(heap_caps_get_free_size(
                         MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) / 1024));
            reply(envelope_id, out);
        } else {
            reply(envelope_id, esp_err_to_name(r));
        }
    } else if (strncmp(prompt, "page", 4) == 0) {
        // page home|status|sensors|settings|back|next|prev — the relay twin
        // of every tappable nav path (parity rule).
        const char *a = prompt + 4;
        while (*a == ' ') ++a;
        esp_err_t r;
        const char *what = a;
        if      (!strncmp(a, "home", 4) || !*a) r = tiny_shell_home();
        else if (!strncmp(a, "status", 6))      r = tiny_shell_open(TINY_PAGE_STATUS);
        else if (!strncmp(a, "sensor", 6))      r = tiny_shell_open(TINY_PAGE_SENSORS);
        else if (!strncmp(a, "setting", 7))     r = tiny_shell_open(TINY_PAGE_SETTINGS);
        else if (!strncmp(a, "wifi", 4))        r = tiny_shell_open(TINY_PAGE_WIFI);
        else if (!strncmp(a, "ble", 3) || !strncmp(a, "bluetooth", 9))
                                                r = tiny_shell_open(TINY_PAGE_BLE);
        else if (!strncmp(a, "gallery", 7) || !strncmp(a, "galeri", 6)) {
            r = tiny_display_gallery_nav(0);   // reopen the stored set
            if (r == ESP_ERR_INVALID_STATE) {
                reply(envelope_id, "gallery is empty - send photos first (render_ui type gallery)");
                return;
            }
        }
        else if (!strncmp(a, "universe", 8) || !strncmp(a, "agents", 6))
            // v11: the relay twin of the settings row / home badge tap.
            r = tiny_shell_universe_route("u:open");
        else if (!strncmp(a, "back", 4))        r = tiny_shell_back();
        else if (!strncmp(a, "next", 4))        r = tiny_shell_cycle(+1);
        else if (!strncmp(a, "prev", 4))        r = tiny_shell_cycle(-1);
        else if (!strncmp(a, "onboard", 7)) {
            // v12: `page onboard [welcome|wifi|link|pair|ready]` — preview a
            // first-run card on any device (no state change; `page home`
            // returns to whatever the real state says). Without a step: the
            // step the device is actually at, or home when done.
            const char *st = a + 7;
            while (*st == ' ') ++st;
            r = *st ? tiny_onboard_preview(st) : tiny_onboard_render();
            if (r == ESP_ERR_INVALID_ARG) {
                reply(envelope_id, "page onboard [welcome|wifi|link|pair|ready]");
                return;
            }
        }
        else { reply(envelope_id, "page: home|status|sensors|settings|wifi|ble|gallery|universe|onboard|back|next|prev"); return; }
        char out[96];
        snprintf(out, sizeof out, "page %s -> %s (card_id=%s)", what,
                 esp_err_to_name(r), tiny_display_current_card_id());
        reply(envelope_id, out);
    } else if (strncmp(prompt, "miccheck", 8) == 0) {
        // miccheck [seconds] - record and report RMS/peak instead of
        // uploading. A dead post-wake mux reads flat zeros; a live PDM mic
        // reads the room's noise floor. This is how sleep->wake->mic gets
        // proven with nobody home to speak.
        int secs = 2;
        sscanf(prompt + 8, "%d", &secs);
        if (secs < 1) secs = 1;
        if (secs > 10) secs = 10;
        uint8_t *wav = NULL; size_t wlen = 0;
        esp_err_t r = tiny_audio_record_wav(secs, &wav, &wlen);
        if (r != ESP_OK || !wav || wlen <= 44) {
            if (wav) free(wav);
            if (r == ESP_ERR_NOT_ALLOWED) {  // §11: reason, not errno
                reply(envelope_id, "miccheck refused: device is pocket-locked "
                      "(S11) - the mic stays dead until unlock");
                return;
            }
            char err[96];
            snprintf(err, sizeof err, "miccheck: record failed: %s",
                     esp_err_to_name(r));
            reply(envelope_id, err);
            return;
        }
        const int16_t *pcm = (const int16_t *)(wav + 44);
        size_t n = (wlen - 44) / 2;
        double acc = 0; int peak = 0; size_t nonzero = 0;
        for (size_t i = 0; i < n; ++i) {
            int v = pcm[i];
            acc += (double)v * v;
            if (v < 0) v = -v;
            if (v > peak) peak = v;
            if (v) ++nonzero;
        }
        free(wav);
        int rms = (int)__builtin_sqrt(acc / (double)(n ? n : 1));
        char out[224];
        snprintf(out, sizeof out,
                 "{\"verdict\":\"%s\",\"rms\":%d,\"peak\":%d,"
                 "\"nonzero_pct\":%d,\"samples\":%u,\"secs\":%d}",
                 (rms > 0 && nonzero > n / 10) ? "live" : "SILENT",
                 rms, peak, (int)(n ? nonzero * 100 / n : 0),
                 (unsigned)n, secs);
        reply(envelope_id, out);
    } else if (strncmp(prompt, "sleep", 5) == 0) {
        // sleep [seconds] — deep sleep. The AI button always wakes us (EXT1,
        // GPIO4); optional seconds adds a timer wake so sleep is testable
        // remotely. Order matters: reply FIRST (the PATCH must land while the
        // radio is up), paint the goodbye card (the panel keeps it with every
        // rail off — that is the e-ink magic), THEN hand over to the HAL,
        // which holds the power latch and gates the wake pin.
        int secs = 0;
        sscanf(prompt + 5, "%d", &secs);
        char bye[160];
        if (secs > 0)
            snprintf(bye, sizeof bye,
                     "sleeping - press the AI button or wait %ds", secs);
        else
            snprintf(bye, sizeof bye, "sleeping - press the AI button to wake");
        reply(envelope_id, bye);
        char card[340];
        snprintf(card, sizeof card,
                 "{\"type\":\"text\",\"title\":\"zzz\",\"card_id\":\"sleep\","
                 "\"body\":\"%s\","
                 "\"footer\":\"deep sleep - the panel keeps this image at 0 mA\"}",
                 bye);
        tiny_display_render_card(card);
        // §12: remember what the glass will show through sleep — the wake
        // paint diffs against a REDRAW of exactly this card (RTC RAM).
        tiny_display_note_sleep_card(bye);
        vTaskDelay(pdMS_TO_TICKS(3000));  // reply PATCH + e-ink refresh settle
        if (secs > 0)
            esp_sleep_enable_timer_wakeup((uint64_t)secs * 1000000ULL);
        sticky_power_enter_deep_sleep();  // noreturn
    } else if (strncmp(prompt, "play", 4) == 0) {
        // play <manifest_url> [interval_s] [max_frames] | play stop
        // The reply goes FIRST (a 60s stream outlives any relay window);
        // the player runs in its own task and the panel-hygiene law
        // (full wipe every 20 frames) is enforced device-side.
        if (strncmp(prompt + 4, " status", 7) == 0) {
            char v[220];
            snprintf(v, sizeof v, "player: %s%s", tiny_stream_verdict(),
                     tiny_stream_playing() ? " [task alive]" : "");
            reply(envelope_id, v);
            return;
        }
        if (strncmp(prompt + 4, " stop", 5) == 0) {
            tiny_stream_stop();
            reply(envelope_id, "stream stopping");
            return;
        }
        char murl[256] = {0}; int iv = 0, mf = 0;
        sscanf(prompt + 4, " %255s %d %d", murl, &iv, &mf);
        esp_err_t r = tiny_stream_play(murl, iv, mf);
        if (r == ESP_OK)
            reply(envelope_id, "stream started (frames follow on the glass; "
                               "play stop or any card ends it)");
        else if (r == ESP_ERR_INVALID_STATE)
            reply(envelope_id, "stream refused: one already playing — "
                               "play stop first");
        else
            reply(envelope_id, "stream refused: need https manifest url");
    } else if (strncmp(prompt, "ota", 3) == 0) {
        // ota [channel] — stage from the channel pointer, reply, THEN reboot
        // (reply first: after esp_restart nobody would ever hear the result).
        const char *ch = prompt[3] == ' ' ? prompt + 4 : "sticky-dev";
        esp_err_t r = tiny_ota_check_and_stage(ch);
        if (r == ESP_OK) {
            reply(envelope_id, "OTA staged + sha verified — rebooting into trial (rollback-armed)");
            vTaskDelay(pdMS_TO_TICKS(1500));
            esp_restart();
        } else if (r == ESP_ERR_INVALID_VERSION) {
            reply(envelope_id, "already up to date (" TINY_FW_VERSION ")");
        } else if (r == ESP_ERR_NOT_SUPPORTED) {
            // check 16: direction guard. The pointer is older than (or
            // incomparable with) the running fw and carries no force flag.
            reply(envelope_id,
                  "OTA refused: pointer is not newer than running "
                  TINY_FW_VERSION " and bundle has no \"force\":\"1\" — "
                  "publish a newer version, or set force to downgrade");
        } else {
            char out[96];
            snprintf(out, sizeof out, "OTA failed: %s", esp_err_to_name(r));
            reply(envelope_id, out);
        }
    } else {
        reply(envelope_id,
              "unknown command. I understand: render_ui {card-json}, status, sensors, "
              "say <text>, ask <text>, voice [seconds], lock, unlock, "
              "screenshot, miccheck, "
              "page <home|status|sensors|settings|wifi|ble|back|next|prev>, "
              "rotate <0|90|180|270|auto|report>, tap <x> <y>, swipe <x0> <y0> <x1> <y1>, "
              "scroll [up|down|top|<±px>], "
              "sleep [seconds], ota [channel].");
    }
}

// ---- BLACK BOX (P0 hunt, 2026-08-26) -------------------------------------
// RTC noinit RAM survives a panic-reboot (the latch fix in board.cpp makes
// that reboot actually HAPPEN now); a true power-on scrambles it, which the
// magic detects. dispatch() notes the verb before running it and marks it
// done after — so a boot that follows a panic can say in status exactly what
// was in flight (`died_doing`) or that the panic hit between verbs
// (`after <verb>`): the autopsy without a USB cable. Wi-Fi keys never enter
// the crumb (config bodies are recorded as their verb alone).
#define CRUMB_MAGIC 0x51C4B0B5u
static RTC_NOINIT_ATTR uint32_t s_crumb_magic;
static RTC_NOINIT_ATTR uint32_t s_crumb_inflight;
static RTC_NOINIT_ATTR char s_crumb[56];
static RTC_NOINIT_ATTR char s_crumb_step[16];  // stage WITHIN the verb
static char s_died_doing[80] = "";  // captured once at boot, then frozen

// Stage marker for multi-stage verbs (screenshot: snapshot→png→upload).
// The verb-level crumb convicted `screenshot` 3/3 but can't say WHERE it
// died; this names the stage, same RTC-noinit autopsy rail. Cleared at
// every dispatch so a stale stage never haunts an unrelated verb.
extern "C" void tiny_node_crumb_step(const char *step) {
    strlcpy(s_crumb_step, step ? step : "", sizeof s_crumb_step);
}

void tiny_node_crumb_boot_capture(void) {
    const esp_reset_reason_t rr = esp_reset_reason();
    const bool abnormal = rr == ESP_RST_PANIC || rr == ESP_RST_INT_WDT ||
                          rr == ESP_RST_TASK_WDT || rr == ESP_RST_WDT;
    if (abnormal && s_crumb_magic == CRUMB_MAGIC && s_crumb[0]) {
        s_crumb[sizeof s_crumb - 1] = 0;
        s_crumb_step[sizeof s_crumb_step - 1] = 0;
        snprintf(s_died_doing, sizeof s_died_doing, "%s%s%s%s",
                 s_crumb_inflight ? "" : "after ", s_crumb,
                 s_crumb_step[0] ? "@" : "", s_crumb_step);
        ESP_LOGE(TAG, "previous life died %s: %s",
                 s_crumb_inflight ? "DOING" : "idle, last verb", s_died_doing);
    }
    s_crumb_magic = CRUMB_MAGIC;
    s_crumb_inflight = 0;
    s_crumb[0] = 0;
    s_crumb_step[0] = 0;
}

const char *tiny_node_died_doing(void) { return s_died_doing; }

static void dispatch(const char *envelope_id, const char *prompt) {
    const bool secret = strncmp(prompt, "config ", 7) == 0;
    strlcpy(s_crumb, secret ? "config <redacted>" : prompt, sizeof s_crumb);
    s_crumb_step[0] = 0;  // stages belong to THIS verb only
    s_crumb_inflight = 1;
    dispatch_inner(envelope_id, prompt);
    s_crumb_inflight = 0;  // verb completed; crumb stays as "last verb"
}

// ---------- 401 discipline (NET-P0-1 hotfix) ----------
// ---------- relay poll ----------
// ---------- adaptive cadence (battery is the product) ----------
// The relay poll was a flat 5s forever — ~17k TLS handshakes/day even when the
// device sat untouched in a pocket. Cadence now follows recent activity:
//   ACTIVE (5s)  while anything happened in the last 2 minutes — an envelope
//                arrived, a UI event was queued (tap/button), or we booted.
//   IDLE  (60s)  after 2 quiet minutes. First interaction snaps back to 5s.
// Heartbeat cadence is untouched (30s / 5min-halted): presence stays honest,
// and the halted path already has its own slow loop. Worst-case cost of idle:
// a relay envelope waits <=60s when nobody has touched the device for 2min —
// and the sender sees pending:true semantics anyway (use_device contract).
#define POLL_ACTIVE_MS   5000
#define POLL_IDLE_MS     60000
static int64_t s_last_activity_us = 0;  // set at boot in tiny_node_start
static void note_activity(void) { s_last_activity_us = esp_timer_get_time(); }
static bool cadence_idle(void) {
    return (esp_timer_get_time() - s_last_activity_us) > ACTIVE_WINDOW_US;
}

// Settings quotes the SYSTEM, not the spec — exported cadence truth.
extern "C" bool tiny_node_poll_idle(void) { return cadence_idle(); }

// ── AUTO-SLEEP — battery is the product ─────────────────────────────────────
// After 30 min with no activity (touch, envelope, boot — the SAME clock the
// poll cadence trusts) the device deep-sleeps with a 15-min timer wake, so a
// remote verb waits ≤15 min worst-case and a button press answers instantly
// (EXT1). A timer wake is a CHECK-IN, not a session: it gets a 90 s budget
// (a few active-cadence polls) and re-sleeps unless something happened.
// Guards, each one an incident waiting to be prevented:
//   charging     — free power; stay reachable, the wall is not the pocket.
//   stream/verb  — never sleep mid-performance or mid-DOING (crumb inflight).
//   unenrolled   — a setup session dying in the user's hands teaches distrust.
#define AUTOSLEEP_NAP_US    (90LL * 1000000)
#define AUTOSLEEP_LOCKED_US (45LL * 1000000)
#define AUTOSLEEP_WAKE_SECS (15 * 60)
// The idle budget is a setting (5/15/30/60 min). Cached
// here so autosleep_due never touches NVS on the poll path; seeded from
// config in tiny_node_start, updated by the settings row through the setter.
static int s_sleep_idle_min = 30;
void tiny_node_sleep_idle_set(int minutes) {
    if (minutes == 5 || minutes == 15 || minutes == 30 || minutes == 60)
        s_sleep_idle_min = minutes;
}
static bool s_timer_wake = false;  // set in tiny_node_start from wake cause
// A LOCKED session is not a session. With three buttons arming
// EXT1, a pocket bumps the glass awake constantly — and while locked there is
// nothing a human can do except the UP+DOWN unlock chord, so a 30 min budget
// would burn radio for a device nobody is holding. Locked ⇒ 45 s: long enough
// to boot, paint the glance and hold the chord twice, short enough that a
// pocket wake costs seconds, not half an hour. Read LIVE (not latched at
// boot): unlocking mid-window restores the full budget, and tiny_lock_set
// pushes the activity clock on the way out so the restored budget starts now.
static bool autosleep_due(void) {
    const int64_t idle = esp_timer_get_time() - s_last_activity_us;
    const int64_t budget = tiny_lock_is_locked() ? AUTOSLEEP_LOCKED_US
                         : s_timer_wake          ? AUTOSLEEP_NAP_US
                         : (int64_t)s_sleep_idle_min * 60 * 1000000;
    return idle > budget;
}

static void poll_relay(char *resp, int cap) {
    cJSON *b = base_body();
    cJSON_AddNumberToObject(b, "max", 5);
    int st = post_cjson(HTTP_METHOD_PUT, "/api/devices/relay", b, resp, cap);
    if (st == 401) { auth_401("relay"); return; }
    if (st == 200) auth_ok();
    if (st != 200 || !resp[0]) return;
    auth_ok();
    cJSON *root = cJSON_Parse(resp);
    if (!root) {
        // Relay delivery is delivered-ONCE: the server has already handed these
        // envelopes over, so a document we cannot parse means up to `max` of
        // them are gone — never dispatched, never acked, and the sender sees a
        // command that simply vanished. We cannot recover them (their ids were
        // in the part we could not read), so the least we owe is naming the
        // loss. If this line ever appears, the sink above is too small: the
        // byte counts in the preceding "response truncated" error say by how
        // much.
        ESP_LOGE(TAG, "relay poll body unparseable (%d B in a %d B buffer) — up "
                      "to %d envelopes LOST unacked this cycle",
                 (int)strlen(resp), cap, 5);
        return;
    }
    const cJSON *msgs = cJSON_GetObjectItem(root, "messages");
    const cJSON *m = NULL;
    cJSON_ArrayForEach(m, msgs) {
        const cJSON *id = cJSON_GetObjectItem(m, "id");
        if (!cJSON_IsString(id)) continue;
        // payload arrives as a SERIALIZED JSON STRING (the sender does
        // JSON.stringify) — Nicla's json.loads(msg["payload"]) is the
        // reference. Tolerate a nested object too, in case that changes.
        const cJSON *payload = cJSON_GetObjectItem(m, "payload");
        cJSON *inner = NULL;
        const cJSON *prompt = NULL;
        if (cJSON_IsString(payload)) {
            inner = cJSON_Parse(payload->valuestring);
            if (inner) prompt = cJSON_GetObjectItem(inner, "prompt");
        } else if (cJSON_IsObject(payload)) {
            prompt = cJSON_GetObjectItem(payload, "prompt");
        }
        // Observer effect: the status verb rides an envelope,
        // and this very line snaps the cadence to active — so build_status()
        // could never truthfully say "idle". Capture the age BEFORE the snap;
        // status reports it as idle_for_s/cadence_at_poll, making the 60s
        // backoff falsifiable by one probe after two quiet minutes.
        s_idle_at_envelope_s =
            (int)((esp_timer_get_time() - s_last_activity_us) / 1000000);
        note_activity();  // an envelope is a conversation: stay in 5s cadence
        if (cJSON_IsString(prompt))
            dispatch(id->valuestring, prompt->valuestring);
        else
            reply(id->valuestring, "unparseable envelope (no prompt) - never silence");
        if (inner) cJSON_Delete(inner);
    }
    cJSON_Delete(root);
}

// ---------- the loop ----------
static void node_task(void *) {
    // 8192, matching REPLY_MAX: a single poll asks for up to 5 envelopes and a
    // `render_ui` envelope carries a whole card spec, so two pushed cards
    // already crowd 4096 — and the overflow was silent (see http_evt). Sized to
    // the largest reply the protocol admits, because the buffer that receives
    // work should not be smaller than the work the protocol allows. Cost is 4 KB
    // of .bss on a device with 512 KB of internal SRAM and 8 MB of PSRAM.
    EXT_RAM_BSS_ATTR static char resp[8192];  // PSRAM .bss — 8K relay sink off the internal rail
    int hb_countdown = 0;  // heartbeat immediately on start
    int hb_fail = 0;
    while (!s_stop) {
        // Wedge watchdog: if ONE gesture/tap route has
        // held the act task for 45s, something below the UI is never coming
        // back (the P0 was exactly this — every later envelope pending
        // forever). This task still runs, so it can turn the silent brick
        // into a reboot. 45s clears the worst honest route by 10x (4s BLE
        // scan + two full e-ink refreshes ≈ 8s); the reboot lands on the
        // same rollback-armed boot path every OTA already trusts.
        const int64_t busy_ms = tiny_touch_act_busy_ms();
        if (busy_ms > 45000) {
            ESP_LOGE(TAG, "act task wedged %lld ms — rebooting to save the relay",
                     (long long)busy_ms);
            tiny_node_post_event("device_note", "wedge watchdog: act task stuck "
                                                ">45s, rebooting");
            vTaskDelay(pdMS_TO_TICKS(500));  // give the event POST a chance
            esp_restart();
        }
        if (!tiny_wifi_is_up()) { vTaskDelay(pdMS_TO_TICKS(2000)); continue; }
        if (hb_countdown <= 0) {
            int st = heartbeat();
            ESP_LOGI(TAG, "heartbeat -> %d", st);
            if (st == 401)      auth_401("heartbeat");
            else if (st == 200) auth_ok();
            hb_fail = (st == 200) ? 0 : hb_fail + 1;
            // Rollback judge: an OTA image boots PENDING_VERIFY; the first
            // heartbeat 200 IS the health proof (wifi + TLS + auth + backend
            // all work), so cancel rollback exactly here and nowhere else.
            if (st == 200) {
                const esp_partition_t *run = esp_ota_get_running_partition();
                esp_ota_img_states_t is;
                if (run && esp_ota_get_state_partition(run, &is) == ESP_OK &&
                    is == ESP_OTA_IMG_PENDING_VERIFY) {
                    esp_ota_mark_app_valid_cancel_rollback();
                    ESP_LOGI(TAG, "OTA image marked VALID (first heartbeat 200)");
                    tiny_bootmark_stage(9);  // flight recorder: clean run
                }
            }
            // §16 law 2 drain: ONE queued ask per healthy heartbeat — the
            // 200 just proved wifi+TLS+auth+backend end to end, which is
            // the exact claim a queued send needs. One per pass, because
            // each ask is a full agent turn on the glass (up to ~80s);
            // draining eight back-to-back would occupy the loop for
            // minutes. Pop ONLY on success: a failed drain leaves the
            // question queued and the next heartbeat tries again.
            if (st == 200 && tiny_askq_count() > 0) {
                char qtext[TINY_ASKQ_MAX_LEN];
                if (tiny_askq_peek(qtext, sizeof qtext) == ESP_OK) {
                    ESP_LOGI(TAG, "askq drain (%d left): %.60s",
                             tiny_askq_count(), qtext);
                    char qout[512];
                    s_askq_draining = true;
                    esp_err_t qres =
                        tiny_node_do_ask(qtext, 0, qout, sizeof qout);
                    s_askq_draining = false;
                    // Pop when the question is SPENT: success, or the backend
                    // spoke (even an error — st>0 is never retried). Only a
                    // transport failure leaves it queued for the next 200.
                    if (qres != ESP_OK && s_ask_backend_spoke) {
                        ESP_LOGW(TAG, "askq: backend rejected queued ask - "
                                      "spent, not retried: %.60s", qtext);
                        tiny_askq_pop();
                        tiny_display_glance();  // retire/decrement the badge
                    }
                    if (qres == ESP_OK) {
                        tiny_askq_pop();
                        // §16 law 4: reconnect is announced ONLY when a
                        // queued answer arrives — this is that moment. The
                        // answer is already on the glass; the chime marks
                        // it, one partial refreshes the qN badge away.
                        tiny_audio_chime("ack");
                        tiny_display_glance();
                    }
                }
            }
            // 6 x 5s = 30s normally; once auth is halted, 5 minutes.
            hb_countdown = s_auth_halted ? 60 : 6;
        }
        if (s_auth_halted) {
            // Auth has been refused for >5 minutes. Stop hammering the relay,
            // keep the identity, and let the slow heartbeat above be the only
            // traffic: if the backend comes back, auth_ok() clears this flag and
            // the loop resumes on its own with no reboot and no wipe.
            hb_countdown--;
            vTaskDelay(pdMS_TO_TICKS(5000));
            continue;
        }
        poll_relay(resp, sizeof resp);
        // Drain queued device events (ui_tap etc.) — best-effort, never fatal.
        tiny_event_t ev;
        while (s_events && xQueueReceive(s_events, &ev, 0) == pdTRUE) {
            note_activity();  // a human touched the glass: snap to 5s cadence
            wire_fold_bank(ev.detail);  // BUG-2: glass encoding stays on glass
            cJSON *b = base_body();
            cJSON_AddStringToObject(b, "kind", ev.kind);
            cJSON_AddStringToObject(b, "detail", ev.detail);
            int st = post_cjson(HTTP_METHOD_POST, "/api/devices/event", b, NULL, 0);
            ESP_LOGI(TAG, "event %s -> %d", ev.kind, st);
        }
        // Adaptive tick. hb_countdown counts TICKS, so an idle tick (60s)
        // spends its whole 30s budget at once: heartbeat lands every ~30s
        // active, every ~60s idle. The contract is "regular presence", and
        // a 60s beat keeps unread-badge freshness acceptable while idle.
        const bool idle = cadence_idle();
        // Auto-sleep sits AFTER envelope handling in the tick, so a
        // verb that just arrived always executes before the due-check runs.
        if (autosleep_due() && !s_crumb_inflight && !tiny_stream_playing()) {
            BatteryDetail bd = {};
            const bool charging =
                sticky_battery_read_detail(bd) == ESP_OK && bd.charging;
            tiny_config_t cfg = {};
            const bool enrolled = tiny_config_load(&cfg) == ESP_OK &&
                                  tiny_config_is_provisioned(&cfg);
            if (!charging && enrolled) {
                ESP_LOGI(TAG, "auto-sleep: idle %s, battery %d%%, wake in %d min",
                         tiny_lock_is_locked() ? "(locked 45s window over)"
                         : s_timer_wake        ? "(timer check-in over)"
                                               : "30 min",
                         bd.percent, AUTOSLEEP_WAKE_SECS / 60);
                char bye[112];
                // The locked glass holds this card for HOURS — it must
                // teach the exit (§11: the lockout card teaches the chord),
                // not promise "any button" to a pocket that can't use one.
                if (tiny_lock_is_locked())
                    snprintf(bye, sizeof bye,
                             "locked - sleeping to save battery (%d%%). "
                             "any button wakes me; hold up+down 1s to unlock.",
                             bd.percent);
                else
                    snprintf(bye, sizeof bye,
                             "idle - sleeping to save battery (%d%%). any button wakes me.",
                             bd.percent);
                char card[320];
                snprintf(card, sizeof card,
                         "{\"type\":\"text\",\"title\":\"zzz\",\"card_id\":\"sleep\","
                         "\"body\":\"%s\","
                         "\"footer\":\"deep sleep - checking in every 15 min\"}",
                         bye);
                tiny_display_render_card(card);
                tiny_display_note_sleep_card(bye);  // §12 wake-paint diff
                vTaskDelay(pdMS_TO_TICKS(3000));    // e-ink refresh settle
                esp_sleep_enable_timer_wakeup(
                    (uint64_t)AUTOSLEEP_WAKE_SECS * 1000000ULL);
                sticky_power_enter_deep_sleep();    // noreturn
            }
            // Charging or unenrolled: not due again until activity resets —
            // push the clock so this branch doesn't re-run every tick.
            note_activity();
        }
        hb_countdown -= idle ? 6 : 1;  // idle tick spends the whole 30s budget
        vTaskDelay(pdMS_TO_TICKS(idle ? POLL_IDLE_MS : POLL_ACTIVE_MS));
    }
    s_task = NULL;
    vTaskDelete(NULL);
}

esp_err_t tiny_node_start(void) {
    tiny_node_crumb_boot_capture();  // read the black box before any dispatch
    note_activity();  // boot starts in ACTIVE cadence — never wake into a 60s hole
    // A timer wake is a 90 s check-in, not a 30 min session.
    s_timer_wake =
        esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_TIMER;
    ESP_RETURN_ON_ERROR(tiny_config_load(&s_cfg), TAG, "cfg");
    if (!s_cfg.device_id[0] || !s_cfg.token[0]) return ESP_ERR_INVALID_STATE;
    // The idle budget the owner chose survives reboot.
    tiny_node_sleep_idle_set(tiny_config_sleepidle_get());
    s_stop = false;
    if (!s_events) s_events = xQueueCreate(EVENT_QUEUE_LEN, sizeof(tiny_event_t));
    // P0 CONVICTED 2026-08-26 (serial backtrace, first hardware witness):
    // "***ERROR*** A stack overflow in task tiny_node" the instant the
    // screenshot upload (96KB png -> base64 body -> esp-tls) started. 8192
    // was sized at M4 for a poll loop; 26 releases of verb frames later,
    // dispatch_inner's deep paths (screenshot/ota = big-transfer TLS under
    // a full verb stack) no longer left the ~4KB mbedtls needs. Heartbeats
    // survived because they run from a shallow frame — that asymmetry WAS
    // the "large transfer = death" signature. 16KB internal RAM is cheap
    // against a 122KB floor; status now reports the true watermark.
    return xTaskCreate(node_task, "tiny_node", 16384, NULL, 5, &s_task) == pdPASS
               ? ESP_OK : ESP_ERR_NO_MEM;
}

void tiny_node_stop(void) { s_stop = true; }

// The unlock chord is the one input the locked window must not
// punish — tiny_lock_set(false) calls this so the full idle budget starts at
// the unlock, not at whatever the pocket last did.
void tiny_node_note_activity(void) { note_activity(); }

esp_err_t tiny_node_post_event(const char *kind, const char *detail) {
    // Producer side only: queue and return. The node task owns all HTTP.
    if (!s_events || !kind || !detail) return ESP_ERR_INVALID_STATE;
    tiny_event_t ev = {};
    strlcpy(ev.kind, kind, sizeof ev.kind);
    strlcpy(ev.detail, detail, sizeof ev.detail);
    return xQueueSend(s_events, &ev, 0) == pdTRUE ? ESP_OK : ESP_ERR_NO_MEM;
}
