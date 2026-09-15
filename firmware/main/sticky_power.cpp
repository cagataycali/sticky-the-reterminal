#include "sticky_power.h"

#include <cstdio>

#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "pin_config.h"
#include "sticky_sdcard.h"

namespace {

constexpr char kTag[] = "sticky_power";
constexpr TickType_t kReleasePollInterval = pdMS_TO_TICKS(20);
constexpr TickType_t kReleaseDebounceTime = pdMS_TO_TICKS(60);

esp_err_t hold_output(gpio_num_t pin, int level)
{
    gpio_hold_dis(pin);
    ESP_RETURN_ON_ERROR(gpio_set_direction(pin, GPIO_MODE_OUTPUT),
                        kTag, "configure held output");
    ESP_RETURN_ON_ERROR(gpio_set_level(pin, level),
                        kTag, "set held output");
    return gpio_hold_en(pin);
}

// With the three-pin EXT1 mask, entering sleep while ANY
// armed button is held means instant re-wake — the pocket sawtooth
// (fabric holds Down → auto-sleep → instant EXT1 wake → session → idle →
// sleep → wake, forever, until the fabric moves). The release wait must
// therefore cover every pin the mask arms, not just the AI button. A
// bounded timeout keeps a genuinely stuck button (or a pocket that never
// lets go) from blocking sleep entirely: after 10 s we proceed anyway —
// one spurious wake beats a device that can never rest, and the wake
// path's idle timer will bring it back down.
constexpr TickType_t kReleaseWaitTimeout = pdMS_TO_TICKS(10000);

void wait_for_wake_buttons_release()
{
    static const gpio_num_t kPins[] = {
        static_cast<gpio_num_t>(PIN_POWER_BTN),
        static_cast<gpio_num_t>(PIN_BTN_UP),
        static_cast<gpio_num_t>(PIN_BTN_DOWN),
    };
    ESP_LOGI(kTag, "Waiting for all wake buttons to release");
    TickType_t waited = 0;
    for (;;) {
        bool any_held = false;
        for (gpio_num_t p : kPins) {
            if (gpio_get_level(p) == 0) { any_held = true; break; }
        }
        if (!any_held) break;
        if (waited >= kReleaseWaitTimeout) {
            ESP_LOGW(kTag, "Wake button still held after 10s - sleeping anyway");
            return;  // skip debounce; we are proceeding under protest
        }
        vTaskDelay(kReleasePollInterval);
        waited += kReleasePollInterval;
    }
    vTaskDelay(kReleaseDebounceTime);
}

}  // namespace

void sticky_power_log_wakeup_reason()
{
    if (esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_EXT1) {
        // Name the actual button — three pins can wake us now.
        const uint64_t st = esp_sleep_get_ext1_wakeup_status();
        const char *which = (st & (1ULL << PIN_BTN_UP))     ? "Up button"
                          : (st & (1ULL << PIN_BTN_DOWN))   ? "Down button"
                                                            : "AI button";
        ESP_LOGI(kTag, "Woke from deep sleep by %s (ext1 status 0x%llx)",
                 which, (unsigned long long)st);
    }
}

[[noreturn]] void sticky_power_enter_deep_sleep()
{
    // SD discipline: a mounted FAT must be unmounted BEFORE SD_EN is held
    // low below, or the card loses power mid-write and the FAT eats it.
    sticky_sdcard_prepare_sleep();

    wait_for_wake_buttons_release();

    ESP_ERROR_CHECK(hold_output(
        static_cast<gpio_num_t>(PIN_POWER_HOLD), 1));
    ESP_ERROR_CHECK(hold_output(
        static_cast<gpio_num_t>(PIN_POWER_LOCK), 1));
    ESP_ERROR_CHECK(hold_output(static_cast<gpio_num_t>(PIN_EPD_EN), 0));
    ESP_ERROR_CHECK(hold_output(static_cast<gpio_num_t>(PIN_TOUCH_EN), 0));
    ESP_ERROR_CHECK(hold_output(static_cast<gpio_num_t>(PIN_TOUCH_RST), 0));
    ESP_ERROR_CHECK(hold_output(static_cast<gpio_num_t>(PIN_MIC_EN), 0));
    ESP_ERROR_CHECK(hold_output(static_cast<gpio_num_t>(PIN_SD_EN), 0));
    ESP_ERROR_CHECK(hold_output(static_cast<gpio_num_t>(PIN_BUZZER), 0));

    // The always-visible sleep card promises "any button wakes me",
    // but EXT1 armed GPIO4 alone — Up/Down presses fell on a deaf chip and
    // the card was a lie for two of the three buttons. Arm all three
    // (stock-firmware parity: "any button wakes" is the vendor's own UX).
    // GPIO4/5/6 are all RTC-capable on the S3, active-low with external
    // pulls of their own; we still enable the internal pull-ups so a
    // floating line cannot fake a press mid-sleep.
    static const gpio_num_t kWakePins[] = {
        static_cast<gpio_num_t>(PIN_POWER_BTN),
        static_cast<gpio_num_t>(PIN_BTN_UP),
        static_cast<gpio_num_t>(PIN_BTN_DOWN),
    };
    for (gpio_num_t p : kWakePins) {
        gpio_hold_dis(p);
        ESP_ERROR_CHECK(gpio_set_direction(p, GPIO_MODE_INPUT));
        ESP_ERROR_CHECK(gpio_pullup_en(p));
        ESP_ERROR_CHECK(gpio_pulldown_dis(p));
        ESP_ERROR_CHECK(gpio_hold_en(p));
    }

    // A pin that is STILL LOW when we arm ANY_LOW fires the
    // wake instantly — a 10s-held button surviving the release wait would
    // re-create the very sawtooth the wait exists to stop. So the mask is
    // built from the pins that are actually released right now; a held pin
    // rejoins the wake set on the next sleep cycle (it cannot wake anyone
    // while held anyway). If everything is held (device face-down in a
    // tight pocket), fall back to the AI button alone: a possible single
    // spurious wake beats a device no button can ever wake again.
    uint64_t wake_mask = 0;
    for (gpio_num_t p : kWakePins) {
        if (gpio_get_level(p) != 0) wake_mask |= (1ULL << p);
    }
    if (wake_mask == 0) {
        ESP_LOGW(kTag, "All wake buttons held at sleep - arming AI button only");
        wake_mask = 1ULL << PIN_POWER_BTN;
    }
    ESP_ERROR_CHECK(esp_sleep_enable_ext1_wakeup_io(
        wake_mask, ESP_EXT1_WAKEUP_ANY_LOW));
    gpio_deep_sleep_hold_en();

    ESP_LOGI(kTag, "Wake mask 0x%llx (GPIO%d/%d/%d any low, held pins excluded)",
             (unsigned long long)wake_mask,
             PIN_POWER_BTN, PIN_BTN_UP, PIN_BTN_DOWN);
    ESP_LOGI(kTag, "Entering deep sleep now");
    std::fflush(stdout);
    vTaskDelay(pdMS_TO_TICKS(50));
    esp_deep_sleep_start();
}
