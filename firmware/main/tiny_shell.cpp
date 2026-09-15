// tiny_shell — home screen + nav stack. See tiny_shell.h.
#include "tiny/tiny_shell.h"
#include "tiny/tiny_onboard.h"
#include "sticky_buzzer.h"
#include "tiny/tiny_config.h"
#include "tiny/tiny_orient.h"
#include "tiny/tiny_display.h"
#include "tiny/tiny_node.h"
#include "tiny/tiny_sensors.h"
#include "tiny/tiny_version.h"
#include "tiny/tiny_wifi.h"
#include "tiny/tiny_ble.h"
#include "tiny/tiny_button.h"
#include "tiny/tiny_agent.h"

#include "nvs.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include "cJSON.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_mac.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "tiny_shell";

// Nav stack. Small and honest: 8 levels is already 5 more than the page set
// can produce today. Guarded by a mutex — UP/DOWN keys and screen taps arrive
// from different tasks.
static tiny_page_t s_stack[8] = { TINY_PAGE_HOME };
static int s_depth = 1;
static SemaphoreHandle_t s_lock = NULL;

// ---------- page renderers ----------

static esp_err_t render_home(void) {
    // The bespoke agent-home surface (owner's design: "the home screen
    // MUST be the agent ui"). All geometry lives in the
    // renderer (tiny_display render_agent_home_card): corner glyphs
    // (settings / chats+unread), conversation canvas, bottom "Message tiny..."
    // input bar + mic. This function only supplies the data. Built with
    // cJSON, never snprintf: the preview is the MODEL's own words (wifi_join
    // audit rule).
    char preview[96] = "";
    const bool have_ans = tiny_node_last_answer(preview, sizeof preview) == ESP_OK;

    cJSON *card = cJSON_CreateObject();
    if (!card) return ESP_ERR_NO_MEM;
    cJSON_AddStringToObject(card, "type", "agent_home");
    cJSON_AddStringToObject(card, "card_id", "home");
    if (have_ans && preview[0])
        cJSON_AddStringToObject(card, "preview", preview);

    char *cs = cJSON_PrintUnformatted(card);
    cJSON_Delete(card);
    if (!cs) return ESP_ERR_NO_MEM;
    esp_err_t r = tiny_display_render_card(cs);
    free(cs);
    return r;
}

