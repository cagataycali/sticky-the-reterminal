// tiny_onboard — first-run flow. See tiny/tiny_onboard.h for the state model.
//
// Design notes (owner session 2026-09-15, "first boot experience is critical"):
//   * Phone-first Wi-Fi: the QR to the softAP portal is the primary path, the
//     on-glass scan+keyboard is one tap away ([type it here]). Both were
//     already built; this page just puts them side by side.
//   * The step is DERIVED from what the device lacks, never stored as a
//     cursor: a reboot after a Wi-Fi join lands on "link"/"pair" by itself,
//     and a device provisioned over serial skips straight to "ready".
//   * Copy is the device's voice (first person, short, honest about what it
//     needs). Numbers are real: the SSID, the failure reason, the IP.
//   * cJSON everywhere a human/network string enters a card (SSID injection,
//     0.15.1). No snprintf into JSON.
#include "tiny/tiny_onboard.h"
#include "tiny/tiny_config.h"
#include "tiny/tiny_display.h"
#include "tiny/tiny_provision.h"
#include "tiny/tiny_shell.h"
#include "tiny/tiny_wifi.h"
#include "tiny/tiny_version.h"
#include "sticky_battery.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "nvs.h"

static const char *TAG = "tiny_onboard";
#define OB_NS  "tinywifi"   // shares the breadcrumb namespace; one more u8
#define OB_KEY "ob_done"

// Per-boot state (RAM): welcome is shown once per cold boot without networks;
// the wifi page remembers whether the human chose phone or glass.
static bool s_welcome_seen = false;
static bool s_link_up = false;
static char s_link_ssid[33] = "";
static int  s_link_reason = 0;
static bool s_link_known = false;   // note_link() has run this boot

static bool ob_done_flag(void) {
    nvs_handle_t h;
    uint8_t v = 0;
    if (nvs_open(OB_NS, NVS_READONLY, &h) != ESP_OK) return false;
    nvs_get_u8(h, OB_KEY, &v);
    nvs_close(h);
    return v == 1;
}

extern "C" esp_err_t tiny_onboard_mark_done(void) {
    nvs_handle_t h;
    ESP_RETURN_ON_ERROR(nvs_open(OB_NS, NVS_READWRITE, &h), TAG, "open");
    esp_err_t e = nvs_set_u8(h, OB_KEY, 1);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    ESP_LOGI(TAG, "tour dismissed -> %s", esp_err_to_name(e));
    return e;
}

extern "C" const char *tiny_onboard_step_name(tiny_ob_step_t s) {
    switch (s) {
        case TINY_OB_WELCOME: return "welcome";
        case TINY_OB_WIFI:    return "wifi";
        case TINY_OB_LINK:    return "link";
        case TINY_OB_PAIR:    return "pair";
        case TINY_OB_READY:   return "ready";
        default:              return "done";
    }
}

extern "C" tiny_ob_step_t tiny_onboard_step(void) {
    static tiny_config_t cfg;   // 1 KB+; never on the caller's stack
    const bool have = tiny_config_load(&cfg) == ESP_OK;
    const bool nets = have && cfg.network_count > 0;
    const bool prov = have && tiny_config_is_provisioned(&cfg);
    if (!nets) return s_welcome_seen ? TINY_OB_WIFI : TINY_OB_WELCOME;
    if (!prov) return tiny_wifi_is_up() ? TINY_OB_PAIR : TINY_OB_LINK;
    if (!ob_done_flag()) return TINY_OB_READY;
    return TINY_OB_DONE;
}

extern "C" bool tiny_onboard_active(void) {
    return tiny_onboard_step() != TINY_OB_DONE;
}

extern "C" void tiny_onboard_note_link(bool up, const char *just_added, int reason) {
    s_link_known = true;
    s_link_up = up;
    s_link_reason = reason;
    if (just_added) strlcpy(s_link_ssid, just_added, sizeof s_link_ssid);
    else s_link_ssid[0] = 0;
}

// ---------- card helpers ----------

static cJSON *part_text(const char *title, const char *body) {
    cJSON *p = cJSON_CreateObject();
    cJSON_AddStringToObject(p, "type", "text");
    if (title) cJSON_AddStringToObject(p, "title", title);
    cJSON_AddStringToObject(p, "body", body);
    return p;
}

