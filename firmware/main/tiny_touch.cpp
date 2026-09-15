// tiny_touch — GT911 taps -> card button regions -> ack + action + ui_tap event.
// Init + portrait->landscape transform adapted from the vendored
// Sticky_dashboard_demo sticky_touch.cpp (production-verified on this panel).
//
// A tap has three jobs and they run in this order, because that is the order a
// human perceives them: (1) FEEL it — buzzer blip on the touch task, ~60ms;
// (2) SEE it — button rect inverts via partial refresh, queued to the display's
// own worker so the e-ink busy-wait never stalls finger sampling; (3) MEAN
// something — the local action runs on this file's action task, and the ui_tap
// event goes upstream. A button that does nothing is a lie on the screen.
#include "tiny/tiny_touch.h"
#include "tiny/tiny_lock.h"

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "gt911.h"
#include "pin_config.h"
#include "sticky_buzzer.h"
#include "tiny/tiny_button.h"
#include "tiny/tiny_display.h"
#include "tiny/tiny_node.h"
#include "tiny/tiny_sensors.h"
#include "tiny/tiny_shell.h"
#include "tiny/tiny_onboard.h"

namespace {

constexpr char kTag[] = "tiny_touch";
constexpr uint16_t kW = 800, kH = 480;
constexpr uint16_t kPortraitW = 480, kPortraitH = 800;
constexpr TickType_t kPoll = pdMS_TO_TICKS(30);
constexpr int kTapSlop = 24;  // finger may drift this much and still be a tap

i2c_master_bus_handle_t s_bus = nullptr;
GT911 s_ctl;
TaskHandle_t s_task = nullptr;
bool s_touching = false;
uint16_t s_start_x = 0, s_start_y = 0, s_last_x = 0, s_last_y = 0;
// Ghost-touch defence: the GT911 emits phantom
// single-poll points during boot/wifi bring-up. A real finger spans several
// 30ms polls; ghosts don't.
//
// But requiring 2 consecutive polls at 30ms threw away crisp 40-60ms taps —
// cagatay's natural taps (audit TOUCH P1-1). The GT911 also reports contact
// AREA, and phantoms characteristically report size == 0, so: a point with
// size > 0 is believed on its FIRST poll; a size-0 point still has to prove
// itself over kDebouncePolls. The boot grace shrinks to the observed ghost
// window because a card with buttons is tappable the moment it is on the glass.
constexpr int kDebouncePolls = 2;
constexpr int64_t kBootGraceMs = 1200;
constexpr uint16_t kMinTrustedSize = 1;
int s_touch_streak = 0;
TickType_t s_init_tick = 0;
// Card generation sampled at finger-DOWN. If a render commits a different card
// while the finger is still down, the button under it is not the one the human
// aimed at, so the tap is dropped (audit TOUCH P0-1).
uint32_t s_gen_at_down = 0;

// ---- local actions ---------------------------------------------------------
// Run OFF the touch task: a voice ask blocks ~30s and a card render blocks
// seconds on the e-ink busy-wait. The touch task must keep sampling fingers.
enum class Act : uint8_t { kNone, kVoiceAsk, kStatusCard, kSensorsCard,
                           kHome, kSettings, kBack, kWifi, kKey, kMenu, kGallery, kSilent,
                           kRescanWifi, kBle, kScroll, kMessages, kTypeAsk, kConfigQr,
                           kUniverse, kOnboard };
// kScroll carries the swipe's endpoints in x0..y1 (panel coords); the display
// layer decides whether that vector means scroll under the current rotation.
struct ActMsg { Act act; int index; int x0, y0, x1, y1;
                char label[32]; char id[48]; };
QueueHandle_t s_act_q = nullptr;
// Wedge watchdog: timestamp of the route the act task is INSIDE.
static volatile int64_t s_act_busy_since_us = 0;
extern "C" int64_t tiny_touch_act_busy_ms(void) {
    const int64_t t = s_act_busy_since_us;
    return t ? (esp_timer_get_time() - t) / 1000 : 0;
}
// Route completion counter: bumps once per FINISHED route.
// The swipe/tap verbs wait on this so their receipt reports the state of the
// glass AFTER the route, not at inject time -- a receipt that races its own
// consequence reports provenance, not truth (the M9 draw-time lesson again).
static volatile uint32_t s_route_seq = 0;
extern "C" uint32_t tiny_touch_route_seq(void) { return s_route_seq; }
// The finished route's OWN verdict: routed:true only says the
// route RAN — a dead-end and a nav both complete. This says what the route
// decided. Written before the seq bump, so a receipt that saw seq advance may
// trust it. Non-gesture routes (buttons) report ESP_OK: their effect IS the
// route body.
static volatile esp_err_t s_route_result = ESP_OK;
extern "C" esp_err_t tiny_touch_route_result(void) { return s_route_result; }
// The finished route's REGION identity. "routed:true, route:ESP_OK"
// names nothing — two reviewer probes could not distinguish "toggle no-op"
// from "hit a different region" without three extra glass ops. Written by
// the act task before the seq bump (same trust rule as s_route_result).
static char s_route_id[48] = "";
static char s_route_label[32] = "";
extern "C" void tiny_touch_route_region(char *id, size_t idcap,
                                        char *label, size_t labcap) {
    if (id) strlcpy(id, s_route_id, idcap);
    if (label) strlcpy(label, s_route_label, labcap);
}

bool token_match(const char *s, const char *needle) {
    if (!s || !needle) return false;
    const size_t n = strlen(needle);
    for (const char *p = s; *p; ++p) {
        size_t i = 0;
        while (i < n && p[i] &&
               (char)tolower((unsigned char)p[i]) == needle[i]) ++i;
        if (i == n) return true;
    }
    return false;
}

// Map a button's id (preferred) or label onto a local action. Anything we do
// not recognise is still reported upstream as ui_tap — the agent decides.
//
// The label is sniffed ONLY when the card supplied no id. A card that gives an
// explicit id has already said what the button is, so its label is just words
// on glass — and once cards are built from other people's words that matters:
// on the message inbox a contact named "No[ble]" or a Turkish line saying
// "[sor]un var" would otherwise re-scan Bluetooth or open the microphone, while
// still reporting the tap, so one finger did two things and neither was asked
// for. The firmware's own hand-written cards pass bare strings with no id
// (tiny_shell's "Wi-Fi"/"Back"/"Home"), which is exactly the case this keeps.
Act action_for(const char *id, const char *label) {
    // Shell-owned ids take absolute precedence over token sniffing: "k:s"
    // must be the letter s, never the "sensors" token.
    if (id && id[0] == 'k' && id[1] == ':') return Act::kKey;
    // ARMOR: "w:home" shipped on five cards (0.25.16 and earlier) and the
    // w:-prefix rule below swallowed it — tiny_shell_menu's atoi("home") is 0,
    // so tapping [Home] after any Wi-Fi scan JOINED network row 0: an open AP
    // persisted to NVS plus a reboot, or a password keyboard aimed at a
    // stranger's SSID. The ids are fixed at the source, but e-ink holds a card
    // until something repaints it: a device that sleeps on an old glass and
    // wakes after this OTA can still hand us the old id. Checked ABOVE the
    // prefix rule so the armor cannot be out-voted.
    if (id && !strcmp(id, "w:home")) return Act::kHome;
    if (id && id[0] == 'w' && id[1] == ':') return Act::kMenu;
    // "wf:<idx>" forget row + "wf:yes"/"wf:no" confirm — shell-owned,
    // same page family as w:. Listed here because the w: rule above only
    // matches 'w'+':' and a fallthrough would hand "wf:0" to token sniffing.
    if (id && id[0] == 'w' && id[1] == 'f' && id[2] == ':') return Act::kMenu;
    // Settings rotation-lock row — shell-owned, routed the day it
    // was born (the rule for every new id).
    if (id && !strcmp(id, "rotlock")) return Act::kMenu;
    if (id && !strcmp(id, "sleepidle")) return Act::kMenu;  // same rule
    if (id && !strcmp(id, "beepvol")) return Act::kMenu;    // same rule
    // "m:inbox" / "m:t:<login>" — the messages app. The destination travels
    // in the id, so no card needs to remember what is on the glass.
    if (id && id[0] == 'm' && id[1] == ':') return Act::kMessages;
    // "g:open" / "g:prev" / "g:next" — the gallery. Direction travels in the
    // id, same pattern as the messages app.
    if (id && id[0] == 'g' && id[1] == ':') return Act::kGallery;
    // "u:open" / "u:clear" / "u:i:<n>" — the universe (grammar v11). The
    // switch target travels in the id as a roster INDEX, same pattern as
    // the messages/gallery apps (48-byte id cap; a 64-char slug would clip).
    if (id && id[0] == 'u' && id[1] == ':') return Act::kUniverse;
    // "ob:next" / "ob:back" / "ob:phone" / "ob:type" / "ob:start" — the
    // first-run flow (tiny_onboard). Routed the day it was born (same rule).
    if (id && id[0] == 'o' && id[1] == 'b' && id[2] == ':') return Act::kOnboard;
    if (id && !strcmp(id, "noop")) return Act::kNone;
    const char *sniff = (id && *id) ? id : label;
    {
        const char *s = sniff;
        if (!s || !*s) return Act::kNone;
        if (token_match(s, "ask") || token_match(s, "sor") ||
            token_match(s, "speak") || token_match(s, "konus"))
            return Act::kVoiceAsk;
        // [Type] on the home card — the on-glass agent composer.
        if (token_match(s, "type") || token_match(s, "yaz"))
            return Act::kTypeAsk;
        if (token_match(s, "status") || token_match(s, "durum"))
            return Act::kStatusCard;
        // "sensor"/"sensors" and the Turkish "sensör" (ASCII "sensor" prefix).
        if (token_match(s, "sensor") || token_match(s, "sens"))
            return Act::kSensorsCard;
        if (token_match(s, "home") || token_match(s, "ana ekran"))
            return Act::kHome;
        if (token_match(s, "settings") || token_match(s, "ayarlar"))
            return Act::kSettings;
        if (token_match(s, "gallery") || token_match(s, "galeri"))
            return Act::kGallery;
        // universe / agents — who answers the asks (grammar v11)
        if (token_match(s, "universe") || token_match(s, "agents") ||
            token_match(s, "evren"))
            return Act::kUniverse;
        if (token_match(s, "silent") || token_match(s, "sessiz"))
            return Act::kSilent;
        // [Config] on settings -> the scan-to-configure QR
        if (token_match(s, "config")) return Act::kConfigQr;
        if (token_match(s, "back") || token_match(s, "geri"))
            return Act::kBack;
        if (token_match(s, "rescan ble") || token_match(s, "bluetooth") ||
            token_match(s, "ble"))
            return Act::kBle;
        if (token_match(s, "rescan")) return Act::kRescanWifi;
        if (token_match(s, "wi-fi") || token_match(s, "wifi"))
            return Act::kWifi;
    }
    return Act::kNone;
}

void act_task(void *) {
    ActMsg m = {};
    while (true) {
        if (xQueueReceive(s_act_q, &m, portMAX_DELAY) != pdTRUE) continue;
        // Wedge watchdog stamp: the node task watches
        // this. A route that wedges the act task must never silence the whole
        // device — better one honest reboot than a brick nobody can reach.
        s_act_busy_since_us = esp_timer_get_time();
        ESP_LOGI(kTag, "action %d for button %d id=\"%s\"", (int)m.act, m.index,
                 m.id);
        // Record WHICH region this route serves, before it runs — a
        // receipt reader must never have to guess what a tap resolved to.
        strlcpy(s_route_id, m.id, sizeof s_route_id);
        strlcpy(s_route_label, m.label, sizeof s_route_label);
        s_route_result = ESP_OK;  // R-6b default: non-gesture routes' effect
                                  // IS the route body; kScroll overwrites.
        switch (m.act) {
            case Act::kVoiceAsk:
                // Reuses the AI-button worker: one ask in flight, ever.
                tiny_button_request_voice_ask();
                break;
            case Act::kTypeAsk:
                // Renders the keyboard immediately (no network) — which is
                // still an e-ink blocking render, hence off the touch task.
                tiny_shell_ask_compose();
                break;
            case Act::kStatusCard:
                tiny_shell_open(TINY_PAGE_STATUS);
                break;
            case Act::kSensorsCard:
                tiny_shell_open(TINY_PAGE_SENSORS);
                break;
            case Act::kHome:
                tiny_shell_home();
                break;
            case Act::kSettings:
                tiny_shell_open(TINY_PAGE_SETTINGS);
                break;
            case Act::kBack:
                tiny_shell_back();
                break;
            case Act::kWifi:
                tiny_shell_open(TINY_PAGE_WIFI);
                break;
            case Act::kConfigQr:
                tiny_shell_open(TINY_PAGE_CFGQR);
                break;
            case Act::kKey:
                tiny_shell_key(m.id);
                break;
            case Act::kMenu:
                tiny_shell_menu(m.id);
                break;
            case Act::kSilent:
                // Mute/unmute. Repaint IS the ack (UI_SPEC §7) —
                // no beep-on-mute by design.
                s_route_result = tiny_shell_silent_toggle();
                break;
            case Act::kGallery: {
                // Blocks on HTTPS fetch + e-ink refresh — belongs here, off
                // the touch task, like kMessages. Empty set answers with a
                // friendly card, not silence (D-UX law: never a dead tap).
                int dir = 0;
                if (!strncmp(m.id, "g:prev", 6)) dir = -1;
                else if (!strncmp(m.id, "g:next", 6)) dir = +1;
                esp_err_t r = tiny_display_gallery_nav(dir);
                if (r == ESP_ERR_INVALID_STATE)
                    r = tiny_display_render_card(
                        "{\"type\":\"text\",\"card_id\":\"gallery\","
                        "\"title\":\"gallery\",\"body\":\"No photos here yet."
                        " Send some from the tiny app on your phone \","
                        "\"buttons\":[{\"label\":\"home\",\"id\":\"home\"}]}");
                s_route_result = r;
                break;
            }
            case Act::kMessages:
                // Skips the "m:" prefix; blocks on HTTPS + a refresh, which is
                // exactly why this runs here and not on the touch task.
                tiny_node_messages_open(m.id + 2);
                break;
            case Act::kUniverse:
                // "u:*" ids or the "universe" token (no id ⇒ open). NVS write
                // + full e-ink repaint — belongs on this task like kMenu.
                s_route_result = tiny_shell_universe_route(
                    m.id[0] == 'u' && m.id[1] == ':' ? m.id : "u:open");
                break;
            case Act::kRescanWifi:
                tiny_shell_rescan_wifi();
                break;
            case Act::kOnboard:
                // Full e-ink repaint, maybe a scan — act task, like kMenu.
                s_route_result = tiny_onboard_route(m.id);
                break;
            case Act::kBle:
                tiny_shell_open(TINY_PAGE_BLE);
                break;
            case Act::kScroll: {
                // D-UX1: the router (tiny_display_scroll_gesture) owns ALL
                // gesture feedback — accept blips fire there, BEFORE the
                // blocking render, and dead-ends double-blip there. Beeping
                // here too would sound every accepted gesture twice.
                const esp_err_t r = tiny_display_scroll_gesture(m.x0, m.y0,
                                                                m.x1, m.y1);
                s_route_result = r;  // R-6b: the receipt reads this verdict
                ESP_LOGI(kTag, "swipe gesture (%d,%d)->(%d,%d): %s", m.x0, m.y0,
                         m.x1, m.y1, esp_err_to_name(r));
                break;
            }
            case Act::kNone:
            default:
                break;
        }
        s_act_busy_since_us = 0;  // route finished: feed the wedge watchdog
        ++s_route_seq;            // receipts wait on this (route-time truth)
    }
}

void queue_action(Act act, int index, const char *label, const char *id) {
    // kNone rides the queue too (2026-08-26 probe nit): an inert region —
    // noop id, or an id this build doesn't recognise — is still a HIT, and
    // the receipt rail must say so. Early-returning here left s_route_seq
    // unbumped, so `tap` replies claimed "nothing under tap" while the
    // ui_tap event proved the hit: two probes, two contradicting answers
    // about one tap. The act task's kNone arm does no work; it exists so
    // the receipt (region id/label, seq) is written on the same task that
    // writes every other route receipt.
    if (!s_act_q) return;
    ActMsg m = {};
    m.act = act;
    m.index = index;
    strlcpy(m.label, label ? label : "", sizeof m.label);
    strlcpy(m.id, id ? id : "", sizeof m.id);
    // No wait: if an action is already running, dropping the second tap is the
    // honest behaviour (double-press != two questions).
    if (xQueueSend(s_act_q, &m, 0) != pdTRUE)
        ESP_LOGW(kTag, "action queue full — tap %d dropped", index);
}

uint16_t scale_coord(uint16_t v, uint16_t in_max, uint16_t out_max) {
    const uint16_t c = std::min(v, in_max);
    return static_cast<uint16_t>(
        (static_cast<uint32_t>(c) * out_max + in_max / 2U) / in_max);
}

void transform(uint16_t tx, uint16_t ty, uint16_t &dx, uint16_t &dy) {
    // GT911 reports portrait coords; display layer rotates 180. Same math as
    // the vendor demo -> physical landscape screen coordinates.
    const uint16_t px = scale_coord(tx, kW, kPortraitW - 1U);
    const uint16_t my = std::min(ty, kH);
    const uint16_t py = scale_coord(static_cast<uint16_t>(kH - my), kH,
                                    kPortraitH - 1U);
    const uint16_t fx = kW - py - 1U;
    const uint16_t fy = px;
    dx = kW - fx - 1U;
    dy = kH - fy - 1U;
}

void on_tap_up() {
    const int adx = std::abs(static_cast<int>(s_last_x) - s_start_x);
    const int ady = std::abs(static_cast<int>(s_last_y) - s_start_y);
    if (adx > kTapSlop || ady > kTapSlop) {
        printf("UI_TAP swipe dx=%d dy=%d from=%u,%u\n",
               static_cast<int>(s_last_x) - s_start_x,
               static_cast<int>(s_last_y) - s_start_y, s_start_x, s_start_y);
        // Vertical swipe = scroll (M12). Handed to the action task with both
        // endpoints — the display layer maps them through the rotation and
        // ignores vectors that are not a vertical scroll. NEVER scrolled here:
        // a scroll re-render blocks ~400ms on the panel and this is the
        // sampling task.
        ActMsg m = {};
        m.act = Act::kScroll;
        m.x0 = s_start_x; m.y0 = s_start_y;
        m.x1 = (int)s_last_x; m.y1 = (int)s_last_y;
        if (s_act_q && xQueueSend(s_act_q, &m, 0) != pdTRUE)
            ESP_LOGW(kTag, "action queue full - swipe dropped");
        return;  // swipe/drag: not a tap
    }

    // The card must not have changed under the finger.
    const uint32_t gen_now = tiny_display_card_gen();
    if (gen_now != s_gen_at_down) {
        printf("UI_TAP dropped: card changed under finger (gen %lu->%lu)\n",
               (unsigned long)s_gen_at_down, (unsigned long)gen_now);
        return;
    }

    static tiny_button_region_t regions[48];  // touch task only; keyboard = 41
    const int n = tiny_display_button_regions(regions, 48);
    for (int i = 0; i < n; ++i) {
        const tiny_button_region_t &r = regions[i];
        // Hit-test the touch-DOWN point: fingers roll on lift, and iOS resolves
        // where you aimed, not where you left (audit TOUCH P1-2).
        if (s_start_x >= r.x && s_start_x < r.x + r.w &&
            s_start_y >= r.y && s_start_y < r.y + r.h) {
            const Act act = action_for(r.id, r.label);
            const bool is_key = act == Act::kKey;
            // 1. FEEL it — 60ms of buzzer, before anything that can block.
            sticky_buzzer_beep();
            // 2. SEE it — invert-ack via the display worker (~300-500ms).
            //    Keyboard keys skip it: the value line updating IS the ack,
            //    and a second partial per keystroke would halve typing speed.
            if (!is_key) tiny_display_ack_button(i);
            // 3. MEAN something — local action, then the upstream event.
            queue_action(act, i, r.label, r.id);
            if (is_key) return;  // PRIVACY: keystrokes never leave the device
                                 // (a password is typed one ui_tap at a time)

            printf("UI_TAP button=%d id=\"%s\" label=\"%s\" card=\"%s\" "
                   "at=%u,%u act=%d\n",
                   i, r.id, r.label, r.card_id, s_start_x, s_start_y, (int)act);
            ESP_LOGI(kTag, "tap on button %d \"%s\" (card %s)", i, r.label,
                     r.card_id);
            char detail[160];
            snprintf(detail, sizeof detail,
                     "ui_tap card_id=%s button_id=%s index=%d label=%s",
                     r.card_id[0] ? r.card_id : "-", r.id[0] ? r.id : "-", i,
                     r.label);
            tiny_node_post_event("device_note", detail);
            return;
        }
    }
    // A tap on empty canvas is still worth seeing at the bench: it proves the
    // sensor is alive even when the coordinates miss every button.
    printf("UI_TAP button=-1 at=%u,%u card=\"%s\" regions=%d\n", s_start_x,
           s_start_y, tiny_display_current_card_id(), n);
}

void touch_task(void *) {
    TickType_t next = xTaskGetTickCount();
    while (true) {
        GTPoint p = {};
        const int8_t count = s_ctl.read_points(&p, 1);
        const bool in_grace =
            (xTaskGetTickCount() - s_init_tick) < pdMS_TO_TICKS(kBootGraceMs);
        // §11 pocket lockout: PHYSICAL touch dies HERE, at the router — one
        // gate, not per-page deshielding. Synthetic taps (inject_tap/swipe)
        // bypass this task on purpose: relay verbs are the owner's remote
        // reach, which lockout does not guard. Logged on the transition only.
        if (tiny_lock_is_locked()) {
            if (count > 0 && !s_touching)
                ESP_LOGW(kTag, "locked: touch point discarded (§11)");
            s_touching = false;
            s_touch_streak = 0;
            vTaskDelayUntil(&next, kPoll);
            continue;
        }
        if (count > 0) {
            // Raw point log — the bench needs to see EVERY point the sensor
            // reports, button or not (mandate 23:35Z). Only on the transition,
            // so a held finger doesn't flood the console at 33Hz.
            if (!s_touching) {
                uint16_t lx = 0, ly = 0;
                transform(p.x, p.y, lx, ly);
                printf("GT911 raw=%u,%u size=%u id=%u -> screen=%u,%u%s\n", p.x,
                       p.y, p.size, p.id, lx, ly, in_grace ? " (boot grace)" : "");
            }
            const bool trusted = p.size >= kMinTrustedSize;
            if (in_grace) { s_touch_streak = 0; s_touching = false; }
            else if (trusted || ++s_touch_streak >= kDebouncePolls) {
                transform(p.x, p.y, s_last_x, s_last_y);
                if (!s_touching) {
                    s_start_x = s_last_x;
                    s_start_y = s_last_y;
                    s_gen_at_down = tiny_display_card_gen();
                    tiny_lock_note_activity();  // §11: a finger resets the idle clock
                }
                s_touching = true;
            }
        } else if (count == 0) {
            if (s_touching) on_tap_up();
            s_touching = false;
            s_touch_streak = 0;
        } else {
            s_touching = false;
            s_touch_streak = 0;
        }
        // A long stall (e-ink/i2c hiccup) would otherwise make DelayUntil fire
        // a catch-up burst of polls with no delay at all (audit TOUCH P2).
        if ((xTaskGetTickCount() - next) > pdMS_TO_TICKS(300))
            next = xTaskGetTickCount();
        vTaskDelayUntil(&next, kPoll);
    }
}

}  // namespace