static esp_err_t render_settings(void) {
    // Root card is a read-only VITALS view, but the pages under it are live:
    // Wi-Fi join (scan + keyboard password) and Bluetooth scan shipped in
    // later. The body copy must say exactly that — review caught the old
    // "keyboard + wifi/ble pages are coming" line still on glass days after
    // they shipped: a panel that understates itself is lying in the humble
    // direction, which is still lying. Editable values (toggles, saved
    // networks with Forget) came later and the copy says "yet".
    tiny_config_t cfg = {};
    bool have_cfg = tiny_config_load(&cfg) == ESP_OK;
    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);

    // ssid/rssi via the status shape (single source of truth for vitals).
    char ssid[40] = "-";
    int rssi = 0;
    bool have_rssi = false;
    static char sj[1024];
    if (tiny_node_status_json(sj, sizeof sj) == ESP_OK) {
        cJSON *o = cJSON_Parse(sj);
        if (o) {
            const cJSON *s = cJSON_GetObjectItem(o, "ssid");
            const cJSON *r = cJSON_GetObjectItem(o, "rssi_dbm");
            if (cJSON_IsString(s)) snprintf(ssid, sizeof ssid, "%s", s->valuestring);
            if (cJSON_IsNumber(r)) { rssi = r->valueint; have_rssi = true; }
            cJSON_Delete(o);
        }
    }

    cJSON *card = cJSON_CreateObject();
    cJSON_AddStringToObject(card, "type", "composite");
    cJSON_AddStringToObject(card, "card_id", "settings");
    // The page title belongs to the FIXED title strip — inside
    // the parts array it scrolled away with the content and the glass lost
    // its page identity at offset>0 (§2: title strip = every page).
    cJSON_AddStringToObject(card, "title", "settings");
    cJSON *parts = cJSON_AddArrayToObject(card, "parts");
    cJSON *head = cJSON_CreateObject();
    cJSON_AddStringToObject(head, "type", "text");
    // Caption earns its space — <=2 lines before any data row hides.
    cJSON_AddStringToObject(head, "body",
        "Wi-Fi joins a network. Config = QR for phone setup.");
    cJSON_AddItemToArray(parts, head);

    // Silent mode: a real 64px tappable row, state visible in the
    // label, toggled by the "silent" route. Placed ABOVE the vitals so it is
    // never the row the overflow note swallows.
    {
        cJSON *mn = cJSON_CreateObject();
        cJSON_AddStringToObject(mn, "type", "menu");
        cJSON *items = cJSON_AddArrayToObject(mn, "items");
        cJSON *it = cJSON_CreateObject();
        const bool silent = sticky_buzzer_silent();
        cJSON_AddStringToObject(it, "label",
            silent ? "sound: SILENT" : "sound: ON");
        cJSON_AddStringToObject(it, "note",
            silent ? "tap to unmute" : "tap to mute beeps");
        cJSON_AddStringToObject(it, "id", "silent");
        cJSON_AddItemToArray(items, it);
        // Sounds: volume tier, directly under the mute row so the
        // two sound controls read as one group. While silent the row says so
        // instead of advertising a change you couldn't hear.
        cJSON *bv = cJSON_CreateObject();
        const bool soft = sticky_buzzer_soft();
        cJSON_AddStringToObject(bv, "label",
            soft ? "beeps: SOFT" : "beeps: NORMAL");
        cJSON_AddStringToObject(bv, "note",
            silent ? "muted - unmute to hear"
                   : (soft ? "tap for full volume" : "tap for soft"));
        cJSON_AddStringToObject(bv, "id", "beepvol");
        cJSON_AddItemToArray(items, bv);
        // Display section: rotation lock. Same menu block — the
        // two behavior toggles live together above the read-only vitals.
        cJSON *rt = cJSON_CreateObject();
        char rlab[36], rnote[36];
        if (tiny_orient_auto()) {
            snprintf(rlab, sizeof rlab, "rotation: AUTO");
            snprintf(rnote, sizeof rnote, "tap to lock at %d deg",
                     tiny_display_rotation());
        } else {
            snprintf(rlab, sizeof rlab, "rotation: LOCKED %d deg",
                     tiny_display_rotation());
            snprintf(rnote, sizeof rnote, "tap to follow gravity");
        }
        cJSON_AddStringToObject(rt, "label", rlab);
        cJSON_AddStringToObject(rt, "note", rnote);
        cJSON_AddStringToObject(rt, "id", "rotlock");
        cJSON_AddItemToArray(items, rt);
        // Autosleep idle budget. The note names the NEXT value —
        // a cycling control that hides its next stop is a roulette wheel.
        cJSON *sl = cJSON_CreateObject();
        const uint8_t mins = tiny_config_sleepidle_get();
        const uint8_t nxt = mins == 5 ? 15 : mins == 15 ? 30
                          : mins == 30 ? 60 : 5;
        char slab[36], snote[36];
        snprintf(slab, sizeof slab, "sleep after: %u min", mins);
        snprintf(snote, sizeof snote, "tap for %u min", nxt);
        cJSON_AddStringToObject(sl, "label", slab);
        cJSON_AddStringToObject(sl, "note", snote);
        cJSON_AddStringToObject(sl, "id", "sleepidle");
        cJSON_AddItemToArray(items, sl);
        // v11: the universe — who answers the asks. State visible in the
        // label (same law as the toggles above); tap opens the roster page.
        cJSON *uv = cJSON_CreateObject();
        const char *cur_agent = tiny_agent_current();
        char ulab[TINY_AGENT_SLUG_MAX + 16];
        snprintf(ulab, sizeof ulab, "agent: %s%s",
                 cur_agent[0] ? "@" : "", cur_agent[0] ? cur_agent : "tiny (yours)");
        cJSON_AddStringToObject(uv, "label", ulab);
        cJSON_AddStringToObject(uv, "note", "tap to switch agents");
        cJSON_AddStringToObject(uv, "id", "u:open");
        cJSON_AddItemToArray(items, uv);
        cJSON_AddItemToArray(parts, mn);
    }

    cJSON *kv = cJSON_CreateObject();
    cJSON_AddStringToObject(kv, "type", "kv");
    cJSON *rows = cJSON_AddArrayToObject(kv, "rows");
    char macs[20], rssis[56];
    snprintf(macs, sizeof macs, "%02x:%02x:%02x:%02x:%02x:%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    if (have_rssi) snprintf(rssis, sizeof rssis, "%s (%d dBm)", ssid, rssi);
    else           snprintf(rssis, sizeof rssis, "%s", ssid);
    char dev_short[16] = "-";
    if (have_cfg && cfg.device_id[0])
        snprintf(dev_short, sizeof dev_short, "%.8s...", cfg.device_id);
    const char *pairs[][2] = {
        { "name",    have_cfg && cfg.name[0] ? cfg.name : "sticky" },
        { "fw",      TINY_FW_VERSION },
        { "channel", "sticky-dev" },
        { "wifi",    rssis },
        { "mac",     macs },
        // The full 36-char id rendered at ~7px — unreadable
        // honesty. First octet + ellipsis here; the FULL id stays readable
        // on the config QR page (it is embedded in the QR payload/URL).
        { "device",  have_cfg && cfg.device_id[0] ? dev_short : "-" },
        // Quote the SYSTEM, not the spec — the cadence is
        // adaptive (5s active / 60s after 2 quiet min) and a fixed sentence
        // here was false roughly half the time. Same truth `status` reports
        // as poll_cadence. (When stop-when-locked lands, it speaks here too.)
        { "poll",    tiny_node_poll_idle() ? "relay 60s (idle) / hb 30s"
                                           : "relay 5s (active) / hb 30s" },
    };
    for (size_t i = 0; i < sizeof pairs / sizeof pairs[0]; ++i) {
        cJSON *row = cJSON_CreateArray();
        cJSON_AddItemToArray(row, cJSON_CreateString(pairs[i][0]));
        cJSON_AddItemToArray(row, cJSON_CreateString(pairs[i][1]));
        cJSON_AddItemToArray(rows, row);
    }
    cJSON_AddItemToArray(parts, kv);

    cJSON *btns = cJSON_AddArrayToObject(card, "buttons");
    cJSON_AddItemToArray(btns, cJSON_CreateString("Wi-Fi"));
    cJSON_AddItemToArray(btns, cJSON_CreateString("Bluetooth"));
    cJSON_AddItemToArray(btns, cJSON_CreateString("Config"));
    // Back CUT, not forgotten — settings is one hop from home,
    // so Back and Home resolved to the same journey and the bottom strip is
    // the scarcest real estate on the device (80px floor). Home stays
    // (matches the gesture vocabulary: bottom-band up = home). CONDITION:
    // if settings ever becomes reachable from anywhere but home, Back earns
    // this slot back.
    cJSON_AddItemToArray(btns, cJSON_CreateString("Home"));

    char *cs = cJSON_PrintUnformatted(card);
    cJSON_Delete(card);
    if (!cs) return ESP_ERR_NO_MEM;
    esp_err_t r = tiny_display_render_card(cs);
    free(cs);
    return r;
}

// ---------- Wi-Fi page + keyboard ----------
// Scan results live here between render and tap so "w:<idx>" can resolve.
// 6 rows predates the scroll rail — an office airspace hides the
// network you came for behind five stronger strangers. 12 scrollable rows,
// and the row after the last one says how many the window still cut.
static tiny_scan_ap_t s_aps[12];
static int s_ap_count = 0;
static int s_ap_total = 0;

// Keyboard editing state. One keyboard at a time (one panel, one human).
// The keyboard is a shared input surface with a PURPOSE — wifi password was
// its first customer, DM compose (gate 7) its second. The purpose decides
// what "ok"/"cancel" mean; the key handling itself never changes.
enum class KbFor { kWifi, kCompose, kAsk };
static KbFor s_kb_for = KbFor::kWifi;
static char s_kb_value[64] = "";
static char s_kb_ssid[33] = "";
// 48, matching the button-id contract in tiny_display.h (logins up to 39
// chars). At 32 this silently clipped past 31 characters — and this variable is
// the ADDRESS handed to tiny_node_messages_send(), so the failure was not a
// truncated label, it was a typed message delivered to a different account or
// to nobody. Filled by a checked copy in tiny_shell_compose().
static char s_kb_peer[48] = "";  // "@login" target when s_kb_for == kCompose
static bool s_kb_shift = false;
static bool s_kb_sym = false;   // v2: symbols plane (?123)
static bool s_kb_active = false;

