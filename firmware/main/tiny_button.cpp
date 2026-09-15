#include "tiny/tiny_button.h"

#include <stdint.h>
#include <string.h>

#include "button_gpio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "iot_button.h"
#include "pin_config.h"
#include "sticky_buzzer.h"
#include "tiny/tiny_audio.h"
#include "tiny/tiny_display.h"
#include "tiny/tiny_node.h"
#include "tiny/tiny_shell.h"
#include "tiny/tiny_lock.h"

static const char *TAG = "tiny_button";

static button_handle_t s_ai = NULL, s_up = NULL, s_down = NULL;
static QueueHandle_t s_ask_q = NULL;  // depth 1: an ask in flight wins

// The queue item carries the WHOLE request: text[0]==0 means "record
// the mic", anything else is a typed question. Copying the text into the item
// instead of a shared buffer keeps the two producers (AI button, keyboard ok)
// race-free without a lock — the queue's copy semantics ARE the lock.
struct AskReq { char text[96]; };

static void voice_ask_task(void *) {
    AskReq req;
    for (;;) {
        if (xQueueReceive(s_ask_q, &req, portMAX_DELAY) != pdTRUE) continue;
        const bool typed = req.text[0] != 0;
        ESP_LOGI(TAG, "%s ask starting", typed ? "typed" : "AI button: voice");
        static char out[512];
        esp_err_t r = tiny_node_do_ask(typed ? req.text : NULL, 6, out, sizeof out);
        ESP_LOGI(TAG, "%s ask -> %s: %s", typed ? "typed" : "AI button",
                 esp_err_to_name(r), out);
        if (r != ESP_OK) tiny_audio_chime("error");
        char note[560];
        snprintf(note, sizeof note, "%s ask: %s", typed ? "typed" : "AI button", out);
        tiny_node_post_event("device_note", note);
    }
}

// The one door into the ask worker. The physical AI button, an "Ask tiny"/
// "Speak" screen tap and the on-glass keyboard are the same intent, so they
// share the same single-flight queue — three paths to one worker, never two
// concurrent asks (one mic, one panel).
extern "C" esp_err_t tiny_button_request_voice_ask(void) {
    if (tiny_lock_is_locked()) {
        ESP_LOGW(TAG, "locked: voice capture refused at entry (§11)");
        return ESP_ERR_NOT_ALLOWED;
    }
    AskReq req = {};
    if (!s_ask_q) return ESP_ERR_INVALID_STATE;
    if (xQueueSend(s_ask_q, &req, 0) != pdTRUE) {
        ESP_LOGW(TAG, "ask already in flight - request dropped");
        return ESP_ERR_INVALID_STATE;
    }
    return ESP_OK;
}

// Typed path (keyboard "ok"). Refuses (never clips) a question over the
// item's 95 chars — the keyboard buffer is 63, so hitting this means the
// contract changed upstream and clipping would send a DIFFERENT question.
extern "C" esp_err_t tiny_button_request_text_ask(const char *text) {
    if (!s_ask_q) return ESP_ERR_INVALID_STATE;
    if (!text || !*text) return ESP_ERR_INVALID_ARG;
    AskReq req = {};
    if (strlcpy(req.text, text, sizeof req.text) >= sizeof req.text) {
        ESP_LOGE(TAG, "typed ask %d B exceeds %u — refusing to clip a question",
                 (int)strlen(text), (unsigned)sizeof req.text);
        return ESP_ERR_INVALID_SIZE;
    }
    if (xQueueSend(s_ask_q, &req, 0) != pdTRUE) {
        ESP_LOGW(TAG, "ask already in flight - typed request dropped");
        return ESP_ERR_INVALID_STATE;
    }
    return ESP_OK;
}

static void on_ai_click(void *, void *) {
    // §11 THE rule that is the point: a pocketed AI button must never start
    // voice capture. (request_voice_ask refuses on its own too — this gate
    // exists so the refusal is FELT: double blip through the fabric. Glance
    // card short-press wake lands with §12.)
    if (tiny_lock_is_locked()) {
        ESP_LOGW(TAG, "locked: AI press ignored - voice capture refused (§11)");
        sticky_buzzer_beep_double();
        return;
    }
    tiny_lock_note_activity();
    tiny_button_request_voice_ask();
}

// Long-press AI = the home button, from anywhere (M10). Renders block on the
// e-ink busy-wait, and iot_button callbacks run on its timer task — so hand
// the work to the shell nav queue below instead of rendering here.
enum class Nav : uint8_t { kHome, kUp, kDown };
static QueueHandle_t s_nav_q = NULL;

