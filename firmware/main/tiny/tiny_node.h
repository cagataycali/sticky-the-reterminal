// tiny_node — the device loop: heartbeat 30s, relay poll 5s, dispatch, reply.
// Mirrors tiny-tech relay-poller + strands-nicla tiny_node.py.
// EVERY request: 15s timeout (a hung socket took the Nicla silent for 4.5min).
// heartbeat/poll 401 -> tiny_config_forget_identity() -> provisioning portal.
// No envelope executes silently: buzzer blip per dispatch.
#pragma once
#include "esp_err.h"
#ifdef __cplusplus
extern "C" {
#endif
esp_err_t tiny_node_start(void);   // spawns node task; needs wifi up + provisioned cfg
void      tiny_node_stop(void);
// Reset the idle/cadence activity clock from outside the node (the unlock
// chord restores the full auto-sleep budget starting at unlock).
void      tiny_node_note_activity(void);
// Post a device event upstream (ui_tap etc) — used by tiny_touch.
esp_err_t tiny_node_post_event(const char *kind, const char *json_fields);
// The ask engine (relay verb AND the AI button): text!=NULL → text ask,
// else records voice_secs of mic audio. Renders the answer (card, or the
// text as a text-card); human-readable summary into out. BLOCKS up to ~90s.
esp_err_t tiny_node_do_ask(const char *text, int voice_secs, char *out, size_t out_cap);
// Machine-readable device status as a JSON object string: fw, device_id,
// battery_pct, charging, wifi, rssi_dbm, ssid, heap_free, uptime_s, sleeping,
// display, card_id, auth_halted, summary. Unknown values are JSON null, never
// a guess. Both the `status` relay verb and the dashboard read this one shape.
esp_err_t tiny_node_status_json(char *out, size_t cap);
bool tiny_node_poll_idle(void);    // live cadence truth (idle-60s vs active-5s)
// Render the status kv-card on the panel with NO network call — the "Status"
// screen button must work when the backend is down.
esp_err_t tiny_node_render_status_card(void);

// Messages app: fetch and render with the DEVICE token (no owner process in the
// loop). `what` is "inbox", "t:<login>" (thread) or "c:<login>" (compose) —
// i.e. a button id minus its "m:" prefix, so each row carries its own
// destination. Blocks on HTTPS + a full e-ink refresh (compose renders
// locally); call it from the touch action worker, never the touch task.
esp_err_t tiny_node_messages_open(const char *what);
// Send `body` to @login (device token), then re-open the thread as the receipt;
// paints a "send failed" card on any non-200. Same blocking rule as open.
esp_err_t tiny_node_messages_send(const char *login, const char *body);
// DM badge: last unread count the heartbeat reply carried (opt-in wantUnread).
// -1 until the platform first says; absent field in a beat keeps the value.
int tiny_node_unread(void);
void tiny_node_sleep_idle_set(int minutes);  // 5/15/30/60 (else ignored)
void tiny_node_set_first_paint(int ms, bool glance_wake);  // §12 <2s receipt
void tiny_node_set_paint_breakdown(int board_ms, int disp_ms);
// Last completed ask answer (streamed or classic), clipped to ~92
// chars, for the home card's preview line. ESP_ERR_NOT_FOUND before the
// first answer of this boot; never a guess, never stale across reboots.
esp_err_t tiny_node_last_answer(char *out, size_t cap);
#ifdef __cplusplus
}
#endif
