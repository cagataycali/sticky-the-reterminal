// tiny_onboard — the first-run flow. Five pages on the shell's own rails
// (cards, touch router, UP/DOWN, edge gestures), derived from device state
// rather than stored as a wizard cursor: the step IS what the device lacks.
//
//   welcome  → no networks, first look this boot
//   wifi     → no networks (phone QR to the softAP portal; [type it here]
//              opens the shell's scan list + keyboard)
//   link     → networks saved, STA not up (joining… / honest failure)
//   pair     → STA up, no device_id+token (Phase A: portal /pair over APSTA;
//              Phase B: pairing code + QR — swap render_pair only)
//   ready    → provisioned, tour not yet dismissed (NVS ob_done)
//   done     → home owns the glass
//
// One writer of config remains tiny_config_merge_json; this module only
// paints and routes. Every page has ≥80 px targets and a [Back]. The QR part
// sits LAST in a composite (it consumes remaining rows).
#pragma once
#include "esp_err.h"
#include <stdbool.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    TINY_OB_WELCOME = 0,
    TINY_OB_WIFI,
    TINY_OB_LINK,
    TINY_OB_PAIR,
    TINY_OB_READY,
    TINY_OB_DONE,
} tiny_ob_step_t;

tiny_ob_step_t tiny_onboard_step(void);   // derived: cfg + wifi + ob_done flag
bool           tiny_onboard_active(void); // step != DONE
esp_err_t      tiny_onboard_render(void); // paint the current step (blocks on e-ink)
// "ob:next" "ob:back" "ob:phone" "ob:type" "ob:retry" "ob:start" — routed by
// the touch router (Act::kOnboard) and by tiny_shell_cycle while onboarding.
esp_err_t      tiny_onboard_route(const char *id);
// Boot-time Wi-Fi walk verdict for the link page (tiny_main calls after the
// walk). ssid may be NULL when nothing was just added; reason is the
// disconnect reason of that ssid if it failed, 0 otherwise.
void           tiny_onboard_note_link(bool up, const char *just_added, int reason);
esp_err_t      tiny_onboard_mark_done(void);
// Preview a page regardless of state — the relay twin (`page onboard <step>`)
// so every first-run card can be screenshotted from a provisioned device.
// Routing from a previewed page follows the REAL state, not the preview.
esp_err_t      tiny_onboard_preview(const char *step_name);
const char    *tiny_onboard_step_name(tiny_ob_step_t s);  // "welcome" … "done"

#ifdef __cplusplus
}
#endif
