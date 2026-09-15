// tiny_lock — pocket lockout (UX_SPEC §11). See header for the contract.
#include "tiny/tiny_lock.h"

#include <stdatomic.h>
#include <cmath>

#include "driver/gpio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "pin_config.h"
#include "sticky_buzzer.h"
#include "tiny/tiny_display.h"
#include "tiny/tiny_node.h"

static const char *TAG = "tiny_lock";

// §11 named constants (spec: "thresholds are firmware constants, named in
// code"). The chord is 1 s of BOTH page buttons held — long enough that
// fabric cannot fake it, short enough for one hand out of the pocket.
static const int kUnlockChordMs = 1000;
static const int kChordPollMs = 50;    // 20 samples over the chord second

static atomic_bool s_locked = false;

// §11 auto-entry constants — named, per spec ("firmware constants, tuned
// on-device"). Sampling arrives at tiny_orient's 1Hz cadence, so the sample
// counts below ARE the seconds.
static const float kFaceDownG = 0.60F;     // az below -this = glass to the fabric
static const int kFaceDownSamples = 3;     // ≥3s face-down -> pocket/table-down
static const float kMotionDeltaG = 0.18F;  // |mag| swing between 1Hz samples
static const int kMotionSamples = 5;       // ≥5s of sustained swing = walking
static const int kNoInputMs = 30000;       // and nobody touched it for 30s

static int s_face_streak = 0;
static int s_motion_streak = 0;
static float s_prev_mag = 1.0F;
static volatile TickType_t s_last_input = 0;

extern "C" void tiny_lock_note_activity(void) {
    s_last_input = xTaskGetTickCount();
}

extern "C" void tiny_lock_imu_feed(float ax, float ay, float az) {
    if (atomic_load(&s_locked)) {
        s_face_streak = 0;
        s_motion_streak = 0;
        return;
    }
    // Face-down: the stand pose measured az = +0.878 (glass up), so glass
    // DOWN is az strongly negative. Threshold 0.60 clears any stand tilt.
    if (az < -kFaceDownG) {
        if (++s_face_streak >= kFaceDownSamples) {
            tiny_lock_set(true, "IMU: face-down 3s");
            return;
        }
    } else {
        s_face_streak = 0;
    }
    // Walking: at 1Hz we cannot see the ~2Hz step wave itself — what we CAN
    // see honestly is the sample-to-sample swing of |a| away from a still
    // 1g. Sustained swing + a 30s-idle input clock is the pocket signature;
    // a device in use also moves, which is exactly why the idle clock gates.
    const float mag = sqrtf(ax * ax + ay * ay + az * az);
    const float delta = fabsf(mag - s_prev_mag);
    s_prev_mag = mag;
    if (delta > kMotionDeltaG) {
        if (++s_motion_streak >= kMotionSamples &&
            (xTaskGetTickCount() - s_last_input) >= pdMS_TO_TICKS(kNoInputMs)) {
            tiny_lock_set(true, "IMU: walking 5s + no input 30s");
        }
    } else {
        s_motion_streak = 0;
    }
}

extern "C" bool tiny_lock_is_locked(void) {
    return atomic_load(&s_locked);
}

extern "C" esp_err_t tiny_lock_set(bool locked, const char *reason) {
    const bool was = atomic_exchange(&s_locked, locked);
    if (was == locked) return ESP_OK;  // idempotent, no ceremony
    ESP_LOGI(TAG, "%s (%s)", locked ? "LOCKED" : "UNLOCKED",
             reason ? reason : "unspecified");
    if (locked) {
        // Two blips = "input refused" in the sound vocabulary — here it means
        // "inputs are now refused", audible through a pocket. The glass keeps
        // its card: e-ink holds it for free, and lockout is not a page.
        sticky_buzzer_beep_double();
        // §12: the strip should SAY it is locked — one partial refresh flips
        // the [L] into the glance cluster without a full flash. Failure is
        // fine (no card cached yet); the lock itself never depends on it.
        tiny_display_glance();
    } else {
        sticky_buzzer_beep();
        // Leaving lockout restores the full 30 min budget, and
        // it starts NOW — otherwise a pocket that woke us 44 s ago would
        // sleep one second after the human finally unlocked it.
        tiny_node_note_activity();
        // §11 exit: full refresh, last card restored. The card never left the
        // glass — the re-render clears partial-refresh ghosting accumulated
        // in the pocket and proves the panel is live again.
        tiny_display_rerender();
    }
    return ESP_OK;
}

// Unlock-chord watcher. Buttons are active-low (iot_button config in
// tiny_button.cpp uses active_level 0). While UNLOCKED this task is one
// atomic read per poll — cheap enough to leave running; while LOCKED it is
// the ONLY path back, so it must not depend on iot_button (whose callbacks
// tiny_button.cpp gates when locked).
static void chord_task(void *) {
    int held_ms = 0;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(kChordPollMs));
        if (!atomic_load(&s_locked)) { held_ms = 0; continue; }
        const bool up = gpio_get_level((gpio_num_t)PIN_BTN_UP) == 0;
        const bool down = gpio_get_level((gpio_num_t)PIN_BTN_DOWN) == 0;
        if (up && down) {
            held_ms += kChordPollMs;
            if (held_ms >= kUnlockChordMs) {
                held_ms = 0;
                tiny_lock_set(false, "UP+DOWN chord held 1s");
            }
        } else {
            held_ms = 0;
        }
    }
}

extern "C" esp_err_t tiny_lock_init(void) {
    // The no-input clock starts at boot: a device that boots mid-walk locks
    // itself 30s later with no special boot-state case (designer's
    // boot-persona question resolves here — flat-on-charger never trips
    // either condition, so the wall persona keeps its unlocked boot).
    s_last_input = xTaskGetTickCount();
    // 6KB stack, same as shell_nav: the unlock path re-renders the card from
    // THIS task, and the render path was sized against 6KB elsewhere.
    if (xTaskCreate(chord_task, "lock_chord", 6144, NULL, 3, NULL) != pdPASS)
        return ESP_ERR_NO_MEM;
    // Boot-state evidence (a probe once saw locked:true at uptime 89s and asked
    // whether 0.17.0 boots locked BY DESIGN: it does not — s_locked inits
    // false; that observation was a queued `lock` envelope from the probe
    // script delivered on the first post-OTA poll). This line is the receipt.
    ESP_LOGI(TAG, "pocket lockout armed: boot state=UNLOCKED, chord=UP+DOWN "
                  "%dms, entry=manual (IMU auto-entry is the next package)",
             kUnlockChordMs);
    return ESP_OK;
}
