// tiny_main — the tiny firmware for reTerminal Sticky (M1 splash + M2 cards).
//
// Boot order is a contract (docs/ARCHITECTURE.md): board_init() FIRST —
// it owns the power latch (GPIO45/46); without it the device shuts itself
// off moments after the button is released. Then display, then splash,
// then (M2) the card renderer demo so the renderer is proven on hardware.
// Wi-Fi provisioning + the node loop land in M3.
#include <cstdio>
#include "nvs.h"
#include "nvs_flash.h"
#include "cJSON.h"
#include "esp_log.h"
#include "driver/uart.h"
#include "driver/uart_vfs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "board.h"
#include "canvas.h"
#include "sticky_buzzer.h"
#include "sticky_display.h"
#include "sticky_power.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "tiny/tiny_display.h"
#include "tiny/tiny_touch.h"
#include "tiny/tiny_config.h"
#include "tiny/tiny_wifi.h"
#include "sticky_battery.h"
#include "sticky_sdcard.h"
#include "tiny/tiny_audio.h"
#include "tiny/tiny_button.h"
#include "tiny/tiny_lock.h"
#include "tiny/tiny_orient.h"
#include "tiny/tiny_sensors.h"
#include "tiny/tiny_time.h"
#include "tiny/tiny_node.h"
#include "tiny/tiny_provision.h"
#include "tiny/tiny_shell.h"
#include "tiny/tiny_onboard.h"
#include <string.h>
#include "esp_system.h"

static const char *TAG = "tiny";
#include "tiny/tiny_version.h"

#include "tiny/tiny_bootmark.h"