static void nav_task(void *) {
    Nav n;
    for (;;) {
        if (xQueueReceive(s_nav_q, &n, portMAX_DELAY) != pdTRUE) continue;
        switch (n) {
            case Nav::kHome: tiny_shell_home(); break;
            // Phone rule (M12): when the card on the glass overflows, UP/DOWN
            // are the scroll wheel; only a card that FITS lets them page.
            // Clamped at the edges — reaching the bottom does not fall through
            // to a page flip (that would turn "read to the end" into "lose the
            // page you were reading").
            case Nav::kUp:
                // Gallery contract: while a photo set owns
                // the glass, UP/DOWN are the page-flip — checked before the
                // scroll rule because a raw frame never overflows.
                if (!strcmp(tiny_display_current_card_id(), "gallery")) {
                    if (tiny_display_gallery_nav(-1) != ESP_OK)
                        sticky_buzzer_beep_double();
                    break;
                }
                if (tiny_display_scroll_page(-1) == ESP_ERR_INVALID_STATE &&
                    tiny_shell_cycle(-1) == ESP_ERR_NOT_ALLOWED)
                    sticky_buzzer_beep_double();  // pushed page: not on the ring
                break;
            case Nav::kDown:
                if (!strcmp(tiny_display_current_card_id(), "gallery")) {
                    if (tiny_display_gallery_nav(+1) != ESP_OK)
                        sticky_buzzer_beep_double();
                    break;
                }
                if (tiny_display_scroll_page(+1) == ESP_ERR_INVALID_STATE &&
                    tiny_shell_cycle(+1) == ESP_ERR_NOT_ALLOWED)
                    sticky_buzzer_beep_double();  // dead-end word, never silence
                break;
        }
    }
}

static void queue_nav(Nav n) {
    if (s_nav_q) xQueueSend(s_nav_q, &n, 0);  // full queue = drop, never block
}

static void on_ai_long(void *, void *) {
    if (tiny_lock_is_locked()) return;  // §11: locked buttons page nothing
    queue_nav(Nav::kHome);
}

static void on_page_click(void *, void *usr) {
    // §11: while locked, UP/DOWN are the unlock chord ONLY (tiny_lock's own
    // GPIO watcher reads them raw) — they page nothing here.
    if (tiny_lock_is_locked()) return;
    tiny_lock_note_activity();  // §11: button = in use, resets the idle clock
    // UP/DOWN cycle the local page ring (M10) — and still tell the fleet.
    queue_nav((uintptr_t)usr ? Nav::kDown : Nav::kUp);
    tiny_node_post_event("device_note",
                         (uintptr_t)usr ? "button: down" : "button: up");
}

static esp_err_t mk_button(int gpio, button_handle_t *h,
                           button_cb_t cb, void *usr) {
    button_config_t bc = {};
    bc.long_press_time = 2000;
    bc.short_press_time = 180;
    button_gpio_config_t gc = {};
    gc.gpio_num = gpio;
    gc.active_level = 0;
    gc.enable_power_save = false;
    gc.disable_pull = false;
    esp_err_t r = iot_button_new_gpio_device(&bc, &gc, h);
    if (r != ESP_OK) return r;
    return iot_button_register_cb(*h, BUTTON_SINGLE_CLICK, NULL, cb, usr);
}

extern "C" esp_err_t tiny_button_init(void) {
    if (s_ai) return ESP_OK;
    s_ask_q = xQueueCreate(1, sizeof(AskReq));
    if (!s_ask_q) return ESP_ERR_NO_MEM;
    // 8KB stack: the worker calls the whole HTTP/TLS ask path.
    if (xTaskCreate(voice_ask_task, "voice_ask", 8192, NULL, 4, NULL) != pdPASS)
        return ESP_ERR_NO_MEM;
    // Nav worker: shell renders block seconds on e-ink; depth 4 is plenty
    // (flipping faster than the panel refreshes just coalesces at the glass).
    s_nav_q = xQueueCreate(4, sizeof(Nav));
    if (!s_nav_q) return ESP_ERR_NO_MEM;
    if (xTaskCreate(nav_task, "shell_nav", 6144, NULL, 3, NULL) != pdPASS)
        return ESP_ERR_NO_MEM;
    ESP_ERROR_CHECK(mk_button(PIN_BTN_OK, &s_ai, on_ai_click, NULL));
    ESP_ERROR_CHECK(iot_button_register_cb(s_ai, BUTTON_LONG_PRESS_START, NULL,
                                           on_ai_long, NULL));
    ESP_ERROR_CHECK(mk_button(PIN_BTN_UP, &s_up, on_page_click, (void *)0));
    ESP_ERROR_CHECK(mk_button(PIN_BTN_DOWN, &s_down, on_page_click, (void *)1));
    ESP_LOGI(TAG, "buttons live: AI=%d up=%d down=%d", PIN_BTN_OK, PIN_BTN_UP,
             PIN_BTN_DOWN);
    return ESP_OK;
}