static esp_err_t render_keyboard(void) {
    cJSON *card = cJSON_CreateObject();
    cJSON_AddStringToObject(card, "type", "keyboard");
    cJSON_AddStringToObject(card, "card_id", "kb");
    char title[64];
    if (s_kb_for == KbFor::kCompose)
        snprintf(title, sizeof title, "reply to @%s", s_kb_peer);
    else if (s_kb_for == KbFor::kAsk)
        snprintf(title, sizeof title, "ask tiny - type your question");
    else
        snprintf(title, sizeof title, "password for %s", s_kb_ssid);
    cJSON_AddStringToObject(card, "title", title);
    cJSON_AddStringToObject(card, "value", s_kb_value);
    cJSON_AddBoolToObject(card, "shift", s_kb_shift);
    cJSON_AddBoolToObject(card, "sym", s_kb_sym);
    char *cs = cJSON_PrintUnformatted(card);
    cJSON_Delete(card);
    if (!cs) return ESP_ERR_NO_MEM;
    esp_err_t r = tiny_display_render_card(cs);
    free(cs);
    return r;
}

static int64_t s_scan_at_us = 0;  // when s_aps was last filled

static esp_err_t render_wifi_ex(bool force) {
    // Page-flipping through wifi must not stall 2-4s every pass: a scan
    // younger than 60s renders from cache. Rescan
    // and a stale cache do the real thing, with feedback first — a button
    // that does nothing for 4 seconds reads as a broken button.
    const bool fresh = s_ap_count > 0 &&
                       (esp_timer_get_time() - s_scan_at_us) < 60LL * 1000000LL;
    esp_err_t serr = ESP_OK;
    if (force || !fresh) {
        tiny_display_render_card(
            "{\"type\":\"text\",\"card_id\":\"wifi\",\"title\":\"wi-fi\","
            "\"body\":\"scanning nearby networks...\"}");
        s_ap_count = 0;
        s_ap_total = 0;
        serr = tiny_wifi_scan(s_aps, 12, &s_ap_count, &s_ap_total);
        if (serr == ESP_OK && s_ap_count > 0) s_scan_at_us = esp_timer_get_time();
    }

    cJSON *card = cJSON_CreateObject();
    cJSON_AddStringToObject(card, "type", "menu");
    cJSON_AddStringToObject(card, "card_id", "wifi");
    cJSON_AddStringToObject(card, "title", "wi-fi - tap a network to join");
    cJSON *items = cJSON_AddArrayToObject(card, "items");
    if (serr != ESP_OK || s_ap_count == 0) {
        cJSON *it = cJSON_CreateObject();
        cJSON_AddStringToObject(it, "label", serr == ESP_OK ?
            "no networks found" : "scan unavailable");
        cJSON_AddStringToObject(it, "id", "noop");
        cJSON_AddItemToArray(items, it);
    }
    for (int i = 0; i < s_ap_count; ++i) {
        cJSON *it = cJSON_CreateObject();
        cJSON_AddStringToObject(it, "label", s_aps[i].ssid);
        char note[32], id[8];
        snprintf(note, sizeof note, "%d dBm%s", s_aps[i].rssi,
                 s_aps[i].open ? " open" : " *");
        snprintf(id, sizeof id, "w:%d", i);
        cJSON_AddStringToObject(it, "note", note);
        cJSON_AddStringToObject(it, "id", id);
        cJSON_AddItemToArray(items, it);
    }
    if (s_ap_total > s_ap_count) {
        // Honesty row, not a tap target: the cut networks are the weakest
        // ones, and Rescan re-ranks — say so instead of hiding the cut.
        cJSON *it = cJSON_CreateObject();
        char more[48];
        snprintf(more, sizeof more, "+%d more (weaker signal)",
                 s_ap_total - s_ap_count);
        cJSON_AddStringToObject(it, "label", more);
        cJSON_AddStringToObject(it, "note", "rescan re-ranks");
        cJSON_AddStringToObject(it, "id", "noop");
        cJSON_AddItemToArray(items, it);
    }
    // Saved networks — the roaming list the device actually walks,
    // each row a Forget affordance. ssid + "saved", dots never the password
    // — rows ride the same scroll rail as the scan list.
    {
        static tiny_config_t cfg;  // action-task only; too big for the stack
        if (tiny_config_load(&cfg) == ESP_OK && cfg.network_count > 0) {
            cJSON *sep = cJSON_CreateObject();
            char sect[40];
            snprintf(sect, sizeof sect, "saved networks (%d)", cfg.network_count);
            cJSON_AddStringToObject(sep, "label", sect);
            cJSON_AddStringToObject(sep, "note", "tap one to forget");
            cJSON_AddStringToObject(sep, "id", "noop");
            cJSON_AddItemToArray(items, sep);
            for (int i = 0; i < cfg.network_count; ++i) {
                cJSON *it = cJSON_CreateObject();
                cJSON_AddStringToObject(it, "label", cfg.networks[i].ssid);
                char note[24], id[10];
                snprintf(note, sizeof note, "saved%s",
                         cfg.networks[i].key[0] ? " *" : " open");
                snprintf(id, sizeof id, "wf:%d", i);
                cJSON_AddStringToObject(it, "note", note);
                cJSON_AddStringToObject(it, "id", id);
                cJSON_AddItemToArray(items, it);
            }
        }
    }
    cJSON *btns = cJSON_AddArrayToObject(card, "buttons");
    cJSON_AddItemToArray(btns, cJSON_CreateString("Rescan"));
    cJSON_AddItemToArray(btns, cJSON_CreateString("Back"));
    char *cs = cJSON_PrintUnformatted(card);
    cJSON_Delete(card);
    if (!cs) return ESP_ERR_NO_MEM;
    esp_err_t r = tiny_display_render_card(cs);
    free(cs);
    return r;
}

static esp_err_t render_wifi(void) { return render_wifi_ex(false); }

// Forget-per-row. The ssid held between the confirm card and the yes
// tap — the same render-to-tap statefulness as s_aps, same owner (action task).
static char s_forget_ssid[33] = "";