static cJSON *part_list(const char *const *items, int n) {
    cJSON *p = cJSON_CreateObject();
    cJSON_AddStringToObject(p, "type", "list");
    cJSON *arr = cJSON_AddArrayToObject(p, "items");
    for (int i = 0; i < n; ++i) cJSON_AddItemToArray(arr, cJSON_CreateString(items[i]));
    return p;
}

static void add_button(cJSON *btns, const char *label, const char *id) {
    cJSON *b = cJSON_CreateObject();
    cJSON_AddStringToObject(b, "label", label);
    cJSON_AddStringToObject(b, "id", id);
    cJSON_AddItemToArray(btns, b);
}

static esp_err_t paint(cJSON *card) {
    char *cs = cJSON_PrintUnformatted(card);
    cJSON_Delete(card);
    if (!cs) return ESP_ERR_NO_MEM;
    esp_err_t r = tiny_display_render_card(cs);
    free(cs);
    return r;
}

static cJSON *composite(const char *card_id, const char *title) {
    cJSON *c = cJSON_CreateObject();
    cJSON_AddStringToObject(c, "type", "composite");
    cJSON_AddStringToObject(c, "card_id", card_id);
    cJSON_AddStringToObject(c, "title", title);
    return c;
}

// ---------- pages ----------

static esp_err_t render_welcome(void) {
    s_welcome_seen = true;
    cJSON *c = composite("ob-welcome", "hi, i'm sticky");
    cJSON *parts = cJSON_AddArrayToObject(c, "parts");
    char body[200];
    int pct = -1;
    BatteryReading batt = {};
    if (sticky_battery_read(batt) == ESP_OK && batt.percent > 0) pct = batt.percent;
    snprintf(body, sizeof body,
             "I'm the e-ink body of your tiny. Three short steps and I'm yours: "
             "Wi-Fi, pairing, a 20-second tour.%s%d%%%s",
             pct >= 0 ? " Battery " : "", pct >= 0 ? pct : 0,
             pct >= 0 ? "." : "");
    if (pct < 0) snprintf(body, sizeof body,
             "I'm the e-ink body of your tiny. Three short steps and I'm yours: "
             "Wi-Fi, pairing, a 20-second tour.");
    cJSON_AddItemToArray(parts, part_text(NULL, body));
    static const char *const items[] = {
        "top buttons: AI (left) - hold to talk to me",
        "UP / DOWN - flip pages, or tap Next below",
        "the glass is touch - tap what you read",
    };
    cJSON_AddItemToArray(parts, part_list(items, 3));
    cJSON *btns = cJSON_AddArrayToObject(c, "buttons");
    add_button(btns, "Next", "ob:next");
    return paint(c);
}

static esp_err_t render_wifi(void) {
    const char *ssid = tiny_provision_ssid();
    cJSON *c = composite("ob-wifi", "1/3 - wi-fi");
    cJSON *parts = cJSON_AddArrayToObject(c, "parts");
    // Two lines, not three: every body line costs the QR ~38 px of height
    // (3 lines left it at the 3 px/module floor — measured on glass).
    cJSON_AddItemToArray(parts, part_text(NULL,
        "Scan with your phone camera, then open "
        "http://192.168.4.1 and pick your Wi-Fi."));
    // QR last: WIFI: payload joins the AP in one scan on iOS/Android.
    cJSON *qr = cJSON_CreateObject();
    cJSON_AddStringToObject(qr, "type", "qr");
    char payload[96], cap[96];
    snprintf(payload, sizeof payload, "WIFI:S:%s;T:WPA;P:%s;;", ssid, tiny_provision_key());
    snprintf(cap, sizeof cap, "no camera? join  %s  / pw  %s", ssid, tiny_provision_key());
    cJSON_AddStringToObject(qr, "text", payload);
    cJSON_AddStringToObject(qr, "caption", cap);
    cJSON_AddItemToArray(parts, qr);
    cJSON *btns = cJSON_AddArrayToObject(c, "buttons");
    add_button(btns, "Back", "ob:back");
    add_button(btns, "Type it here", "ob:type");
    return paint(c);
}

