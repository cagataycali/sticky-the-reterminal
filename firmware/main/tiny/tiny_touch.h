// tiny_touch — GT911 taps -> card button regions -> ui_tap device events.
// Calibrate orientation on OUR unit in M2 (community build needed 800-y).
#pragma once
#include "esp_err.h"
#ifdef __cplusplus
extern "C" {
#endif
// id[48] to match the ONE button-id contract in tiny_display.h — ids carry the
// destination ("m:t:<login>"), so a 24-byte id here would silently open somebody
// else's conversation the day anyone wires tiny_touch_set_regions up (it is a
// stub today; the live regions live in tiny_display.cpp, already 48). A stale
// narrower copy of a contract in a public header is a bug with a delay on it.
typedef struct { int x, y, w, h; char id[48]; char card_id[24]; } tiny_touch_region_t;
esp_err_t tiny_touch_init(void);
void tiny_touch_set_regions(const tiny_touch_region_t *r, int count); // from render_card
// Test hook: inject a tap at panel coordinates through the real resolver.
esp_err_t tiny_touch_inject_tap(int x, int y);
// Test hook (D-UX0): inject a swipe between two panel coordinates through the
// real release classifier. INVALID_SIZE = travel within tap slop (refused —
// a swipe that would resolve as a tap is a caller error, not a tap).
esp_err_t tiny_touch_inject_swipe(int x0, int y0, int x1, int y1);
// Milliseconds the act task has been stuck inside ONE route (0 = idle).
// The node task treats a long-wedged route as fatal and reboots: a gesture
// bug may cost a reboot, never the relay.
int64_t tiny_touch_act_busy_ms(void);
// Monotonic count of completed act-task routes. Wait for it to advance after
// an inject and the receipt can speak display-time truth.
uint32_t tiny_touch_route_seq(void);
// The finished route's own verdict (valid once route_seq advanced past your
// snapshot): ESP_OK = did something, ESP_ERR_NOT_ALLOWED = announced dead-end.
esp_err_t tiny_touch_route_result(void);
// id+label of the region the finished route served (valid once
// route_seq advanced past your snapshot; "" = gesture/no region).
void tiny_touch_route_region(char *id, size_t idcap, char *label, size_t labcap);
#ifdef __cplusplus
}
#endif