// Per-network actions card. Position honesty in the body ("2nd
// of 3 in the roaming walk") because move-up is meaningless without knowing
// where you stand; [Move up] offered only when a slot above exists — a
// button that can't do anything is a lie with a border.
static esp_err_t wifi_forget_confirm(const char *ssid, int pos, int total) {
    strlcpy(s_forget_ssid, ssid, sizeof s_forget_ssid);
    cJSON *c = cJSON_CreateObject();
    if (!c) return ESP_ERR_NO_MEM;
    cJSON_AddStringToObject(c, "type", "text");
    cJSON_AddStringToObject(c, "card_id", "wforget");
    cJSON_AddStringToObject(c, "title", ssid);
    char body[200];
    snprintf(body, sizeof body,
             "%d of %d in the roaming walk (boot tries them in order). "
             "forget removes it from the list - if you're connected to it "
             "now, the connection stays up until the next reboot.",
             pos + 1, total);
    cJSON_AddStringToObject(c, "body", body);
    cJSON *btns = cJSON_AddArrayToObject(c, "buttons");
    cJSON *b1 = cJSON_CreateObject();
    cJSON_AddStringToObject(b1, "label", "Forget");
    cJSON_AddStringToObject(b1, "id", "wf:yes");
    cJSON_AddItemToArray(btns, b1);
    if (pos > 0) {
        cJSON *bu = cJSON_CreateObject();
        cJSON_AddStringToObject(bu, "label", "Move up");
        cJSON_AddStringToObject(bu, "id", "wf:up");
        cJSON_AddItemToArray(btns, bu);
    }
    cJSON *b2 = cJSON_CreateObject();
    cJSON_AddStringToObject(b2, "label", "Cancel");
    cJSON_AddStringToObject(b2, "id", "wf:no");
    cJSON_AddItemToArray(btns, b2);
    char *cs = cJSON_PrintUnformatted(c);
    cJSON_Delete(c);
    if (!cs) return ESP_ERR_NO_MEM;
    esp_err_t r = tiny_display_render_card(cs);
    free(cs);
    return r;
}

// [Move up] tapped: one slot up, then straight back to the repainted list —
// the row visibly higher IS the receipt. Rail on refusal.
static esp_err_t wifi_promote_confirmed(void) {
    if (!s_forget_ssid[0]) return render_wifi_ex(false);
    const esp_err_t err = tiny_config_wifi_promote(s_forget_ssid);
    s_forget_ssid[0] = 0;
    if (err != ESP_OK)
        return tiny_display_render_card(
            "{\"type\":\"text\",\"card_id\":\"wforget\",\"title\":\"couldn't move it\","
            "\"body\":\"the settings store refused the write - the order is "
            "unchanged. try again in a moment.\","
            "\"buttons\":[{\"label\":\"Back\",\"id\":\"w:back\"}]}");
    return render_wifi_ex(false);
}

static esp_err_t wifi_forget_confirmed(void) {
    if (!s_forget_ssid[0]) return render_wifi_ex(false);
    // cJSON rule: the ssid is the human's bytes — never hand-built JSON.
    cJSON *root = cJSON_CreateObject();
    if (!root) return ESP_ERR_NO_MEM;
    cJSON *nets = cJSON_AddArrayToObject(root, "networks");
    cJSON *n = cJSON_CreateObject();
    cJSON_AddStringToObject(n, "ssid", s_forget_ssid);
    cJSON_AddBoolToObject(n, "forget", true);
    cJSON_AddItemToArray(nets, n);
    char *js = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!js) return ESP_ERR_NO_MEM;
    const esp_err_t err = tiny_config_merge_json(js);
    free(js);
    s_forget_ssid[0] = 0;
    if (err != ESP_OK) {
        // Rail: a failed forget must confess, not pretend. NVS said no;
        // the entry is still there and the list we repaint would prove it —
        // say it in words first.
        return tiny_display_render_card(
            "{\"type\":\"text\",\"card_id\":\"wforget\",\"title\":\"couldn't forget\","
            "\"body\":\"the settings store refused the write - the network is "
            "still saved. try again in a moment.\","
            "\"buttons\":[{\"label\":\"Back\",\"id\":\"w:back\"}]}");
    }
    // Success = the repainted list, with the row gone, is the receipt.
    return render_wifi_ex(false);
}

// BLE page: scan-only list, per the honesty rule — the page shows
// devices, it does not promise pairing. Rows are deliberately NOT tappable.
static esp_err_t render_ble(void) {
    tiny_display_render_card(
        "{\"type\":\"text\",\"card_id\":\"ble\",\"title\":\"bluetooth\","
        "\"body\":\"scanning nearby devices (4s)...\"}");
    tiny_ble_dev_t devs[6];
    int n = 0;
    esp_err_t serr = tiny_ble_scan(devs, 6, &n, 4000);

    cJSON *card = cJSON_CreateObject();
    cJSON_AddStringToObject(card, "type", "menu");
    cJSON_AddStringToObject(card, "card_id", "ble");
    cJSON_AddStringToObject(card, "title", "bluetooth - nearby (scan only)");
    cJSON *items = cJSON_AddArrayToObject(card, "items");
    if (serr != ESP_OK || n == 0) {
        cJSON *it = cJSON_CreateObject();
        cJSON_AddStringToObject(it, "label",
            serr == ESP_OK ? "nothing advertising nearby" : "BLE unavailable");
        cJSON_AddStringToObject(it, "id", "noop");
        cJSON_AddItemToArray(items, it);
    }
    for (int i = 0; i < n; ++i) {
        cJSON *it = cJSON_CreateObject();
        cJSON_AddStringToObject(it, "label",
                                devs[i].name[0] ? devs[i].name : devs[i].addr);
        char note[40];
        snprintf(note, sizeof note, "%d dBm%s", devs[i].rssi,
                 devs[i].name[0] ? "" : " (no name)");
        cJSON_AddStringToObject(it, "note", note);
        cJSON_AddStringToObject(it, "id", "noop");
        cJSON_AddItemToArray(items, it);
    }
    cJSON *btns = cJSON_AddArrayToObject(card, "buttons");
    cJSON_AddItemToArray(btns, cJSON_CreateString("Rescan BLE"));
    cJSON_AddItemToArray(btns, cJSON_CreateString("Back"));
    char *cs = cJSON_PrintUnformatted(card);
    cJSON_Delete(card);
    if (!cs) return ESP_ERR_NO_MEM;
    esp_err_t r = tiny_display_render_card(cs);
    free(cs);
    return r;
}

