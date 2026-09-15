// tiny_audio — PDM mic capture (16kHz/16-bit WAV in PSRAM, cap 15s) + buzzer.
// Buzzer = chimes/RTTTL (tier 1). PCM-through-PWM is a timeboxed EXPERIMENT
// (tier 2) — measure, don't promise (docs/ARCHITECTURE.md §Audio).
// The mic pin-mux fix after a deep-sleep wake lives inside the capture path.
#pragma once
#include "esp_err.h"
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
esp_err_t tiny_audio_init(void);
esp_err_t tiny_audio_record_wav(int seconds, uint8_t **wav, size_t *len); // PSRAM, caller frees
esp_err_t tiny_audio_chime(const char *name_or_rtttl);                    // "ack","ask","error",...
#ifdef __cplusplus
}
#endif
