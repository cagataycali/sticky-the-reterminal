// tiny_audio — PDM mic capture (16kHz/16-bit mono WAV in PSRAM) + chime.
// Pins per pin_config.h (official hardware-overview): CLK=GPIO19, DATA=GPIO20,
// EN=GPIO38 (TPS22916 load switch). USB-JTAG reclaims GPIO19/20 after a
// deep-sleep wake, so every capture re-claims the pins first (see
// tiny_audio_record_wav) and clean-cycles the mic rail.
#include "tiny/tiny_audio.h"
#include "tiny/tiny_lock.h"

#include <string.h>

#include "driver/gpio.h"
#include "driver/i2s_pdm.h"
#include "soc/usb_serial_jtag_struct.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "pin_config.h"
#include "sticky_buzzer.h"

static const char *TAG = "tiny_audio";

#define SAMPLE_RATE 16000
#define MAX_SECONDS 15

static i2s_chan_handle_t s_rx = NULL;

// 44-byte canonical PCM WAV header.
static void wav_header(uint8_t *h, uint32_t pcm_bytes) {
    const uint32_t byte_rate = SAMPLE_RATE * 2;  // mono s16
    memcpy(h, "RIFF", 4);
    uint32_t v = 36 + pcm_bytes;      memcpy(h + 4, &v, 4);
    memcpy(h + 8, "WAVEfmt ", 8);
    v = 16;                           memcpy(h + 16, &v, 4);
    uint16_t u = 1;                   memcpy(h + 20, &u, 2);  // PCM
    u = 1;                            memcpy(h + 22, &u, 2);  // mono
    v = SAMPLE_RATE;                  memcpy(h + 24, &v, 4);
    v = byte_rate;                    memcpy(h + 28, &v, 4);
    u = 2;                            memcpy(h + 32, &u, 2);  // block align
    u = 16;                           memcpy(h + 34, &u, 2);  // bits
    memcpy(h + 36, "data", 4);
    memcpy(h + 40, &pcm_bytes, 4);
}

extern "C" esp_err_t tiny_audio_init(void) {
    // Mic power rail (clean-cycled again per recording).
    gpio_config_t en = {};
    en.pin_bit_mask = 1ULL << PIN_MIC_EN;
    en.mode = GPIO_MODE_OUTPUT;
    ESP_RETURN_ON_ERROR(gpio_config(&en), TAG, "mic en gpio");
    gpio_set_level((gpio_num_t)PIN_MIC_EN, 0);
    return sticky_buzzer_init();
}

extern "C" esp_err_t tiny_audio_record_wav(int seconds, uint8_t **wav, size_t *len) {
    // D-UX5 (P1, found on glass): lockout gates capture PATHS, not verb
    // names. EVERY microphone activation funnels through this function —
    // voice verb, AI-button ask, miccheck, and any future path — so the §11
    // gate lives HERE, at the audio entry point. miccheck never uploads, but
    // a mic sampling a locked pocket is still a mic.
    if (tiny_lock_is_locked()) {
        ESP_LOGW(TAG, "locked: mic capture refused at audio entry (S11/D-UX5)");
        return ESP_ERR_NOT_ALLOWED;
    }
    if (!wav || !len) return ESP_ERR_INVALID_ARG;
    *wav = NULL; *len = 0;
    if (seconds < 1) seconds = 1;
    if (seconds > MAX_SECONDS) seconds = MAX_SECONDS;

    // Voice Companion deep-sleep fix (SYS-P0-1): after any deep-sleep wake the
    // ESP32-S3 pin mux hands GPIO19/20 back to the native USB-Serial-JTAG
    // peripheral, which silently mutes the PDM mic (every post-wake capture
    // reads zeros). Reclaim the pins BEFORE I2S init, every time - it is
    // idempotent and costs microseconds. Console is untouched: this board's
    // serial is UART0 on GPIO43/44, not USB-JTAG.
    USB_SERIAL_JTAG.conf0.usb_pad_enable = 0;
    gpio_reset_pin((gpio_num_t)PIN_MIC_CLK);
    gpio_reset_pin((gpio_num_t)PIN_MIC_DATA);

    // Clean-cycle the mic rail (community fix: 150ms off clears a wedged PDM).
    gpio_set_level((gpio_num_t)PIN_MIC_EN, 0);
    vTaskDelay(pdMS_TO_TICKS(150));
    gpio_set_level((gpio_num_t)PIN_MIC_EN, 1);
    vTaskDelay(pdMS_TO_TICKS(100));

    i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    ESP_RETURN_ON_ERROR(i2s_new_channel(&cc, NULL, &s_rx), TAG, "chan");

    i2s_pdm_rx_config_t pc = {};
    pc.clk_cfg = I2S_PDM_RX_CLK_DEFAULT_CONFIG(SAMPLE_RATE);
    pc.slot_cfg = I2S_PDM_RX_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                 I2S_SLOT_MODE_MONO);
    pc.gpio_cfg.clk = (gpio_num_t)PIN_MIC_CLK;
    pc.gpio_cfg.din = (gpio_num_t)PIN_MIC_DATA;
    esp_err_t err = i2s_channel_init_pdm_rx_mode(s_rx, &pc);
    if (err == ESP_OK) err = i2s_channel_enable(s_rx);
    if (err != ESP_OK) goto fail;

    {
        const size_t pcm_bytes = (size_t)seconds * SAMPLE_RATE * 2;
        uint8_t *buf = (uint8_t *)heap_caps_malloc(44 + pcm_bytes,
                                                   MALLOC_CAP_SPIRAM);
        if (!buf) { err = ESP_ERR_NO_MEM; goto fail; }
        size_t got = 0;
        while (got < pcm_bytes) {
            size_t n = 0;
            err = i2s_channel_read(s_rx, buf + 44 + got, pcm_bytes - got, &n,
                                   pdMS_TO_TICKS(1000));
            if (err != ESP_OK) { free(buf); goto fail; }
            got += n;
        }
        wav_header(buf, (uint32_t)pcm_bytes);
        *wav = buf;
        *len = 44 + pcm_bytes;
    }

fail:
    if (s_rx) {
        i2s_channel_disable(s_rx);
        i2s_del_channel(s_rx);
        s_rx = NULL;
    }
    gpio_set_level((gpio_num_t)PIN_MIC_EN, 0);
    ESP_LOGI(TAG, "record %ds -> %s (%u bytes)", seconds,
             esp_err_to_name(err), (unsigned)*len);
    return err;
}

extern "C" esp_err_t tiny_audio_chime(const char *) {
    // Tier 1: the vendored buzzer beep. Named chimes/RTTTL are M7 polish.
    return sticky_buzzer_beep();
}