// Join = merge into the roaming list (upsert by ssid — tiny_config owns the
// rule) and reboot into the normal STA path. Honest and simple: live re-join
// without reboot is future work, and the card SAYS a reboot is coming.
static esp_err_t wifi_join(const char *ssid, const char *key) {
    cJSON *root = cJSON_CreateObject();
    cJSON *nets = cJSON_AddArrayToObject(root, "networks");
    cJSON *net = cJSON_CreateObject();
    cJSON_AddStringToObject(net, "ssid", ssid);
    cJSON_AddStringToObject(net, "key", key);
    cJSON_AddItemToArray(nets, net);
    char *js = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!js) return ESP_ERR_NO_MEM;
    esp_err_t r = tiny_config_merge_json(js);
    free(js);
    ESP_LOGI(TAG, "wifi join \"%s\" merge -> %s", ssid, esp_err_to_name(r));
    if (r == ESP_OK) {
        // Breadcrumb for the post-reboot boot walk. If THIS network
        // fails auth on the very next boot, the boot path reads this key and
        // tells the human the truth ("wrong password?") instead of letting
        // "saved ✓" stand as the last word. Consumed (erased) after one report.
        nvs_handle_t nh;
        if (nvs_open("tinywifi", NVS_READWRITE, &nh) == ESP_OK) {
            nvs_set_str(nh, "just_added", ssid);
            nvs_commit(nh);
            nvs_close(nh);
        }
        // cJSON, not snprintf. An SSID is arbitrary bytes chosen by whoever owns
        // the radio, and this card was pasting it raw into hand-built JSON. Two
        // exploits, attacker's choice, both inside the 32-byte SSID limit:
        //   x","buttons":["ask"],"q":"     -> 26 B. The card still parses, and it
        //     now carries a REAL button. draw_buttons stages a bare-string button
        //     with id = label, and action_for sniffs the label when no id is
        //     given: token_match("ask") is Act::kVoiceAsk. So a stranger's
        //     network name painted a microphone button on this device.
        //   a lone " or \ or a raw control byte -> cJSON_Parse fails, render_card
        //     returns INVALID_ARG (discarded here), and the panel keeps the
        //     password keyboard for 1.5 s and then reboots. The human never
        //     learns whether their network was saved.
        // Escaping closes both: the SSID lands inside one JSON string and stays
        // there. Bounded blast radius before (we reboot in 1.5 s) is not a
        // defence — the lie on the glass was the damage.
        cJSON *c = cJSON_CreateObject();
        char *cs = NULL;
        if (c) {
            cJSON_AddStringToObject(c, "type", "text");
            cJSON_AddStringToObject(c, "card_id", "joined");
            cJSON_AddStringToObject(c, "title", "saved");
            char line[160];
            snprintf(line, sizeof line,
                     "%.40s is in my roaming list now. Rebooting to join...", ssid);
            cJSON_AddStringToObject(c, "body", line);
            cs = cJSON_PrintUnformatted(c);
            cJSON_Delete(c);
        }
        // Never leave this moment silent: the network IS saved either way, and a
        // reboot follows in 1.5 s, so a blank panel would read as a crash.
        if (cs) {
            tiny_display_render_card(cs);
            free(cs);
        } else {
            tiny_display_render_card(
                "{\"type\":\"text\",\"card_id\":\"joined\",\"title\":\"saved\","
                "\"body\":\"network stored - rebooting to join...\"}");
        }
        vTaskDelay(pdMS_TO_TICKS(1500));
        esp_restart();
    } else {
        tiny_display_render_card(
            "{\"type\":\"text\",\"card_id\":\"joinfail\",\"title\":\"save failed\","
            "\"body\":\"I could not save this network - storage is full or the name is too long. try again from settings > wifi.\"}");
    }
    return r;
}

// Left-band swipe-right (UX_SPEC §2): BACK on a pushed card, ring-prev at a
// ring root. The depth read is a snapshot — worst case a race renders a page
// the finger is one gesture behind on, same exposure UP/DOWN already has.
esp_err_t tiny_shell_nav_back(void) {
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const bool pushed = s_depth > 1;
    xSemaphoreGive(s_lock);
    return pushed ? tiny_shell_back() : tiny_shell_cycle(-1);
}

esp_err_t tiny_shell_key(const char *id) {
    if (!s_kb_active || !id || strncmp(id, "k:", 2) != 0) return ESP_ERR_INVALID_STATE;
    const char *k = id + 2;
    size_t len = strlen(s_kb_value);
    if (!strcmp(k, "shift")) { s_kb_shift = !s_kb_shift; }
    else if (!strcmp(k, "sym")) { s_kb_sym = !s_kb_sym; s_kb_shift = false; }
    else if (!strcmp(k, "bksp")) { if (len) s_kb_value[len - 1] = 0; }
    else if (!strcmp(k, "space")) { if (len < sizeof s_kb_value - 1) { s_kb_value[len] = ' '; s_kb_value[len + 1] = 0; } }
    else if (!strcmp(k, "cancel")) {
        s_kb_active = false;
        if (s_kb_for == KbFor::kAsk) return tiny_shell_home();
        if (s_kb_for == KbFor::kCompose) {
            // 52: "t:" + a 48-byte peer. -Werror=format-truncation caught this
            // the moment s_kb_peer grew to hold a full-length login — the
            // compiler was right, and it is the same clipped-address bug one hop
            // downstream: cancelling a draft must return to the SAME thread.
            char t[52];
            snprintf(t, sizeof t, "t:%s", s_kb_peer);
            return tiny_node_messages_open(t);  // back to the thread, unsent
        }
        return render_wifi_ex(false);
    }
    else if (!strcmp(k, "ok")) {
        s_kb_active = false;
        if (s_kb_for == KbFor::kAsk) {
            if (!len) return tiny_shell_home();     // empty: never ask ""
            // Same single-flight door as the AI button — the worker streams
            // the answer onto the glass. The request copies the text, so the
            // keyboard buffer is reusable the moment this returns.
            esp_err_t ar = tiny_button_request_text_ask(s_kb_value);
            if (ar == ESP_OK) {
                // The worker takes seconds of HTTP before the first SSE delta
                // paints — leaving the keyboard on glass would read as a
                // dropped question. Echo it (cJSON: it is the human's bytes).
                cJSON *c = cJSON_CreateObject();
                char *cs = NULL;
                if (c) {
                    cJSON_AddStringToObject(c, "type", "text");
                    cJSON_AddStringToObject(c, "card_id", "ask");
                    cJSON_AddStringToObject(c, "title", "asking tiny");
                    char q[96];
                    snprintf(q, sizeof q, "you: %s", s_kb_value);
                    cJSON_AddStringToObject(c, "body", q);
                    cJSON_AddStringToObject(c, "footer", "thinking...");
                    cs = cJSON_PrintUnformatted(c);
                    cJSON_Delete(c);
                }
                esp_err_t r = ESP_ERR_NO_MEM;
                if (cs) { r = tiny_display_render_card(cs); free(cs); }
                return r;
            }
            // Refused: say why instead of silently eating the question.
            return tiny_display_render_card(
                "{\"type\":\"text\",\"card_id\":\"askbusy\",\"title\":\"one at a time\","
                "\"body\":\"an ask is already in flight - wait for the answer, then try again\"}");
        }
        if (s_kb_for == KbFor::kCompose) {
            if (!len) {  // empty body: treat as cancel, never POST ""
                char t[52];  // see the cancel branch above
                snprintf(t, sizeof t, "t:%s", s_kb_peer);
                return tiny_node_messages_open(t);
            }
            return tiny_node_messages_send(s_kb_peer, s_kb_value);
        }
        return wifi_join(s_kb_ssid, s_kb_value);
    }
    else if (strlen(k) == 1) {
        if (len < sizeof s_kb_value - 1) { s_kb_value[len] = k[0]; s_kb_value[len + 1] = 0; }
        s_kb_shift = false;  // shift is one-shot, like a phone
    } else return ESP_ERR_INVALID_ARG;
    return render_keyboard();  // same card_id -> partial refresh path
}

