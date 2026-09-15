// tiny_provision — first-boot softAP portal, Nicla-compatible so the tiny iOS
// app onboards unchanged (contract: ~/strands-nicla/firmware/tiny_provision.py).
//
//   AP  "tiny-XXXX" (WPA2, key "tinysetup"), 192.168.4.1
//   GET  /       minimal HTML form (a human with any phone browser)
//   GET  /info   identity JSON so the app can recognize the device
//   POST /setup  JSON body (the app: device_id, token, networks[]) OR
//                urlencoded form (the human: ssid/key/device_id/token)
//                -> tiny_config_merge_json -> reply -> reboot into node mode
//
// XXXX = FNV-1a over the full 6-byte base MAC. The Nicla lesson transfers:
// short suffixes of a factory-sequential id collide within a lot, and two APs
// with one name is an owner who cannot tell the app which device to set up.
// FNV-1a moves when any byte moves.
//
// The e-ink shows a scannable WIFI: QR (any phone camera joins the AP in one
// scan — the qr card type shipped in 0.23.x, paying this file's original IOU)
// plus the manual steps for a camera-less human. The qr part sits LAST in the
// composite because it consumes all remaining rows; render_qr_card degrades
// honestly (prints the payload) if a future layout squeezes it below 3
// px/module.
#include "tiny/tiny_provision.h"
#include "tiny/tiny_config.h"
#include "cJSON.h"
#include "tiny/tiny_display.h"
#include "tiny/tiny_version.h"
#include "tiny/tiny_onboard.h"
#include "tiny/tiny_wifi.h"

#include <string.h>
#include <stdio.h>
#include <stdlib.h>

#include "esp_check.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_wifi.h"

static const char *TAG = "tiny_provision";
#define AP_KEY "tinysetup"

static char s_ssid[16];  // "tiny-xxxx"
static void make_ssid(void);
extern "C" const char *tiny_provision_ssid(void) { if (!s_ssid[0]) make_ssid(); return s_ssid; }
extern "C" const char *tiny_provision_key(void)  { return AP_KEY; }

static void make_ssid(void) {
    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
    uint32_t h = 0x811C9DC5;
    for (int i = 0; i < 6; ++i) { h ^= mac[i]; h *= 0x01000193; }
    snprintf(s_ssid, sizeof s_ssid, "tiny-%04x", (unsigned)(h & 0xFFFF));
}

// ---------- handlers ----------

static esp_err_t info_get(httpd_req_t *req) {
    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    tiny_config_t cfg;
    bool prov = tiny_config_load(&cfg) == ESP_OK && tiny_config_is_provisioned(&cfg);
    char body[320];
    snprintf(body, sizeof body,
             "{\"device\":\"reterminal_sticky\",\"board\":\"XIAO_ESP32S3_STICKY\","
             "\"uid\":\"%02x%02x%02x%02x%02x%02x\",\"ssid\":\"%s\",\"fw\":\"%s\","
             "\"capabilities\":[\"display\",\"touch\",\"mic\",\"buzzer\",\"sensors\","
             "\"wifi\"],\"provisioned\":%s}",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5], s_ssid,
             TINY_FW_VERSION, prov ? "true" : "false");
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, body, HTTPD_RESP_USE_STRLEN);
}