static esp_err_t render_link(void) {
    static tiny_config_t cfg;
    tiny_config_load(&cfg);
    const char *ssid = s_link_ssid[0] ? s_link_ssid
                     : (cfg.network_count > 0 ? cfg.networks[0].ssid : "your network");
    cJSON *c = composite("ob-link", "1/3 - wi-fi");
    cJSON *parts = cJSON_AddArrayToObject(c, "parts");
    cJSON *btns = cJSON_AddArrayToObject(c, "buttons");
    if (!s_link_known) {
        // Painted before the boot walk finishes: say so, don't pretend.
        cJSON *p = cJSON_CreateObject();
        cJSON_AddStringToObject(p, "type", "text");
        char b[120];
        snprintf(b, sizeof b, "joining %.32s...", ssid);
        cJSON_AddStringToObject(p, "body", b);
        cJSON_AddItemToArray(parts, p);
        add_button(btns, "Use my phone", "ob:phone");
        return paint(c);
    }
    // Known failure. Same reason families as the boot-time join-failure confession.
    const bool range = (s_link_reason == 200 || s_link_reason == 201);
    const bool auth  = (s_link_reason == 2 || s_link_reason == 15 ||
                        s_link_reason == 202 || s_link_reason == 204 ||
                        s_link_reason == 205);
    cJSON *p = cJSON_CreateObject();
    cJSON_AddStringToObject(p, "type", "text");
    cJSON_AddStringToObject(p, "title", "couldn't join");
    char b[200];
    if (range)
        snprintf(b, sizeof b, "I can't see %.32s from here. Move closer, or pick another network.", ssid);
    else if (auth)
        snprintf(b, sizeof b, "%.32s didn't accept the password. Wrong password? Try again.", ssid);
    else if (s_link_reason)
        snprintf(b, sizeof b, "couldn't join %.32s (reason %d). Try again or pick another network.", ssid, s_link_reason);
    else
        snprintf(b, sizeof b, "I'm not online yet. I keep retrying %.32s every 30 s.", ssid);
    cJSON_AddStringToObject(p, "body", b);
    cJSON_AddItemToArray(parts, p);
    add_button(btns, "Use my phone", "ob:phone");
    add_button(btns, "Pick a network", "ob:type");
    return paint(c);
}

static void sta_ip(char *out, size_t cap) {
    out[0] = 0;
    esp_netif_t *n = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    esp_netif_ip_info_t ip = {};
    if (n && esp_netif_get_ip_info(n, &ip) == ESP_OK && ip.ip.addr)
        snprintf(out, cap, IPSTR, IP2STR(&ip.ip));
}

// Phase A pair page: the portal, over APSTA, with a /pair form. Phase B
// replaces the body + QR with a pairing code (RFC 8628 style); the surface
// and the [Back]/[Use my phone] contract stay.
static esp_err_t render_pair(void) {
    static tiny_config_t cfg;
    tiny_config_load(&cfg);
    const char *ssid = tiny_provision_ssid();
    char ip[20];
    sta_ip(ip, sizeof ip);
    cJSON *c = composite("ob-pair", "2/3 - pair me to your tiny");
    cJSON *parts = cJSON_AddArrayToObject(c, "parts");
    {
        // Two lines (QR budget, see render_wifi). The network and IP live on
        // the status page; this page has one job.
        cJSON *p = cJSON_CreateObject();
        cJSON_AddStringToObject(p, "type", "text");
        char b[160];
        snprintf(b, sizeof b,
                 "Online on %.24s. Add a device at tiny.technology, "
                 "then scan, open 192.168.4.1/pair, paste.",
                 cfg.network_count > 0 ? cfg.networks[0].ssid : "wi-fi");
        (void)ip;
        cJSON_AddStringToObject(p, "body", b);
        cJSON_AddItemToArray(parts, p);
    }
    cJSON *qr = cJSON_CreateObject();
    cJSON_AddStringToObject(qr, "type", "qr");
    char payload[96], cap[80];
    snprintf(payload, sizeof payload, "WIFI:S:%s;T:WPA;P:%s;;", ssid, tiny_provision_key());
    snprintf(cap, sizeof cap, "%s / %s - setup portal", ssid, tiny_provision_key());
    cJSON_AddStringToObject(qr, "text", payload);
    cJSON_AddStringToObject(qr, "caption", cap);
    cJSON_AddItemToArray(parts, qr);
    cJSON *btns = cJSON_AddArrayToObject(c, "buttons");
    add_button(btns, "Back", "ob:back");
    return paint(c);
}

