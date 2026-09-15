// tiny_shell — "stickyOS": home screen + local navigation.
// The card renderer is the view layer; pages are LOCAL card specs and LOCAL
// tap handlers. Navigation NEVER touches the network — that is the phone feel.
// Unknown taps still go upstream as ui_tap.
//
//   Home:     app "grid" (buttons bar v1): Ask tiny / Status / Sensors / Settings
//   Ring:     UP/DOWN buttons cycle home -> status -> sensors -> settings ->
//   Stack:    open() pushes, back() pops, bottom is always home
//   Home key: LONG-PRESS the AI button, from anywhere (single click stays ask)
#pragma once
#include "esp_err.h"
#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    TINY_PAGE_HOME = 0,
    TINY_PAGE_STATUS,
    TINY_PAGE_SENSORS,
    TINY_PAGE_SETTINGS,
    TINY_PAGE_WIFI,
    TINY_PAGE_BLE,
    TINY_PAGE_CFGQR,   // drill-only: settings config QR
    TINY_PAGE_ONBOARD, // first-run flow (tiny_onboard) — owns the glass until
                       // the device is provisioned and the tour is dismissed
    TINY_PAGE_COUNT,
} tiny_page_t;

esp_err_t tiny_shell_init(void);            // render home; call after display+node init
esp_err_t tiny_shell_open(tiny_page_t p);   // push + render (blocks on e-ink; call off touch task)
esp_err_t tiny_shell_back(void);            // pop + render; at bottom stays home
esp_err_t tiny_shell_home(void);            // clear stack + render home
esp_err_t tiny_shell_cycle(int dir);        // +1/-1 through the ring (UP/DOWN keys)
// D-UX1 (UX_SPEC §2, law #2): the left-band "back" gesture. Depth-aware —
// a pushed card pops, a ring root goes to the previous ring page.
esp_err_t tiny_shell_nav_back(void);
// True when the visible page is a ring root (depth 1, one of the 4 ring
// pages). Pushed cards answer false — the router's dead-end signal.
bool tiny_shell_at_root(void);
// Taps owned by shell pages (called off the touch task):
esp_err_t tiny_shell_key(const char *id);   // keyboard key ("k:a", "k:ok", …)
esp_err_t tiny_shell_menu(const char *id);  // menu row ("w:<idx>" = wifi join)
// v11 universe: "u:open" render roster page, "u:clear" back to own tiny,
// "u:i:<n>" switch to roster index n. Repaint IS the ack.
esp_err_t tiny_shell_universe_route(const char *id);
// v11: render_ui {"type":"universe","tinys":[…]?} — merge slugs into the
// roster, then render the page (called by the display dispatch).
esp_err_t tiny_shell_universe_push(const char *spec_json);
esp_err_t tiny_shell_rescan_wifi(void);     // force a fresh scan (Rescan button)
esp_err_t tiny_shell_silent_toggle(void);   // flip + persist + repaint settings
// Gate 7 — open the keyboard as a DM composer targeting @login ("Reply" tap).
// Renders immediately, no network; "ok" hands the text to tiny_node_messages_send.
esp_err_t tiny_shell_compose(const char *login);
// Open the keyboard as the agent composer ([Type] on the home card).
// "ok" routes the question into the same single-flight ask worker the AI
// button uses; the answer streams onto the glass.
esp_err_t tiny_shell_ask_compose(void);
#ifdef __cplusplus
}
#endif