// Two forms, one page. Wi-Fi alone is enough to move the device to the
// "pair" step; pairing alone is enough on a device that is already online.
// /pair is the same page scrolled to the second form (the onboarding pair
// card links it). Scanned networks feed a <datalist> so a phone user picks
// instead of typing an SSID — scanning works because the portal runs APSTA.
static esp_err_t root_get(httpd_req_t *req) {
    static const char head[] =
        "<!doctype html><html><head><meta name=viewport "
        "content=\"width=device-width,initial-scale=1\"><title>tiny setup</title>"
        "<style>*{box-sizing:border-box}body{font-family:-apple-system,sans-serif;max-width:24em;"
        "margin:2em auto;padding:0 1em;color:#111}h2{margin:.2em 0}h3{margin:1.6em 0 .4em}"
        "input{width:100%;padding:.6em;margin:.25em 0 1em;box-sizing:border-box;"
        "font-size:1em}button{padding:.7em 2em;font-size:1em}small{color:#555}"
        "</style></head><body><h2>sticky &mdash; setup</h2>"
        "<small>fw " TINY_FW_VERSION "</small>"
        "<h3>1 &middot; Wi-Fi</h3><form method=POST action=/setup>"
        "<label>network</label><input name=ssid list=aps required autocomplete=off>"
        "<datalist id=aps>";
    static const char mid[] =
        "</datalist><label>password</label><input name=key type=password>"
        "<button>Save Wi-Fi</button></form>"
        "<h3 id=pair>2 &middot; Pair to your tiny</h3>"
        "<small>On tiny.technology &rsaquo; devices, add a device and copy its "
        "id and token. The token is shown once.</small>"
        "<form method=POST action=/setup>"
        "<label>device id</label><input name=device_id required autocomplete=off>"
        "<label>device token (tind_&hellip;)</label><input name=token required autocomplete=off>"
        "<button>Pair</button></form></body></html>";
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    httpd_resp_send_chunk(req, head, HTTPD_RESP_USE_STRLEN);
    // Scan is best-effort and bounded by the driver; an empty list leaves a
    // plain text field, which still works.
    static tiny_scan_ap_t aps[12];
    int n = 0, total = 0;
    if (tiny_wifi_scan(aps, 12, &n, &total) == ESP_OK) {
        for (int i = 0; i < n; ++i) {
            // HTML-escape the SSID: it is 32 arbitrary bytes from a stranger.
            char esc[33 * 6 + 1];
            size_t o = 0;
            for (const char *p = aps[i].ssid; *p && o + 7 < sizeof esc; ++p) {
                switch (*p) {
                    case '&': o += snprintf(esc + o, sizeof esc - o, "&amp;"); break;
                    case '<': o += snprintf(esc + o, sizeof esc - o, "&lt;"); break;
                    case '>': o += snprintf(esc + o, sizeof esc - o, "&gt;"); break;
                    case '"': o += snprintf(esc + o, sizeof esc - o, "&quot;"); break;
                    default:  esc[o++] = *p; esc[o] = 0;
                }
            }
            esc[o] = 0;
            char row[33 * 6 + 32];
            snprintf(row, sizeof row, "<option value=\"%s\">", esc);
            httpd_resp_send_chunk(req, row, HTTPD_RESP_USE_STRLEN);
        }
    }
    httpd_resp_send_chunk(req, mid, HTTPD_RESP_USE_STRLEN);
    return httpd_resp_send_chunk(req, NULL, 0);
}

// %xx + '+' decode, in place. A WiFi password is free text a person typed.
static void url_decode(char *s) {
    char *o = s;
    for (; *s; ++s, ++o) {
        if (*s == '+') { *o = ' '; continue; }
        if (*s == '%' && s[1] && s[2]) {
            char hex[3] = { s[1], s[2], 0 };
            *o = (char)strtol(hex, NULL, 16);
            s += 2;
        } else *o = *s;
    }
    *o = 0;
}

