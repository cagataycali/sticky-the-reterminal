// P0-BOUNCE flight recorder — see tiny/tiny_bootmark.h for the contract.
// Kept in its own TU so tests/host compiles the SHIPPED object under test
// (same discipline as tiny_askq: drift impossible by construction).
#include "tiny/tiny_bootmark.h"
#include "nvs.h"
#include "nvs_flash.h"

static int s_bm_prev = -1;

extern "C" int tiny_bootmark_prev(void) { return s_bm_prev; }

extern "C" void tiny_bootmark_stage(uint8_t stage)
{
    nvs_handle_t nh;
    if (nvs_open("tinyboot", NVS_READWRITE, &nh) != ESP_OK) {
        // Stage 1 runs before tiny_config's lazy nvs_flash_init — init here,
        // idempotent, so the earliest stages are recorded too.
        nvs_flash_init();
        if (nvs_open("tinyboot", NVS_READWRITE, &nh) != ESP_OK) return;
    }
    if (s_bm_prev < 0) {  // first write this boot: preserve the last run's verdict
        uint8_t prev = 0;
        if (nvs_get_u8(nh, "bm_last", &prev) == ESP_OK) s_bm_prev = prev;
        else s_bm_prev = 0;
        nvs_set_u8(nh, "bm_prev", (uint8_t)s_bm_prev);
    }
    nvs_set_u8(nh, "bm_last", stage);
    nvs_commit(nh);
    nvs_close(nh);
}
