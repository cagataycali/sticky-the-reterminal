#include "sticky_buzzer.h"

#include "driver/ledc.h"
#include "esp_check.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "pin_config.h"

namespace {

constexpr ledc_mode_t kSpeedMode = LEDC_LOW_SPEED_MODE;
constexpr ledc_timer_t kTimer = LEDC_TIMER_0;
constexpr ledc_channel_t kChannel = LEDC_CHANNEL_0;
constexpr uint32_t kBeepFrequencyHz = 2400;
constexpr uint32_t kBeepDuty = 256;
// Sounds: SOFT tier = duty/8. On a piezo, PWM duty is loudness;
// 32/1024 is clearly audible at arm's length and unremarkable in a meeting —
// measured by ear on the bench, the only instrument that matters here.
constexpr uint32_t kBeepDutySoft = 32;
bool s_soft = false;
uint32_t beep_duty() { return s_soft ? kBeepDutySoft : kBeepDuty; }
constexpr TickType_t kBeepDuration = pdMS_TO_TICKS(60);

esp_err_t set_duty(uint32_t duty)
{
    esp_err_t result = ledc_set_duty(kSpeedMode, kChannel, duty);
    if (result != ESP_OK) {
        return result;
    }
    return ledc_update_duty(kSpeedMode, kChannel);
}

}  // namespace

void sticky_buzzer_set_soft(bool soft) { s_soft = soft; }
bool sticky_buzzer_soft(void) { return s_soft; }

static volatile bool s_silent = false;
void sticky_buzzer_set_silent(bool silent) { s_silent = silent; }
bool sticky_buzzer_silent(void) { return s_silent; }

esp_err_t sticky_buzzer_init()
{
    ledc_timer_config_t timer_config = {};
    timer_config.speed_mode = kSpeedMode;
    timer_config.timer_num = kTimer;
    timer_config.duty_resolution = LEDC_TIMER_10_BIT;
    timer_config.freq_hz = kBeepFrequencyHz;
    timer_config.clk_cfg = LEDC_AUTO_CLK;
    ESP_RETURN_ON_ERROR(
        ledc_timer_config(&timer_config), "sticky_buzzer", "configure timer");

    ledc_channel_config_t channel_config = {};
    channel_config.gpio_num = PIN_BUZZER;
    channel_config.speed_mode = kSpeedMode;
    channel_config.channel = kChannel;
    channel_config.intr_type = LEDC_INTR_DISABLE;
    channel_config.timer_sel = kTimer;
    channel_config.duty = 0;
    channel_config.hpoint = 0;
    return ledc_channel_config(&channel_config);
}

esp_err_t sticky_buzzer_beep_double()
{
    if (s_silent) return ESP_OK;  // UI_SPEC §7: silence is a success, not an error

    // 1800 Hz, 45ms x2 with a 70ms gap: audibly a DIFFERENT word from the
    // single 2400 Hz accept-blip, still under the 200ms a finger tolerates.
    ESP_RETURN_ON_ERROR(ledc_set_freq(kSpeedMode, kTimer, 1800),
                        "sticky_buzzer", "set frequency");
    for (int i = 0; i < 2; ++i) {
        ESP_RETURN_ON_ERROR(set_duty(beep_duty()), "sticky_buzzer", "blip");
        vTaskDelay(pdMS_TO_TICKS(45));
        ESP_RETURN_ON_ERROR(set_duty(0), "sticky_buzzer", "blip off");
        if (i == 0) vTaskDelay(pdMS_TO_TICKS(70));
    }
    return ESP_OK;
}

esp_err_t sticky_buzzer_beep()
{
    if (s_silent) return ESP_OK;  // UI_SPEC §7

    ESP_RETURN_ON_ERROR(
        ledc_set_freq(kSpeedMode, kTimer, kBeepFrequencyHz),
        "sticky_buzzer", "set frequency");
    ESP_RETURN_ON_ERROR(set_duty(beep_duty()), "sticky_buzzer", "start beep");
    vTaskDelay(kBeepDuration);
    return set_duty(0);
}
