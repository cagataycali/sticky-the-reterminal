// tiny_lock — pocket lockout state machine (UX_SPEC §11, SAFETY-CRITICAL).
//
// Pocket-carry's one unforgivable failure: fabric holds the AI button, 15 s of
// audio records and uploads. Lockout guards the device's OWN INPUTS (touch,
// buttons, mic) — never the owner's remote reach: relay verbs still answer,
// except `voice`, because a remote-triggered recording from a pocket is the
// same privacy incident with extra steps.
//
// States: UNLOCKED ⇄ LOCKED. No third state — "locking…" does not exist on
// e-ink. Entry: manual (lock verb) or IMU auto-entry — face-down ≥3 s, or
// walking-motion ≥5 s with no input for 30 s (constants below, tuned
// on-device per spec).
//
// Exit: hold UP+DOWN together ≥1 s (raw GPIO poll — iot_button's long-press
// clock is 2 s and per-button; the chord is OURS) → full re-render of the
// card already on the glass → unlocked.
#pragma once
#include "esp_err.h"
#ifdef __cplusplus
extern "C" {
#endif

esp_err_t tiny_lock_init(void);      // starts the unlock-chord watcher task
bool tiny_lock_is_locked(void);      // the gate every input choke point asks

// reason is for the log line — every entry/exit names its cause.
// Locking is idempotent; unlock re-renders the last card (full refresh).
esp_err_t tiny_lock_set(bool locked, const char *reason);

// §11 half 2 — IMU auto-entry. tiny_orient's 1Hz task feeds every good
// accel sample here (ONE imu reader on the bus; lock policy lives in this
// module). Locks on: face-down ≥3s, or walking-motion ≥5s while the device
// has seen no input for 30s. No-ops while already locked.
void tiny_lock_imu_feed(float ax_g, float ay_g, float az_g);

// Any human input (trusted touch, button press) — resets the no-input clock
// the walking condition needs. Spec §11 says "no touch"; buttons count too
// because a pressed button is the same evidence of "in use, not pocketed".
void tiny_lock_note_activity(void);

#ifdef __cplusplus
}
#endif