// Synthetic tap at PANEL coordinates, routed through the very same resolver a
// finger uses (region hit-test, buzzer, invert-ack, action, upstream ui_tap).
// It exists so a rotation can be PROVEN over the relay: after `rotate 90`, a
// tap at the pixel where the screenshot shows a button must hit that button.
// Coordinates are panel-space on purpose — the same space as the screenshot.
extern "C" esp_err_t tiny_touch_inject_tap(int x, int y) {
    if (x < 0 || y < 0 || x >= 800 || y >= 480) return ESP_ERR_INVALID_ARG;
    s_start_x = static_cast<uint16_t>(x);
    s_start_y = static_cast<uint16_t>(y);
    s_last_x = s_start_x;
    s_last_y = s_start_y;
    s_gen_at_down = tiny_display_card_gen();
    on_tap_up();
    return ESP_OK;
}

// Synthetic swipe between two PANEL coordinates, through the same release
// classifier a finger uses (slop check in on_tap_up, then the scroll/gesture
// path). D-UX0: the designer loop gated every gesture in UX_SPEC §2 on this
// verb existing — a grammar nobody can drive over the relay is unverifiable,
// and unverifiable gestures pile up as unreviewable code. Endpoints whose
// travel is within tap slop are REFUSED, not reinterpreted as a tap: a caller
// who asked for a swipe and got a button press would blame the button.
extern "C" esp_err_t tiny_touch_inject_swipe(int x0, int y0, int x1, int y1) {
    if (x0 < 0 || y0 < 0 || x0 >= 800 || y0 >= 480 ||
        x1 < 0 || y1 < 0 || x1 >= 800 || y1 >= 480)
        return ESP_ERR_INVALID_ARG;
    if (std::abs(x1 - x0) <= kTapSlop && std::abs(y1 - y0) <= kTapSlop)
        return ESP_ERR_INVALID_SIZE;  // sub-slop travel: not a swipe, say so
    s_start_x = static_cast<uint16_t>(x0);
    s_start_y = static_cast<uint16_t>(y0);
    s_last_x = static_cast<uint16_t>(x1);
    s_last_y = static_cast<uint16_t>(y1);
    s_gen_at_down = tiny_display_card_gen();
    on_tap_up();
    return ESP_OK;
}