esp_err_t tiny_shell_rescan_wifi(void) { return render_wifi_ex(true); }

// ---- THE UNIVERSE (grammar v11) --------------------------------------------
// Who answers the asks. A composite card: caption + menu of the roster with
// the active agent marked. Row ids carry roster INDICES (u:i:<n>) because a
// 64-char slug would clip in the 48-byte region id; the card is rebuilt from
// the same roster on every render, so index→slug is stable between paint and
// tap (single-writer: switches repaint immediately).
static esp_err_t render_universe(void) {
    const char *cur = tiny_agent_current();
    char roster[TINY_AGENT_ROSTER_MAX][TINY_AGENT_SLUG_MAX + 1];
    const int n = tiny_agent_roster(roster, TINY_AGENT_ROSTER_MAX);

    cJSON *card = cJSON_CreateObject();
    if (!card) return ESP_ERR_NO_MEM;
    cJSON_AddStringToObject(card, "type", "composite");
    cJSON_AddStringToObject(card, "card_id", "universe");
    // The title strip carries WHO IS ANSWERING — page identity + the owner
    // rule ("never mistake a persona's words for your own tiny's") in one.
    char title[80];
    snprintf(title, sizeof title, "universe%s%s",
             cur[0] ? " - @" : "", cur[0] ? cur : "");
    cJSON_AddStringToObject(card, "title", title);
    cJSON *parts = cJSON_AddArrayToObject(card, "parts");
    cJSON *head = cJSON_CreateObject();
    cJSON_AddStringToObject(head, "type", "text");
    cJSON_AddStringToObject(head, "body",
        "Who answers your asks. Tap a row to switch - it sticks until you "
        "switch back.");
    cJSON_AddItemToArray(parts, head);

    cJSON *mn = cJSON_CreateObject();
    cJSON_AddStringToObject(mn, "type", "menu");
    cJSON *items = cJSON_AddArrayToObject(mn, "items");
    {   // row 0, always: the way home. Never needs typing (tiny_agent law:
        // the roster may drop entries, this row may not).
        cJSON *it = cJSON_CreateObject();
        cJSON_AddStringToObject(it, "label", "tiny (yours)");
        cJSON_AddStringToObject(it, "note",
                                cur[0] ? "tap to switch back" : "ACTIVE");
        cJSON_AddStringToObject(it, "id", "u:clear");
        cJSON_AddItemToArray(items, it);
    }
    for (int i = 0; i < n; ++i) {
        cJSON *it = cJSON_CreateObject();
        char lab[TINY_AGENT_SLUG_MAX + 2], id[16];
        snprintf(lab, sizeof lab, "@%s", roster[i]);
        snprintf(id, sizeof id, "u:i:%d", i);
        const bool active = cur[0] && strcmp(cur, roster[i]) == 0;
        cJSON_AddStringToObject(it, "label", lab);
        cJSON_AddStringToObject(it, "note",
                                active ? "ACTIVE" : "public tiny");
        cJSON_AddStringToObject(it, "id", id);
        cJSON_AddItemToArray(items, it);
    }
    {   // plain home row — universe is reachable from settings AND relay,
        // so the way out must live on the card itself (D-UX: no dead ends).
        cJSON *it = cJSON_CreateObject();
        cJSON_AddStringToObject(it, "label", "home");
        cJSON_AddStringToObject(it, "note", "leave this page");
        cJSON_AddStringToObject(it, "id", "home");
        cJSON_AddItemToArray(items, it);
    }
    cJSON_AddItemToArray(parts, mn);

    char *cs = cJSON_PrintUnformatted(card);
    cJSON_Delete(card);
    if (!cs) return ESP_ERR_NO_MEM;
    esp_err_t r = tiny_display_render_card(cs);
    free(cs);
    return r;
}

esp_err_t tiny_shell_universe_route(const char *id) {
    if (!id) return ESP_ERR_INVALID_ARG;
    if (strcmp(id, "u:open") == 0) return render_universe();
    if (strcmp(id, "u:clear") == 0) {
        tiny_agent_set(NULL);
        return render_universe();  // repaint = ack (ACTIVE moves to row 0)
    }
    if (strncmp(id, "u:i:", 4) == 0) {
        // Discipline: a non-numeric tail must refuse, not atoi()-to-0.
        const char *t = id + 4;
        if (!*t || strspn(t, "0123456789") != strlen(t))
            return ESP_ERR_INVALID_ARG;
        const int want = atoi(t);
        char roster[TINY_AGENT_ROSTER_MAX][TINY_AGENT_SLUG_MAX + 1];
        const int n = tiny_agent_roster(roster, TINY_AGENT_ROSTER_MAX);
        if (want < 0 || want >= n) return ESP_ERR_INVALID_ARG;
        esp_err_t r = tiny_agent_set(roster[want]);
        if (r != ESP_OK) return r;
        return render_universe();
    }
    return ESP_ERR_INVALID_ARG;
}

// Relay/iOS roster push + page open: render_ui {"type":"universe",
// "tinys":["slug",…]?}. Slugs merge into the roster (invalid ones are
// skipped, honestly logged by tiny_agent), then the page renders.
esp_err_t tiny_shell_universe_push(const char *spec_json) {
    cJSON *o = spec_json ? cJSON_Parse(spec_json) : NULL;
    if (o) {
        const cJSON *arr = cJSON_GetObjectItem(o, "tinys");
        const cJSON *e = NULL;
        cJSON_ArrayForEach(e, arr)
            if (cJSON_IsString(e)) tiny_agent_roster_add(e->valuestring);
        cJSON_Delete(o);
    }
    return render_universe();
}


// Silent-mode toggle. Route: settings menu row id "silent".
// Order matters: persist + flip FIRST, then repaint (the repainted row shows
// the new state = the visual ack). Unmuting confirms audibly — that single
// beep is the proof the speaker path still works; muting must stay silent.
esp_err_t tiny_shell_silent_toggle(void) {
    const bool now_silent = !sticky_buzzer_silent();
    sticky_buzzer_set_silent(now_silent);
    tiny_config_silent_set(now_silent);
    if (!now_silent) sticky_buzzer_beep();
    return render_settings();
}

