#pragma once

#include "esp_err.h"

// Initializes the onboard buzzer for a single short feedback tone.
esp_err_t sticky_buzzer_init();

// Plays one short, blocking button-feedback beep.
esp_err_t sticky_buzzer_beep();
// Two short blips, lower pitch: "recognized but refused" — the dead-end word
// of the 3-word feedback vocabulary (UX_SPEC §0). On e-ink the sound IS the
// explanation; a silent refusal reads as a broken panel.
esp_err_t sticky_buzzer_beep_double();

// Silent mode (UI_SPEC §7): ALL chirps gate here — the single
// funnel, like tiny_audio_record_wav is for the mic. When silent, beeps
// return ESP_OK without touching the LEDC so callers never special-case it;
// visual acks (button invert flash) remain the feedback channel.
void sticky_buzzer_set_silent(bool silent);
bool sticky_buzzer_silent(void);

// Volume tier: soft = duty/8, same pitch, same words —
// the vocabulary is untouched, only the room it fills changes.
void sticky_buzzer_set_soft(bool soft);
bool sticky_buzzer_soft(void);