// The M2 demo card — title + kv rows + buttons, exactly the shape the
// backend's ```card fenced block emits (docs/API_CONTRACT.md).
extern "C" void app_main(void)
{
    ESP_LOGI(TAG, "tiny-sticky fw %s booting", TINY_FW_VERSION);
    sticky_power_log_wakeup_reason();
    if (esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_TIMER)
        ESP_LOGI(TAG, "woke from deep sleep by timer");
    // §12: waking from deep sleep is the pocket glance — pull out, press,
    // look. A wake is NOT a boot ceremony: skip the splash and its 3s read
    // pause, paint home (whose title bar IS the glance strip: RTC time,
    // battery, w-) before any network exists, and measure it.
    const bool glance_wake =
        esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_EXT1 ||
        esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_TIMER;

    ESP_ERROR_CHECK(board_init());               // power latch lives here
    tiny_bootmark_stage(1);
    const int64_t t_board = esp_timer_get_time();
    ESP_ERROR_CHECK(tiny_display_init());
    tiny_bootmark_stage(2);
    const int64_t t_disp = esp_timer_get_time();
    ESP_ERROR_CHECK(sticky_buzzer_init());
    // Silent mode survives reboot + OTA — restore BEFORE the boot
    // chime below, or muting would un-mute for exactly one beep per boot.
    sticky_buzzer_set_silent(tiny_config_silent_get());
    sticky_buzzer_set_soft(tiny_config_beepsoft_get());  // volume tier too
    // Rotation lock survives reboot too — restore before the
    // orient watcher starts, or gravity wins one turn before the lock lands.
    tiny_orient_set_auto(tiny_config_rotauto_get());
    tiny_bootmark_stage(3);

    if (!glance_wake) {
        ESP_ERROR_CHECK(tiny_display_splash(TINY_FW_VERSION,
                                            "wifi: not provisioned yet - M3"));
        sticky_buzzer_beep();            // audible "I'm alive"
        ESP_LOGI(TAG, "splash rendered - tiny is on the wall");
    }
    tiny_bootmark_stage(4);

    // The 3s "read the version" pause was replaced with the version text
    // itself — a bar that parks for three seconds is a loading bar lying
    // about loading. The splash now lives exactly as long as boot does.

    // The paint's ONLY data dependencies: battery (glance cluster reads the
    // gauge) and the RTC clock (cluster time before wifi exists). Both are
    // millisecond I2C reads — they stay ahead of the paint on every path.
    ESP_LOGI(TAG, "battery init: %s",
             esp_err_to_name(sticky_battery_init(board_sensor_i2c_bus())));
    // The PCF8563 is battery-backed, so it can hand us a clock before wifi
    // exists. ESP_ERR_INVALID_STATE here just means "never set" (factory VL
    // flag) — SNTP below fixes that permanently.
    ESP_LOGI(TAG, "clock from rtc: %s",
             esp_err_to_name(tiny_time_init_from_rtc()));
    tiny_bootmark_stage(5);

    // §12: everything a HUMAN needs before their first tap — but nothing the
    // PAINT needs. On a glance wake these run AFTER the glass speaks (the
    // 80% interaction is look-and-pocket; a tap 300ms later still wins).
    // Cold boot keeps the old order: the 3s splash pause absorbs all of it.
    const auto init_inputs = [&]() {
        // M2b: GT911 touch -> button hit-regions -> UI_TAP events + beep.
        ESP_LOGI(TAG, "touch init: %s", esp_err_to_name(tiny_touch_init()));
        // M5: mic power rail + buzzer.
        ESP_LOGI(TAG, "audio init: %s", esp_err_to_name(tiny_audio_init()));
        // M9: SHT40 + IMU on I2C1 — the `sensors` verb. Logged, never
        // ESP_ERROR_CHECK'd: a dead sensor must not stop the boot.
        ESP_LOGI(TAG, "sensors init: %s", esp_err_to_name(tiny_sensors_init()));
        ESP_LOGI(TAG, "buttons init: %s", esp_err_to_name(tiny_button_init()));
        // §11 pocket lockout: armed after buttons exist (the chord reads
        // their GPIOs raw) — boots UNLOCKED; auto-entry is IMU-driven.
        ESP_LOGI(TAG, "lockout init: %s", esp_err_to_name(tiny_lock_init()));
    };
    // P0-BOUNCE bisect arm (0.25.36): the splash_progress partials are
    // SUSPECT #1 for the 0.25.35 trial crash (partial-after-monochrome-full,
    // never witnessed on hardware). They are stripped in this probe: if the
    // OTA lands, the partial is convicted; if it still bounces, the flight
    // recorder above names the stage. The progress bar returns once cleared.
    if (!glance_wake) {
        init_inputs();
    }
    tiny_bootmark_stage(6);

    // §12 wake paint: arm the diff baseline (redrawn goodbye card) so the
    // shell's first commit is one ~300-500ms partial instead of the ~2s full
    // flash. INVALID_STATE = cold boot or power loss: full flash, honestly.
    if (glance_wake)
        ESP_LOGI(TAG, "wake baseline: %s",
                 esp_err_to_name(tiny_display_arm_wake_baseline()));
    // M10: home screen up before any network — navigation must work offline.
    ESP_LOGI(TAG, "shell init: %s", esp_err_to_name(tiny_shell_init()));
    tiny_bootmark_stage(7);
    // §12 measurement: reset-to-home-on-glass, in ms, exposed via `status`.
    // On a glance wake this is the number the acceptance names (<2000).
    tiny_node_set_first_paint((int)(esp_timer_get_time() / 1000), glance_wake);
    // §12 breakdown: where does the wake budget actually go? board = power
    // latch + buses; display = SSD1677 panel init; paint = shell render +
    // the e-ink full refresh. The floor argument, if one must be made to the
    // designer, is made with these numbers and not with adjectives.
    tiny_node_set_paint_breakdown((int)(t_board / 1000),
                                  (int)((t_disp - t_board) / 1000));
    ESP_LOGI(TAG, "first paint: %lld ms after wake (glance_wake=%d)",
             esp_timer_get_time() / 1000, (int)glance_wake);
    if (glance_wake) init_inputs();
    // Designer's lazy clean-flash (ruling 67b8f22): a partial waveform leaves
    // residue; one gray4 re-render a beat later self-heals it while the human
    // is already reading the card. 6s = past the glance, before the pocket.
    if (glance_wake) {
        vTaskDelay(pdMS_TO_TICKS(6000));
        tiny_display_rerender();
    }

    // M11: the UI follows the body. Started AFTER the shell so its first commit
    // has a cached card to turn, and after the IMU so the first sample is real.
    ESP_LOGI(TAG, "auto-rotate: %s", esp_err_to_name(tiny_orient_start()));

    // MicroSD (gallery + photo library). AFTER first paint so the 100ms power
    // sequencing + mount never touch the glance budget; after display init so
    // SPI2 exists (the driver adds a device, never re-inits the bus). A
    // missing card is a clean skip inside init — boot stays identical.
    ESP_LOGI(TAG, "sdcard init: %s", esp_err_to_name(sticky_sdcard_init()));

    // M3: load config; join wifi when provisioned with at least one network.
    tiny_config_t cfg;
    if (tiny_config_load(&cfg) == ESP_OK && cfg.network_count > 0) {
        esp_err_t werr = tiny_wifi_connect(&cfg);
        ESP_LOGI(TAG, "wifi: %s", esp_err_to_name(werr));
        tiny_bootmark_stage(8);
        // The wrong-password journey used to end in a silent lie —
        // "saved ✓", reboot, join fails, and the walk lands on some OTHER
        // network (or nothing) with the human never told. If a network was
        // JUST added (breadcrumb written by wifi_join, consumed here so the
        // confession happens exactly once) and THIS walk tried it and failed,
        // say so on the glass — the human who typed that password is watching
        // this boot. Runs regardless of werr: joining a different network
        // does not make the poisoned entry honest.
        {
            nvs_handle_t nh;
            char just[33] = {0};
            size_t jlen = sizeof just;
            if (nvs_open("tinywifi", NVS_READWRITE, &nh) == ESP_OK) {
                if (nvs_get_str(nh, "just_added", just, &jlen) != ESP_OK)
                    just[0] = 0;
                if (just[0]) { nvs_erase_key(nh, "just_added"); nvs_commit(nh); }
                nvs_close(nh);
            }
            int reason = 0;
            const bool just_failed = just[0] && tiny_wifi_walk_failed(just, &reason);
            // First run: the link page owns this verdict (same reason families,
            // in-flow buttons instead of a dead-end card). The provisioned
            // device keeps the classic confession below.
            if (!tiny_config_is_provisioned(&cfg)) {
                tiny_onboard_note_link(werr == ESP_OK, just[0] ? just : NULL,
                                       just_failed ? reason : 0);
                // Phone path stays open while the shell owns the glass.
                ESP_LOGI(TAG, "portal over sta: %s",
                         esp_err_to_name(tiny_provision_portal_apsta()));
                tiny_onboard_render();
            } else if (just_failed) {
                ESP_LOGW(TAG, "boot confession: \"%s\" failed, reason %d",
                         just, reason);
                // 201 = AP not found (range problem); 15/202/204 = handshake/
                // auth (password problem). Same cJSON-escape rule as
                // wifi_join: the ssid never touches hand-built JSON.
                cJSON *c = cJSON_CreateObject();
                if (c) {
                    cJSON_AddStringToObject(c, "type", "text");
                    cJSON_AddStringToObject(c, "card_id", "joinfail_boot");
                    cJSON_AddStringToObject(c, "title", "couldn't join");
                    char body[200];
                    // A confession card must not confess the wrong
                    // sin. 201 = AP not found, 200 = beacon timeout (walked
                    // out of range mid-join) → range copy. Only the auth
                    // family {2,15,202,204,205} may blame the password;
                    // anything else gets the neutral truth with the reason
                    // number as its receipt.
                    const bool range = (reason == 200 || reason == 201);
                    const bool auth  = (reason == 2 || reason == 15 ||
                                        reason == 202 || reason == 204 ||
                                        reason == 205);
                    if (range) {
                        snprintf(body, sizeof body,
                                 "%.32s: I can't see this network from here. "
                                 "settings > wifi to rescan.", just);
                    } else if (auth) {
                        snprintf(body, sizeof body,
                                 "%.32s didn't accept the password - wrong "
                                 "password? settings > wifi to retry.", just);
                    } else {
                        snprintf(body, sizeof body,
                                 "couldn't join %.32s (reason %d). "
                                 "settings > wifi to retry.", just, reason);
                    }
                    cJSON_AddStringToObject(c, "body", body);
                    cJSON *btns = cJSON_AddArrayToObject(c, "buttons");
                    cJSON_AddItemToArray(btns, cJSON_CreateString("home"));
                    char *cs = cJSON_PrintUnformatted(c);
                    cJSON_Delete(c);
                    if (cs) {
                        tiny_display_render_card(cs);
                        free(cs);
                    }
                }
            }
        }
        if (werr == ESP_OK) {
            // One sync per boot, written back to the RTC. Bounded wait: a
            // sulking NTP server must not keep the node loop off the relay.
            ESP_LOGI(TAG, "clock sntp: %s",
                     esp_err_to_name(tiny_time_sync_sntp(6000)));
        }
        if (werr == ESP_OK && tiny_config_is_provisioned(&cfg)) {
            esp_err_t nerr = tiny_node_start();
            ESP_LOGI(TAG, "node loop: %s", esp_err_to_name(nerr));
        } else if (werr == ESP_OK) {
            ESP_LOGI(TAG, "node loop: waiting for device_id+token (config incomplete)");
        }
    } else {
        // No stored network: open the setup portal (softAP + HTTP). The
        // serial console below stays alive as the bench path — the portal
        // and the console both funnel into tiny_config_merge_json.
        esp_err_t perr = tiny_provision_start();
        ESP_LOGI(TAG, "provision portal: %s", esp_err_to_name(perr));
    }

    // Serial card console (M2 bench harness, pre-Wi-Fi): any line on UART0
    // starting with '{' is parsed as a card and rendered. Drive it with
    // tools/render_card.py — this is how tiny tests on the device live.
    // stdin over UART needs the driver + VFS hookup, else getchar() is EOF forever.
    ESP_ERROR_CHECK(uart_driver_install(UART_NUM_0, 4096, 0, 0, NULL, 0));
    uart_vfs_dev_use_driver(UART_NUM_0);
    ESP_LOGI(TAG, "card console ready: send {json} line over serial");
    static char line[2048];
    size_t pos = 0;
    for (;;) {
        int ch = getchar();
        if (ch == EOF) { vTaskDelay(pdMS_TO_TICKS(50)); continue; }
        if (ch == '\r') continue;
        if (ch == '\n') {
            line[pos] = '\0';
            if (pos > 0 && line[0] == '{') {
                if (strstr(line, "\"config\"") != NULL) {
                    // bench provisioning: {"config":{networks,device_id,token,...}}
                    const char *key = strstr(line, "\"config\"");
                    const char *inner = strchr(key + 8, '{');
                    esp_err_t r = inner ? tiny_config_merge_json(inner)
                                        : ESP_ERR_INVALID_ARG;
                    printf("CONFIG_RESULT %s\n", esp_err_to_name(r));
                    if (r == ESP_OK) {
                        printf("rebooting to apply config\n");
                        vTaskDelay(pdMS_TO_TICKS(300));
                        esp_restart();
                    }
                } else {
                    esp_err_t r = tiny_display_render_card(line);
                    printf("CARD_RESULT %s\n", esp_err_to_name(r));
                }
            }
            pos = 0;
            continue;
        }
        if (pos < sizeof(line) - 1) line[pos++] = (char)ch;
        else pos = 0;  // overflow: drop the line
    }
}
