#pragma once

#include "esp_err.h"

class Canvas;

constexpr uint16_t kStickyDisplayWidth = 800;
constexpr uint16_t kStickyDisplayHeight = 480;

// Initializes SPI2, powers the SSD1677 panel, and creates the framebuffer.
esp_err_t sticky_display_init();

// Returns the drawing surface after sticky_display_init() succeeds.
Canvas *sticky_display_canvas();

// Rotates the logical framebuffer 180 degrees and sends it to the panel.
// Canvas coordinates remain a normal 800 x 480, top-left-origin coordinate system.
esp_err_t sticky_display_refresh();

// Refreshes black-and-white changes made to Canvas since the previous refresh.
// SSD1677 requires a full comparison frame to preserve unchanged pixels; that
// transfer and the physical 180-degree rotation are handled internally.
esp_err_t sticky_display_refresh_partial();
esp_err_t sticky_display_refresh_partial_baseline(const uint8_t *prev_canvas);

// Refreshes the full screen with the panel's black-and-white waveform.
// Intended for pages that contain only Black and White canvas pixels.
esp_err_t sticky_display_refresh_monochrome();

// Clears the physical panel to white and clears the framebuffer.
esp_err_t sticky_display_clear();

// Puts the panel controller to sleep and removes display power. The last
// e-paper image remains visible without power.
esp_err_t sticky_display_sleep();
