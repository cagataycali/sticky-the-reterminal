// MicroSD driver — see sticky_sdcard.h for the contract.
//
// Adapted from vendor/Sticky_dashboard_demo sticky_sdcard.cpp with two
// deliberate departures:
//  1. PERSISTENT MOUNT. The demo mounts+unmounts around every read; we keep
//     /sdcard live so gallery page flips are disk reads, not mount cycles.
//     Bus safety is the SPI master driver's bus lock (SD and the SSD1677
//     are separate CS devices on SPI2; the driver serializes transactions).
//  2. HOT-PLUG RECONCILE instead of assuming the card stays put: ensure()
//     re-reads DETECT and repairs state either direction.
#include "sticky_sdcard.h"

#include <cstring>

#include "driver/gpio.h"
#include "driver/sdspi_host.h"
#include "esp_log.h"
#include "esp_vfs_fat.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "pin_config.h"
#include "sdmmc_cmd.h"

namespace {

constexpr char kTag[] = "sticky_sdcard";
constexpr TickType_t kPowerOnDelay = pdMS_TO_TICKS(100);  // vendor sequencing

bool s_gpio_ready = false;
sdmmc_card_t *s_card = nullptr;          // non-null == mounted
SemaphoreHandle_t s_lock = nullptr;      // guards mount/unmount transitions

bool detect_inserted() {
    return gpio_get_level(static_cast<gpio_num_t>(PIN_SD_DETECT)) == 0;
}

void set_power(bool on) {
    gpio_set_level(static_cast<gpio_num_t>(PIN_SD_EN), on ? 1 : 0);
}

// Caller holds s_lock.
esp_err_t mount_locked() {
    if (s_card) return ESP_OK;
    if (!detect_inserted()) return ESP_ERR_NOT_FOUND;

    gpio_set_level(static_cast<gpio_num_t>(PIN_SD_CS), 1);
    set_power(true);
    vTaskDelay(kPowerOnDelay);  // vendor: card needs ~100ms after SD_EN

    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = SPI2_HOST;

    sdspi_device_config_t slot = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot.gpio_cs = static_cast<gpio_num_t>(PIN_SD_CS);
    slot.host_id = SPI2_HOST;
    slot.gpio_cd = GPIO_NUM_NC;  // we poll DETECT ourselves
    slot.gpio_wp = GPIO_NUM_NC;

    esp_vfs_fat_sdmmc_mount_config_t mcfg = {};
    mcfg.format_if_mount_failed = false;  // never eat the owner's card
    mcfg.max_files = 4;
    mcfg.allocation_unit_size = 16 * 1024;

    sdmmc_card_t *card = nullptr;
    esp_err_t err = esp_vfs_fat_sdspi_mount(
        STICKY_SD_MOUNT_POINT, &host, &slot, &mcfg, &card);
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "mount failed: %s", esp_err_to_name(err));
        gpio_set_level(static_cast<gpio_num_t>(PIN_SD_CS), 1);
        set_power(false);
        return err;
    }
    s_card = card;
    ESP_LOGI(kTag, "mounted %s: %s %.1f GB", STICKY_SD_MOUNT_POINT,
             card->cid.name,
             (double)card->csd.capacity * card->csd.sector_size / 1e9);
    return ESP_OK;
}

// Caller holds s_lock.
esp_err_t unmount_locked(bool power_off) {
    if (!s_card) {
        if (power_off) set_power(false);
        return ESP_OK;
    }
    esp_err_t err = esp_vfs_fat_sdcard_unmount(STICKY_SD_MOUNT_POINT, s_card);
    s_card = nullptr;
    gpio_set_level(static_cast<gpio_num_t>(PIN_SD_CS), 1);
    if (power_off) set_power(false);
    ESP_LOGI(kTag, "unmounted (%s)", esp_err_to_name(err));
    return err;
}