// urlencoded form -> the same JSON the app would have sent, so
// tiny_config_merge_json stays the single owner of merge semantics.
static esp_err_t form_to_json(char *body, char *out, size_t cap) {
    char ssid[64] = "", key[64] = "", device_id[48] = "", token[80] = "";
    for (char *tok = strtok(body, "&"); tok; tok = strtok(NULL, "&")) {
        char *eq = strchr(tok, '=');
        if (!eq) continue;
        *eq = 0;
        char *val = eq + 1;
        url_decode(val);
        if      (!strcmp(tok, "ssid"))      snprintf(ssid, sizeof ssid, "%s", val);
        else if (!strcmp(tok, "key"))       snprintf(key, sizeof key, "%s", val);
        else if (!strcmp(tok, "device_id")) snprintf(device_id, sizeof device_id, "%s", val);
        else if (!strcmp(tok, "token"))     snprintf(token, sizeof token, "%s", val);
    }
    // Either form alone is a valid step: Wi-Fi moves the device to "pair",
    // identity completes a device that is already online. Both at once is the
    // app's JSON path, not this form.
    const bool have_wifi = ssid[0] != 0;
    const bool have_id   = device_id[0] && token[0];
    if (!have_wifi && !have_id) return ESP_ERR_INVALID_ARG;
    // The note that used to sit here said the values are pasted verbatim because
    // "a token/ssid cannot contain '\"' per their own grammars". That premise is
    // false: an 802.11 SSID is up to 32 ARBITRARY bytes, quotes and backslashes
    // included, and hostile network names exist. This is the same mistake
    // wifi_join made in tiny_shell until 0.15.1, where a crafted SSID painted a
    // live microphone button — but the blast radius here is larger, because this
    // string goes to tiny_config_merge_json and lands in NVS. An SSID of
    //     x"}],"networks":[{"ssid":"attacker-ap","key":"hunter2
    // closes the object early and injects a SECOND networks[] entry: the
    // attacker's access point saved as a roaming target beside the owner's, on a
    // device whose whole job is to hold a credential and phone home. cJSON
    // escapes the bytes instead of trusting a grammar that was never a grammar.
    cJSON *j = cJSON_CreateObject();
    if (!j) return ESP_ERR_NO_MEM;
    bool ok = true;
    if (have_id)
        ok = cJSON_AddStringToObject(j, "device_id", device_id) &&
             cJSON_AddStringToObject(j, "token", token);
    if (ok && have_wifi) {
        cJSON *nets = cJSON_AddArrayToObject(j, "networks");
        cJSON *net  = nets ? cJSON_CreateObject() : NULL;
        // key may legitimately be empty (open network) — add it either way so
        // the merge sees an explicit value rather than inheriting a stale one.
        ok = net && cJSON_AddStringToObject(net, "ssid", ssid) &&
             cJSON_AddStringToObject(net, "key", key);
        if (ok) cJSON_AddItemToArray(nets, net);
        else if (net) cJSON_Delete(net);
    }
    char *js = ok ? cJSON_PrintUnformatted(j) : NULL;
    cJSON_Delete(j);
    if (!js) return ESP_ERR_NO_MEM;
    // Length-check the ESCAPED result, not the inputs: escaping can double a
    // string's length, so the only honest measurement is of what will be sent.
    const size_t n = strlen(js);
    if (n >= cap) {
        ESP_LOGE(TAG, "escaped setup JSON is %u B, buffer is %u B — refusing the "
                      "form rather than storing a truncated config",
                 (unsigned)n, (unsigned)cap);
        free(js);
        return ESP_ERR_NO_MEM;
    }
    memcpy(out, js, n + 1);
    free(js);
    return ESP_OK;
}