esp_err_t tiny_shell_menu(const char *id) {
    if (!id) return ESP_ERR_INVALID_ARG;
    if (id[0] == 'w' && id[1] == ':') {
        // A w: id whose tail is not a number is NOT a network row.
        // atoi() answers 0 for "home", which used to mean "join the first AP
        // in the scan list" — a silent join to a stranger's network. Refuse
        // loudly instead: an unrouted tap is still reported upstream as
        // ui_tap, so the agent can answer for it.
        if (id[2] < '0' || id[2] > '9') {
            ESP_LOGW(TAG, "menu id \"%s\" is not a network row (w:<index>) — "
                          "refusing to join any AP for it", id);
            return ESP_ERR_INVALID_ARG;
        }
        int i = atoi(id + 2);
        if (i < 0 || i >= s_ap_count) return ESP_ERR_INVALID_ARG;
        if (s_aps[i].open) return wifi_join(s_aps[i].ssid, "");
        strlcpy(s_kb_ssid, s_aps[i].ssid, sizeof s_kb_ssid);
        s_kb_value[0] = 0;
        s_kb_shift = false;
        s_kb_sym = false;
        s_kb_for = KbFor::kWifi;
        s_kb_active = true;
        return render_keyboard();
    }
    // "wf:<idx>" = forget row; "wf:yes"/"wf:no" = the confirm card.
    if (id[0] == 'w' && id[1] == 'f' && id[2] == ':') {
        const char *t = id + 3;
        if (!strcmp(t, "yes")) return wifi_forget_confirmed();
        if (!strcmp(t, "up")) return wifi_promote_confirmed();
        if (!strcmp(t, "no")) { s_forget_ssid[0] = 0; return render_wifi_ex(false); }
        // Armor, same law as w:<idx>: non-numeric tails are refused,
        // never atoi'd into row 0.
        if (t[0] < '0' || t[0] > '9') {
            ESP_LOGW(TAG, "menu id \"%s\" is not a saved-network row - refusing", id);
            return ESP_ERR_INVALID_ARG;
        }
        static tiny_config_t cfg;  // action-task only
        if (tiny_config_load(&cfg) != ESP_OK) return ESP_FAIL;
        int i = atoi(t);
        if (i < 0 || i >= cfg.network_count) return ESP_ERR_INVALID_ARG;
        return wifi_forget_confirm(cfg.networks[i].ssid, i, cfg.network_count);
    }
    if (!strcmp(id, "w:back")) return render_wifi_ex(false);
    // Rotation lock toggle. Persist FIRST, then repaint — the
    // repainted row showing the new state is the ack (same law as silent).
    // Sounds: volume tier. The repaint is the visible ack; the
    // DEMO BEEP at the new volume is the audible one — a volume control that
    // doesn't let you hear the result makes you test it on the next error.
    // While silent the beep gates to nothing (§7 funnel) — row copy covers it.
    if (!strcmp(id, "beepvol")) {
        const bool now_soft = !sticky_buzzer_soft();
        sticky_buzzer_set_soft(now_soft);
        const esp_err_t perr = tiny_config_beepsoft_set(now_soft);
        if (perr != ESP_OK)
            ESP_LOGW(TAG, "beepvol persisted=NO (%s) - holds until reboot only",
                     esp_err_to_name(perr));
        sticky_buzzer_beep();  // hear the tier you just chose
        return render_settings();
    }
    // Sleep-idle cycle 5→15→30→60→5. Persist first, apply to the
    // live budget second, repaint third — the row showing the new value and
    // its next stop is the ack.
    if (!strcmp(id, "sleepidle")) {
        const uint8_t mins = tiny_config_sleepidle_get();
        const uint8_t nxt = mins == 5 ? 15 : mins == 15 ? 30
                          : mins == 30 ? 60 : 5;
        const esp_err_t perr = tiny_config_sleepidle_set(nxt);
        tiny_node_sleep_idle_set(nxt);
        if (perr != ESP_OK)
            ESP_LOGW(TAG, "sleepidle persisted=NO (%s) - %u min holds until "
                          "reboot only", esp_err_to_name(perr), nxt);
        return render_settings();
    }
    if (!strcmp(id, "rotlock")) {
        const bool now_auto = !tiny_orient_auto();
        tiny_orient_set_auto(now_auto);
        const esp_err_t perr = tiny_config_rotauto_set(now_auto);
        if (perr != ESP_OK)
            ESP_LOGW(TAG, "rotlock persisted=NO (%s) - toggle holds until "
                          "reboot only", esp_err_to_name(perr));
        return render_settings();
    }
    return ESP_OK;  // "noop" and future menus
}

// Open the keyboard as a DM composer targeting @login. Called by the messages
// app ("Reply" tap) off the touch task; renders immediately, no network.
// Compose cap = the keyboard buffer (63 chars) — pocket replies are short, and
// the send handler 400s over the platform's 2000-cp cap long before we can
// type it at 2 keys/sec. Documented debt: grow with the UTF-8 font work.
esp_err_t tiny_shell_compose(const char *login) {
    if (!login || !*login) return ESP_ERR_INVALID_ARG;
    // Refuse to open a composer we cannot address correctly. Sending to a
    // clipped login is worse than not offering to send: the human watches their
    // words go somewhere and has no way to know it was the wrong somewhere.
    if (strlcpy(s_kb_peer, login, sizeof s_kb_peer) >= sizeof s_kb_peer) {
        s_kb_peer[0] = 0;
        ESP_LOGE(TAG, "compose target %d B exceeds %u — refusing to open a "
                      "composer aimed at a clipped login",
                 (int)strlen(login), (unsigned)sizeof s_kb_peer);
        return ESP_ERR_INVALID_SIZE;
    }
    s_kb_value[0] = 0;
    s_kb_shift = false;
    s_kb_sym = false;
    s_kb_for = KbFor::kCompose;
    s_kb_active = true;
    return render_keyboard();
}

// Open the keyboard as the agent composer ([Type] on the home card).
// Renders immediately, no network; "ok" hands the text to the single-flight
// ask worker (tiny_button_request_text_ask) and the answer streams.
esp_err_t tiny_shell_ask_compose(void) {
    s_kb_value[0] = 0;
    s_kb_shift = false;
    s_kb_sym = false;
    s_kb_for = KbFor::kAsk;
    s_kb_active = true;
    return render_keyboard();
}