static esp_err_t render_ready(void) {
    static tiny_config_t cfg;
    tiny_config_load(&cfg);
    cJSON *c = composite("ob-ready", "3/3 - you're set");
    cJSON *parts = cJSON_AddArrayToObject(c, "parts");
    // One screen, no scroll: a tour that needs a scrollbar is a manual.
    // Lines are sized to the body width (~46 glyphs) — measured on glass.
    char b[120];
    snprintf(b, sizeof b, "I'm %s, paired and online. How I work:",
             cfg.name[0] ? cfg.name : "sticky");
    cJSON *p = cJSON_CreateObject();
    cJSON_AddStringToObject(p, "type", "text");
    cJSON_AddStringToObject(p, "body", b);
    cJSON_AddItemToArray(parts, p);
    static const char *const items[] = {
        "hold the AI button 1 s, talk - I answer here",
        "UP / DOWN: home, status, sensors, settings",
        "swipe up from the bottom edge = home",
        "pocket-locked? hold UP + DOWN for 1 s",
    };
    cJSON_AddItemToArray(parts, part_list(items, 4));
    cJSON *btns = cJSON_AddArrayToObject(c, "buttons");
    add_button(btns, "Start", "ob:start");
    return paint(c);
}

extern "C" esp_err_t tiny_onboard_render(void) {
    const tiny_ob_step_t s = tiny_onboard_step();
    ESP_LOGI(TAG, "render step %s", tiny_onboard_step_name(s));
    switch (s) {
        case TINY_OB_WELCOME: return render_welcome();
        case TINY_OB_WIFI:    return render_wifi();
        case TINY_OB_LINK:    return render_link();
        case TINY_OB_PAIR:    return render_pair();
        case TINY_OB_READY:   return render_ready();
        default:              return tiny_shell_home();
    }
}

extern "C" esp_err_t tiny_onboard_preview(const char *step_name) {
    if (!step_name || !*step_name) return tiny_onboard_render();
    if (!strncmp(step_name, "welcome", 7)) return render_welcome();
    if (!strncmp(step_name, "wifi", 4))    return render_wifi();
    if (!strncmp(step_name, "link", 4))    return render_link();
    if (!strncmp(step_name, "pair", 4))    return render_pair();
    if (!strncmp(step_name, "ready", 5))   return render_ready();
    return ESP_ERR_INVALID_ARG;
}

extern "C" esp_err_t tiny_onboard_route(const char *id) {
    if (!id || strncmp(id, "ob:", 3) != 0) return ESP_ERR_INVALID_ARG;
    const char *a = id + 3;
    const tiny_ob_step_t s = tiny_onboard_step();
    ESP_LOGI(TAG, "route %s at %s", a, tiny_onboard_step_name(s));
    if (!strcmp(a, "next")) {
        if (s == TINY_OB_WELCOME) { s_welcome_seen = true; return render_wifi(); }
        if (s == TINY_OB_READY)   return tiny_onboard_route("ob:start");
        return tiny_onboard_render();  // wifi/link/pair advance by themselves
    }
    if (!strcmp(a, "back")) {
        if (s == TINY_OB_WIFI) { s_welcome_seen = false; return render_welcome(); }
        if (s == TINY_OB_PAIR || s == TINY_OB_LINK) {
            // Back from pairing = change the network: the shell's wifi page,
            // which lists saved networks with Forget rows.
            return tiny_shell_open(TINY_PAGE_WIFI);
        }
        return tiny_onboard_render();
    }
    // "phone": show the portal QR for whatever this step needs — the pair page
    // carries its own QR; every earlier step uses the wifi page's.
    if (!strcmp(a, "phone")) return s == TINY_OB_PAIR ? render_pair() : render_wifi();
    if (!strcmp(a, "type") || !strcmp(a, "retry")) {
        // The shell's scan list + keyboard. Its [Back] pops to us.
        return tiny_shell_open(TINY_PAGE_WIFI);
    }
    if (!strcmp(a, "start")) {
        tiny_onboard_mark_done();
        return tiny_shell_home();
    }
    return ESP_ERR_INVALID_ARG;
}