extern "C" esp_err_t tiny_touch_init(void) {
    gpio_config_t en = {};
    en.pin_bit_mask = 1ULL << PIN_TOUCH_EN;
    en.mode = GPIO_MODE_OUTPUT;
    ESP_RETURN_ON_ERROR(gpio_config(&en), kTag, "en gpio");
    ESP_RETURN_ON_ERROR(
        gpio_set_level(static_cast<gpio_num_t>(PIN_TOUCH_EN), 1), kTag, "en");
    vTaskDelay(pdMS_TO_TICKS(250));

    i2c_master_bus_config_t bus = {};
    bus.i2c_port = I2C_NUM_0;
    bus.sda_io_num = static_cast<gpio_num_t>(PIN_TOUCH_SDA);
    bus.scl_io_num = static_cast<gpio_num_t>(PIN_TOUCH_SCL);
    bus.clk_source = I2C_CLK_SRC_DEFAULT;
    bus.glitch_ignore_cnt = 7;
    bus.flags.enable_internal_pullup = 1;
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus, &s_bus), kTag, "i2c");

    if (!s_ctl.begin(PIN_TOUCH_INT, PIN_TOUCH_RST, kW, kH, s_bus)) {
        ESP_LOGE(kTag, "GT911 init failed");
        return ESP_FAIL;
    }
    uint16_t sw = 0, sh = 0;
    s_ctl.readResolution(sw, sh);
    ESP_LOGI(kTag, "GT911 ready: sensor=%ux%u addr=0x%02X", sw, sh,
             s_ctl.address());
    s_init_tick = xTaskGetTickCount();

    // Depth 1: one pending action. A second tap while an ask is running is
    // dropped, not queued.
    s_act_q = xQueueCreate(1, sizeof(ActMsg));
    if (!s_act_q) return ESP_ERR_NO_MEM;
    // 4096 is enough: the heavy work (record/upload/ask) happens on the
    // voice_ask worker; this task only dispatches.
    // 12 KB, not 4: kMessages does a TLS handshake + JSON parse + card render
    // on this stack (tiny_node runs on 8 KB for the same work, and mbedtls is
    // the hungry part). The other actions need far less; the ceiling rules.
    if (xTaskCreate(act_task, "tiny_act", 12288, nullptr, 4, nullptr) != pdPASS)
        return ESP_ERR_NO_MEM;

    // prio 6 — above the node task: sampling fingers must never wait on HTTP.
    return xTaskCreate(touch_task, "tiny_touch", 4096, nullptr, 6, &s_task) ==
                   pdPASS
               ? ESP_OK
               : ESP_ERR_NO_MEM;
}

extern "C" void tiny_touch_set_regions(const tiny_touch_region_t *, int) {
    // Regions come straight from tiny_display_button_regions() for now;
    // this API stays for M4 when cards carry explicit ids.
}