static esp_err_t setup_post(httpd_req_t *req) {
    static char body[2048];  // portal is single-user; static beats stack here
    int total = req->content_len;
    if (total <= 0 || total >= (int)sizeof body) {
        httpd_resp_set_status(req, "400 Bad Request");
        return httpd_resp_send(req, "{\"ok\":false,\"error\":\"bad length\"}",
                               HTTPD_RESP_USE_STRLEN);
    }
    int got = 0;
    while (got < total) {
        int r = httpd_req_recv(req, body + got, total - got);
        if (r <= 0) return ESP_FAIL;
        got += r;
    }
    body[total] = 0;

    esp_err_t err;
    static char json[2048];
    const char *payload = body;
    if (body[0] != '{') {                       // urlencoded form path
        err = form_to_json(body, json, sizeof json);
        if (err != ESP_OK) {
            httpd_resp_set_status(req, "400 Bad Request");
            return httpd_resp_send(req,
                "{\"ok\":false,\"error\":\"need ssid, or device_id+token\"}",
                HTTPD_RESP_USE_STRLEN);
        }
        payload = json;
    }
    err = tiny_config_merge_json(payload);
    ESP_LOGI(TAG, "setup: merge -> %s", esp_err_to_name(err));
    if (err != ESP_OK) {
        httpd_resp_set_status(req, "400 Bad Request");
        return httpd_resp_send(req, "{\"ok\":false,\"error\":\"merge failed\"}",
                               HTTPD_RESP_USE_STRLEN);
    }
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, "{\"ok\":true,\"reboot\":true}", HTTPD_RESP_USE_STRLEN);

    {
        static tiny_config_t cfg;
        const bool prov = tiny_config_load(&cfg) == ESP_OK && tiny_config_is_provisioned(&cfg);
        tiny_display_render_card(prov
            ? "{\"type\":\"text\",\"title\":\"paired\",\"card_id\":\"provisioned\","
              "\"body\":\"Identity saved. Rebooting to say hello to your tiny...\"}"
            : "{\"type\":\"text\",\"title\":\"got it\",\"card_id\":\"provisioned\","
              "\"body\":\"Wi-Fi saved. Rebooting to join it...\"}");
    }
    vTaskDelay(pdMS_TO_TICKS(1200));  // let the 200 flush + panel settle
    esp_restart();
    return ESP_OK;  // unreached
}

// ---------- entry ----------

// Shared tail: AP config + httpd + handler registration. Both entries funnel
// here; the difference is only who owns Wi-Fi init (first-boot: us, fully;
// rescue: the STA stack already running — we merely widen it to APSTA).
static esp_err_t portal_up(void) {
    wifi_config_t ap = {};
    snprintf((char *)ap.ap.ssid, sizeof ap.ap.ssid, "%s", s_ssid);
    ap.ap.ssid_len = strlen(s_ssid);
    snprintf((char *)ap.ap.password, sizeof ap.ap.password, "%s", AP_KEY);
    ap.ap.channel = 1;
    ap.ap.max_connection = 4;
    ap.ap.authmode = WIFI_AUTH_WPA2_PSK;
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_AP, &ap), TAG, "config");

    httpd_config_t hc = HTTPD_DEFAULT_CONFIG();
    hc.server_port = 80;
    hc.lru_purge_enable = true;
    // 8 KB, not the 4 KB default: POST /setup runs tiny_config_merge_json on
    // this task, which holds two tiny_config_t (2 x ~1064 B) plus cJSON and
    // the NVS read on the stack. The 0.28.0 first-boot test overflowed the
    // httpd task on the pair form ("***ERROR*** A stack overflow in task
    // httpd") and the identity never reached NVS. Same figure the node task
    // uses for the identical merge (config verb).
    hc.stack_size = 8192;
    httpd_handle_t srv = NULL;
    ESP_RETURN_ON_ERROR(httpd_start(&srv, &hc), TAG, "httpd");
    static const httpd_uri_t u_root  = { "/",      HTTP_GET,  root_get,  NULL };
    static const httpd_uri_t u_info  = { "/info",  HTTP_GET,  info_get,  NULL };
    static const httpd_uri_t u_setup = { "/setup", HTTP_POST, setup_post, NULL };
    httpd_register_uri_handler(srv, &u_root);
    httpd_register_uri_handler(srv, &u_info);
    httpd_register_uri_handler(srv, &u_setup);
    return ESP_OK;
}

