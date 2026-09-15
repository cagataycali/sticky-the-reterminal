// tiny_stream — fetch server-side-dithered frames over Wi-Fi and blit.
// "Video" on e-ink is a frame cadence (partial refresh, >=1.5s) — the Nicla
// clip framing: it shows how the scene CHANGES, not smooth motion.
#pragma once
#include <stdbool.h>
#include "esp_err.h"
#ifdef __cplusplus
extern "C" {
#endif
esp_err_t tiny_stream_image(const char *url, bool gray4);         // one frame
esp_err_t tiny_stream_play(const char *manifest_url, int interval_s, int max_frames);
void      tiny_stream_stop(void);
bool      tiny_stream_playing(void);
const char *tiny_stream_verdict(void);  // last player exit, one sentence
#ifdef __cplusplus
}
#endif
