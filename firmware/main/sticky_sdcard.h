// MicroSD on the shared SPI2 bus (pins in pin_config.h: CS=8, DETECT=11
// active-low, EN=10 power switch; MOSI/MISO/CLK shared with the SSD1677).
//
// Contract:
// - sticky_sdcard_init() must run AFTER tiny_display_init() — it adds an
//   sdspi device to the ALREADY-initialized SPI2 bus and never re-inits it.
// - The mount is persistent (unlike the vendor demo's mount-per-read):
//   /sdcard stays live so gallery nav reads are instant. The ESP-IDF SPI
//   master bus lock serializes SD vs panel transactions by CS; our own
//   mutex only guards mount/unmount state transitions.
// - No card at boot is a clean, silent skip — boot must not change.
// - sticky_sdcard_prepare_sleep() unmounts + drops SD_EN before deep sleep
//   (the vendor power path holds SD_EN low in sleep).
// - sticky_sdcard_ensure() is the hot-plug tolerance: call it before file
//   I/O; it reconciles the detect pin with mount state (yank -> unmount,
//   insert -> mount) instead of crashing on a vanished FAT.
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define STICKY_SD_MOUNT_POINT "/sdcard"

// One-time GPIO setup + first mount attempt if a card is present.
// Returns ESP_OK even when no card is inserted (absence is not an error).
esp_err_t sticky_sdcard_init(void);

// Card physically in the slot right now (DETECT pin, active low).
bool sticky_sdcard_present(void);

// FAT is mounted at /sdcard and usable.
bool sticky_sdcard_mounted(void);

// Reconcile detect pin with mount state. Call before file I/O.
// Returns ESP_OK when mounted and usable afterwards; ESP_ERR_NOT_FOUND
// when no card is present.
esp_err_t sticky_sdcard_ensure(void);

// Explicit mount/unmount (ensure() is usually what you want).
esp_err_t sticky_sdcard_mount(void);
esp_err_t sticky_sdcard_unmount(void);

// Capacity report. Any output pointer may be NULL. ESP_ERR_INVALID_STATE
// when not mounted.
esp_err_t sticky_sdcard_info(uint64_t *total_bytes, uint64_t *free_bytes);

// Sleep discipline: unmount + SD_EN low. Safe to call with no card.
void sticky_sdcard_prepare_sleep(void);

// DESTRUCTIVE: format the card FAT32 and mount it. Exists because 128GB+
// SDXC cards ship exFAT, which ESP-IDF FATFS cannot read (mount fails with
// FR_NO_FILESYSTEM). Never called automatically — only from the explicit,
// confirm-guarded `sd format yes` verb.
esp_err_t sticky_sdcard_format(void);

#ifdef __cplusplus
}
#endif