// Caller holds s_lock. DESTRUCTIVE. Uses the mount path's
// format_if_mount_failed rail: FATFS f_mkfs picks FAT32 for a card this
// size (exFAT is compiled out, so FM_ANY cannot pick it back).
esp_err_t format_locked() {
    unmount_locked(false);
    if (!detect_inserted()) return ESP_ERR_NOT_FOUND;

    gpio_set_level(static_cast<gpio_num_t>(PIN_SD_CS), 1);
    set_power(true);
    vTaskDelay(kPowerOnDelay);

    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = SPI2_HOST;

    sdspi_device_config_t slot = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot.gpio_cs = static_cast<gpio_num_t>(PIN_SD_CS);
    slot.host_id = SPI2_HOST;
    slot.gpio_cd = GPIO_NUM_NC;
    slot.gpio_wp = GPIO_NUM_NC;

    esp_vfs_fat_sdmmc_mount_config_t mcfg = {};
    mcfg.format_if_mount_failed = true;  // the one sanctioned use
    mcfg.max_files = 4;
    mcfg.allocation_unit_size = 16 * 1024;

    sdmmc_card_t *card = nullptr;
    ESP_LOGW(kTag, "FORMAT: wiping card to FAT32 (this takes a while)");
    esp_err_t err = esp_vfs_fat_sdspi_mount(
        STICKY_SD_MOUNT_POINT, &host, &slot, &mcfg, &card);
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "format/mount failed: %s", esp_err_to_name(err));
        gpio_set_level(static_cast<gpio_num_t>(PIN_SD_CS), 1);
        set_power(false);
        return err;
    }
    // The mount above may have succeeded WITHOUT formatting (fs was valid).
    // The verb's contract is "wipe": format explicitly now that it's mounted.
    err = esp_vfs_fat_sdcard_format(STICKY_SD_MOUNT_POINT, card);
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "explicit format failed: %s", esp_err_to_name(err));
        esp_vfs_fat_sdcard_unmount(STICKY_SD_MOUNT_POINT, card);
        gpio_set_level(static_cast<gpio_num_t>(PIN_SD_CS), 1);
        set_power(false);
        return err;
    }
    s_card = card;
    ESP_LOGI(kTag, "formatted FAT32 and mounted");
    return ESP_OK;
}

}  // namespace

extern "C" esp_err_t sticky_sdcard_init(void) {
    if (s_gpio_ready) return ESP_OK;
    if (!s_lock) s_lock = xSemaphoreCreateMutex();
    if (!s_lock) return ESP_ERR_NO_MEM;

    gpio_config_t out = {};
    out.pin_bit_mask = (1ULL << PIN_SD_EN) | (1ULL << PIN_SD_CS);
    out.mode = GPIO_MODE_OUTPUT;
    esp_err_t err = gpio_config(&out);
    if (err != ESP_OK) return err;
    gpio_set_level(static_cast<gpio_num_t>(PIN_SD_CS), 1);
    set_power(false);

    gpio_config_t in = {};
    in.pin_bit_mask = 1ULL << PIN_SD_DETECT;
    in.mode = GPIO_MODE_INPUT;
    in.pull_up_en = GPIO_PULLUP_ENABLE;
    err = gpio_config(&in);
    if (err != ESP_OK) return err;
    s_gpio_ready = true;

    // First mount attempt — absence of a card is a clean skip, not an error.
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (detect_inserted()) {
        const esp_err_t merr = mount_locked();
        if (merr != ESP_OK)
            ESP_LOGW(kTag, "card present but mount failed: %s",
                     esp_err_to_name(merr));
    } else {
        ESP_LOGI(kTag, "no card in slot");
    }
    xSemaphoreGive(s_lock);
    return ESP_OK;
}

extern "C" bool sticky_sdcard_present(void) {
    return s_gpio_ready && detect_inserted();
}

extern "C" bool sticky_sdcard_mounted(void) {
    return s_card != nullptr;
}

extern "C" esp_err_t sticky_sdcard_ensure(void) {
    if (!s_gpio_ready) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    esp_err_t err;
    if (!detect_inserted()) {
        // Yanked: tear down the stale FAT so nothing writes into the void.
        unmount_locked(true);
        err = ESP_ERR_NOT_FOUND;
    } else {
        err = mount_locked();
    }
    xSemaphoreGive(s_lock);
    return err;
}

extern "C" esp_err_t sticky_sdcard_mount(void) {
    if (!s_gpio_ready) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const esp_err_t err = mount_locked();
    xSemaphoreGive(s_lock);
    return err;
}

extern "C" esp_err_t sticky_sdcard_unmount(void) {
    if (!s_gpio_ready) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const esp_err_t err = unmount_locked(true);
    xSemaphoreGive(s_lock);
    return err;
}

extern "C" esp_err_t sticky_sdcard_info(uint64_t *total_bytes,
                                        uint64_t *free_bytes) {
    if (!s_card) return ESP_ERR_INVALID_STATE;
    uint64_t total = 0, free_b = 0;
    const esp_err_t err =
        esp_vfs_fat_info(STICKY_SD_MOUNT_POINT, &total, &free_b);
    if (err != ESP_OK) return err;
    if (total_bytes) *total_bytes = total;
    if (free_bytes) *free_bytes = free_b;
    return ESP_OK;
}

extern "C" esp_err_t sticky_sdcard_format(void) {
    if (!s_gpio_ready) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const esp_err_t err = format_locked();
    xSemaphoreGive(s_lock);
    return err;
}

extern "C" void sticky_sdcard_prepare_sleep(void) {
    if (!s_gpio_ready) return;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    unmount_locked(true);  // SD_EN low matches the vendor sleep path
    xSemaphoreGive(s_lock);
}
