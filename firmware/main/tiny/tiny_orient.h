// tiny_orient — the accelerometer decides which way up the UI is.
//
// This is the difference between a screen and a body: pick the device off the
// wall, stand it on its side, and the card should turn with it. The panel takes
// ~600 ms per full refresh and every refresh is visible, so the rule is "turn
// once the human has clearly finished turning" — never chase a wobble.
//
// Policy, deliberately small:
//   * sample sticky_imu_read() every kPollMs;
//   * ignore face_up / face_down entirely — flat on a table says nothing about
//     which edge is "up", and the last standing orientation is the best guess;
//   * only commit after kAgree consecutive identical readings (hysteresis);
//   * `manual` mode parks the task: a relay/shell `rotate 90` must not be
//     undone two seconds later by gravity.
#pragma once
#include "esp_err.h"
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif

// Starts the watcher task. Safe to call before the IMU is up (it will simply
// report no reading until sticky_imu_init succeeds).
esp_err_t tiny_orient_start(void);

// auto = follow gravity; manual = hold whatever rotation was last set.
bool tiny_orient_auto(void);
void tiny_orient_set_auto(bool on);

// Degrees the watcher last committed (-1 before its first commit) and the raw
// orientation name behind it, for the `rotate`/`sensors` verbs.
int tiny_orient_last_degrees(void);
const char *tiny_orient_last_name(void);

// Why the panel did or did not turn: sample/error counts, the last IMU error,
// how many agreeing samples are stacked up, and the raw gravity vector.
int tiny_orient_debug_json(char *out, size_t len);

#ifdef __cplusplus
}
#endif