// Config QR: one glance-able QR to the dashboard's
// scan-to-configure page (/s/<device_id>). The phone does the typing;
// the device only has to say WHO it is. URL carries no secret — the page
// itself is WebAuthn-gated, so a stranger scanning the glass gets a login
// wall, not a control panel.
static esp_err_t render_cfgqr(void) {
    tiny_config_t cfg = {};
    const bool have = tiny_config_load(&cfg) == ESP_OK && cfg.device_id[0];
    cJSON *card = cJSON_CreateObject();
    if (!card) return ESP_ERR_NO_MEM;
    cJSON_AddStringToObject(card, "type", "qr");
    cJSON_AddStringToObject(card, "card_id", "cfgqr");
    cJSON_AddStringToObject(card, "title", "configure");
    char url[128];
    snprintf(url, sizeof url, CONFIG_TINY_DASHBOARD_URL "/s/%s",
             have ? cfg.device_id : "unknown");
    cJSON_AddStringToObject(card, "text", url);
    cJSON_AddStringToObject(card, "caption",
        have ? "scan with your phone - wifi, settings, toggles"
             : "device not enrolled yet - QR points nowhere useful");
    cJSON *btns = cJSON_AddArrayToObject(card, "buttons");
    cJSON_AddItemToArray(btns, cJSON_CreateString("Back"));
    cJSON_AddItemToArray(btns, cJSON_CreateString("Home"));
    char *cs = cJSON_PrintUnformatted(card);
    cJSON_Delete(card);
    if (!cs) return ESP_ERR_NO_MEM;
    esp_err_t r = tiny_display_render_card(cs);
    free(cs);
    return r;
}

static esp_err_t render_page(tiny_page_t p) {
    switch (p) {
        case TINY_PAGE_HOME:     return render_home();
        case TINY_PAGE_STATUS:   return tiny_node_render_status_card();
        case TINY_PAGE_SENSORS:  return tiny_sensors_render_card();
        case TINY_PAGE_SETTINGS: return render_settings();
        case TINY_PAGE_WIFI:     return render_wifi();
        case TINY_PAGE_BLE:      return render_ble();
        case TINY_PAGE_CFGQR:    return render_cfgqr();
        case TINY_PAGE_ONBOARD:  return tiny_onboard_render();
        default:                 return ESP_ERR_INVALID_ARG;
    }
}

// ---------- nav ----------

static tiny_page_t top_locked(void) { return s_stack[s_depth - 1]; }

esp_err_t tiny_shell_open(tiny_page_t p) {
    if (p < 0 || p >= TINY_PAGE_COUNT) return ESP_ERR_INVALID_ARG;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (top_locked() != p) {
        if (s_depth < (int)(sizeof s_stack / sizeof *s_stack)) s_stack[s_depth++] = p;
        else s_stack[s_depth - 1] = p;  // full: replace top, never wedge
    }
    xSemaphoreGive(s_lock);
    ESP_LOGI(TAG, "open page %d (depth %d)", (int)p, s_depth);
    return render_page(p);
}

esp_err_t tiny_shell_back(void) {
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_depth > 1) --s_depth;
    tiny_page_t p = top_locked();
    xSemaphoreGive(s_lock);
    ESP_LOGI(TAG, "back -> page %d (depth %d)", (int)p, s_depth);
    return render_page(p);
}

esp_err_t tiny_shell_home(void) {
    // First run: "home" is the onboarding flow until the device is paired and
    // the tour dismissed — an agent home with no agent behind it would be a
    // message bar that answers nothing. Long-press AI / bottom-edge swipe
    // land here too, so the human can never escape into a dead surface.
    const bool onboarding = tiny_onboard_active();
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_depth = 1;
    s_stack[0] = onboarding ? TINY_PAGE_ONBOARD : TINY_PAGE_HOME;
    xSemaphoreGive(s_lock);
    ESP_LOGI(TAG, "home%s", onboarding ? " -> onboarding" : "");
    return onboarding ? tiny_onboard_render() : render_home();
}

esp_err_t tiny_shell_cycle(int dir) {
    xSemaphoreTake(s_lock, portMAX_DELAY);
    tiny_page_t cur = top_locked();
    // P0 fix: the RING is the first four pages only —
    // home/status/sensors/settings, exactly the contract in tiny_shell.h.
    // The enum grew past it (WIFI, BLE) and this modulus silently
    // grew the ring with it: a ring-next from a pushed wifi card landed in
    // render_ble() — an unrequested 4s radio bring-up + scan + two blocking
    // renders on the act task, on glass the owner never asked to scan. Drill
    // pages are reachable by tap ONLY; the ring never visits them, and
    // cycling from one is the caller's dead-end to announce.
    static_assert(TINY_PAGE_WIFI == 4 && TINY_PAGE_COUNT == 8,
                  "ring math assumes drill pages sit after the 4 roots");
    const int kRing = (int)TINY_PAGE_WIFI;  // ring size = pages before WIFI
    if (cur == TINY_PAGE_ONBOARD) {
        // UP/DOWN inside the flow = next/back, the same routes the buttons take.
        xSemaphoreGive(s_lock);
        return tiny_onboard_route(dir >= 0 ? "ob:next" : "ob:back");
    }
    if ((int)cur >= kRing) {
        xSemaphoreGive(s_lock);
        return ESP_ERR_NOT_ALLOWED;  // pushed-only page: not on the ring
    }
    tiny_page_t next = (tiny_page_t)(((int)cur + (dir >= 0 ? 1 : kRing - 1))
                                     % kRing);
    // cycling REPLACES the top (it is paging, not drilling) — back still
    // returns to where you were before you started flipping.
    s_stack[s_depth - 1] = next;
    xSemaphoreGive(s_lock);
    ESP_LOGI(TAG, "cycle %+d -> page %d", dir, (int)next);
    return render_page(next);
}

// True while the visible page is a ring root (depth 1 AND a ring page).
// The gesture router uses this to route §2's pushed-card row: mid-content
// horizontal on a pushed card is a DEAD-END, never a ring hop.
extern "C" bool tiny_shell_at_root(void) {
    if (!s_lock) return true;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const bool root = s_depth <= 1 && ((int)top_locked() < (int)TINY_PAGE_WIFI ||
                                       top_locked() == TINY_PAGE_ONBOARD);
    xSemaphoreGive(s_lock);
    return root;
}

esp_err_t tiny_shell_init(void) {
    if (!s_lock) s_lock = xSemaphoreCreateMutex();
    if (!s_lock) return ESP_ERR_NO_MEM;
    return tiny_shell_home();
}