esp_err_t tiny_provision_start(void) {
    make_ssid();

    // Own Wi-Fi init. Safe by construction: main only calls this when
    // network_count == 0, the exact case tiny_wifi_connect refuses, so the
    // two inits never race. APSTA, not AP: the on-glass Wi-Fi page scans
    // through the STA interface while a phone talks to the AP.
    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "netif");
    esp_err_t el = esp_event_loop_create_default();
    if (el != ESP_OK && el != ESP_ERR_INVALID_STATE) return el;  // may exist
    esp_netif_create_default_wifi_ap();
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&init), TAG, "init");
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_APSTA), TAG, "mode");
    ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "start");
    ESP_RETURN_ON_ERROR(portal_up(), TAG, "portal");

    // The glass belongs to the onboarding flow (tiny_onboard renders the same
    // QR on its wi-fi page); painting here would fight it. The pre-0.28 card
    // lived here — its copy moved to tiny_onboard.cpp render_wifi().
    ESP_LOGI(TAG, "portal up (APSTA): ssid=%s key=" AP_KEY " ip=192.168.4.1", s_ssid);
    return ESP_OK;  // httpd owns its own task; serial console stays usable
}

// Portal over a live STA, no card: onboarding's link/pair steps keep the
// phone path open while the shell owns the glass. Idempotent.
extern "C" esp_err_t tiny_provision_portal_apsta(void) {
    static bool s_up = false;
    if (s_up) return ESP_OK;
    make_ssid();
    static esp_netif_t *s_ap_netif = NULL;
    if (!s_ap_netif) s_ap_netif = esp_netif_create_default_wifi_ap();
    if (!s_ap_netif) return ESP_FAIL;
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_APSTA), TAG, "apsta");
    ESP_RETURN_ON_ERROR(portal_up(), TAG, "portal");
    s_up = true;
    ESP_LOGI(TAG, "portal up over STA (APSTA): ssid=%s ip=192.168.4.1", s_ssid);
    return ESP_OK;
}

// Rescue portal — the missing half of the NET-P0-1 auth-halt policy. That
// hotfix stopped a 401 from wiping NVS, but its "re-provision me" card sent
// the owner to the platform to re-issue a token WITH NO PATH for the new
// token to reach the device short of a serial cable. This opens that path:
// widen the already-running STA stack to APSTA (no re-init — the full-init
// entry above would double-init and abort), raise the same portal, same
// /setup -> tiny_config_merge_json -> reboot contract, same iOS onboarding.
// STA stays associated: home Wi-Fi keeps retrying the token every 5 min, so
// if the 401s were a backend blip that clears, the device heals itself and
// the portal simply goes unused until the next reboot.
extern "C" esp_err_t tiny_provision_start_rescue(void) {
    static bool s_up = false;
    if (s_up) return ESP_OK;  // idempotent: auth-halt may re-enter
    make_ssid();
    // Create the AP netif exactly once; STA netif already exists.
    static esp_netif_t *s_ap_netif = NULL;
    if (!s_ap_netif) s_ap_netif = esp_netif_create_default_wifi_ap();
    if (!s_ap_netif) return ESP_FAIL;
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_APSTA), TAG, "apsta");
    ESP_RETURN_ON_ERROR(portal_up(), TAG, "portal");
    s_up = true;

    char card[768];
    snprintf(card, sizeof card,
             "{\"type\":\"composite\",\"card_id\":\"authfail\",\"parts\":["
             "{\"type\":\"text\",\"title\":\"re-provision me\","
             "\"body\":\"My token is being refused. I have NOT erased "
             "anything, and I keep retrying every 5 minutes.\"},"
             "{\"type\":\"list\",\"items\":["
             "\"1. tiny.technology > devices > sticky > re-issue\","
             "\"2. Scan (or join  %s  / pw  " AP_KEY "),\","
             "\"   then the tiny app or http://192.168.4.1\"]},"
             "{\"type\":\"qr\",\"text\":\"WIFI:S:%s;T:WPA;P:" AP_KEY ";;\","
             "\"caption\":\"rescue portal - tiny sticky %s\"}]}",
             s_ssid, s_ssid, TINY_FW_VERSION);
    tiny_display_render_card(card);
    ESP_LOGW(TAG, "RESCUE portal up (APSTA): ssid=%s ip=192.168.4.1", s_ssid);
    return ESP_OK;
}
