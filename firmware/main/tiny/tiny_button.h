// tiny_button — physical controls, the no-relay path to talk to tiny.
// AI/OK button (GPIO4, active-low) single click → voice ask: chime, record
// 6s, upload, /api/devices/ask, answer renders on the panel. The iot_button
// callback runs on the button component's timer task and MUST NOT block for
// the ~30s a full ask takes, so the click only feeds a depth-1 queue that a
// dedicated worker task drains — a second click during an ask is dropped,
// not queued (double-press ≠ two questions).
// UP/DOWN (GPIO5/6) single click → device event upstream (page verbs later).
#pragma once
#include "esp_err.h"
#ifdef __cplusplus
extern "C" {
#endif
esp_err_t tiny_button_init(void);
// Start a voice ask (chime, 6s record, upload, /api/devices/ask, render).
// Non-blocking: feeds the same depth-1 queue the physical AI button uses, so a
// screen tap on "Ask tiny" and the hardware button can never run two asks at
// once. Returns ESP_ERR_INVALID_STATE when an ask is already in flight.
esp_err_t tiny_button_request_voice_ask(void);
// A TYPED question through the same single-flight door (keyboard "ok").
// The text is copied into the queue item, so the caller's buffer is free the
// moment this returns. ESP_ERR_INVALID_STATE = an ask is already in flight;
// ESP_ERR_INVALID_SIZE = over the 95-char item cap (refused, never clipped).
esp_err_t tiny_button_request_text_ask(const char *text);
#ifdef __cplusplus
}
#endif
