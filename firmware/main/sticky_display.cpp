#include "sticky_display.h"

#include <cstring>
#include <new>

#include "canvas.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "epaper_panel.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "pin_config.h"

namespace {

constexpr size_t kFramebufferStride = kStickyDisplayWidth / 4U;
constexpr size_t kFramebufferSize = kFramebufferStride * kStickyDisplayHeight;
constexpr size_t kMonochromeStride = kStickyDisplayWidth / 8U;

seeed_epaper_panel_handle_t s_panel = nullptr;
spi_device_handle_t s_spi_device = nullptr;
uint8_t *s_framebuffer = nullptr;
uint8_t *s_rotated_framebuffer = nullptr;
Canvas *s_canvas = nullptr;

uint8_t reverse_pixel_order(uint8_t packed_pixels)
{
    return static_cast<uint8_t>(((packed_pixels & 0x03U) << 6) |
                                ((packed_pixels & 0x0CU) << 2) |
                                ((packed_pixels & 0x30U) >> 2) |
                                ((packed_pixels & 0xC0U) >> 6));
}

void rotate_framebuffer_180(const uint8_t *source, uint8_t *destination)
{
    // Four 2-bit pixels are packed into each byte. Reversing both the byte
    // order and the four pixel groups produces a complete 180-degree rotation.
    for (size_t source_index = 0; source_index < kFramebufferSize; ++source_index) {
        const size_t destination_index = kFramebufferSize - 1U - source_index;
        destination[destination_index] = reverse_pixel_order(source[source_index]);
    }
}

void convert_gray4_to_monochrome_in_place(uint8_t *buffer)
{
    // Canvas pixels use two bits: Black=0 and White=3. The conversion runs
    // forward safely in place because each 1bpp output row is half the size
    // of its 2bpp input row. Values 2 and 3 map to white; 0 and 1 map to black.
    for (size_t y = 0; y < kStickyDisplayHeight; ++y) {
        const size_t source_row = y * kFramebufferStride;
        const size_t destination_row = y * kMonochromeStride;

        for (size_t byte_x = 0; byte_x < kMonochromeStride; ++byte_x) {
            uint8_t monochrome_pixels = 0;
            for (size_t bit = 0; bit < 8U; ++bit) {
                const size_t pixel_x = byte_x * 8U + bit;
                const uint8_t packed = buffer[source_row + pixel_x / 4U];
                const uint8_t shift =
                    static_cast<uint8_t>((3U - (pixel_x & 0x03U)) * 2U);
                const uint8_t gray = static_cast<uint8_t>((packed >> shift) & 0x03U);
                if (gray >= 2U) {
                    monochrome_pixels |= static_cast<uint8_t>(1U << (7U - bit));
                }
            }
            buffer[destination_row + byte_x] = monochrome_pixels;
        }
    }
}

}  // namespace

esp_err_t sticky_display_init()
{
    if (s_panel != nullptr && s_canvas != nullptr) {
        return ESP_OK;
    }

    gpio_config_t power_config = {};
    power_config.pin_bit_mask = 1ULL << PIN_EPD_EN;
    power_config.mode = GPIO_MODE_OUTPUT;
    power_config.pull_up_en = GPIO_PULLUP_DISABLE;
    power_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    power_config.intr_type = GPIO_INTR_DISABLE;
    ESP_RETURN_ON_ERROR(gpio_config(&power_config), "sticky_display", "configure display power");
    ESP_RETURN_ON_ERROR(gpio_set_level(static_cast<gpio_num_t>(PIN_EPD_EN), 1),
                        "sticky_display", "enable display power");
    vTaskDelay(pdMS_TO_TICKS(100));

    spi_bus_config_t bus_config = {};
    bus_config.mosi_io_num = PIN_EPD_MOSI;
    bus_config.miso_io_num = PIN_EPD_MISO;
    bus_config.sclk_io_num = PIN_EPD_CLK;
    // ESP-IDF requires every unused SPI data pin to be -1. Leaving these
    // zero-initialized assigns GPIO0 to the SPI bus, but GPIO0 is also the
    // sensor I2C SCL pin. That made SHT40 work before display init and fail
    // immediately afterwards.
    bus_config.quadwp_io_num = -1;
    bus_config.quadhd_io_num = -1;
    bus_config.data4_io_num = -1;
    bus_config.data5_io_num = -1;
    bus_config.data6_io_num = -1;
    bus_config.data7_io_num = -1;
    bus_config.max_transfer_sz = kStickyDisplayWidth * kStickyDisplayHeight / 8;
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI2_HOST, &bus_config, SPI_DMA_CH_AUTO),
                        "sticky_display", "initialize SPI2");

    spi_device_interface_config_t device_config = {};
    device_config.clock_speed_hz = 10 * 1000 * 1000;
    device_config.mode = 0;
    device_config.spics_io_num = PIN_EPD_CS;
    device_config.queue_size = 1;
    ESP_RETURN_ON_ERROR(spi_bus_add_device(SPI2_HOST, &device_config, &s_spi_device),
                        "sticky_display", "add display to SPI2");

    seeed_epaper_panel_config_t panel_config = {};
    panel_config.spi_handle = s_spi_device;
    panel_config.pin_dc = static_cast<gpio_num_t>(PIN_EPD_DC);
    panel_config.pin_rst = static_cast<gpio_num_t>(PIN_EPD_RST);
    panel_config.pin_busy = static_cast<gpio_num_t>(PIN_EPD_BUSY);
    panel_config.pin_enable = GPIO_NUM_NC;
    panel_config.busy_timeout_ms = 10000;
    panel_config.reset_low_ms = 10;
    panel_config.reset_high_ms = 10;
    panel_config.busy_level = 1;
    panel_config.enable_level = 1;
    panel_config.mirror_x = true;
    ESP_RETURN_ON_ERROR(
        seeed_epaper_new_panel(SEEED_EPAPER_PANEL_SSD1677, &panel_config, &s_panel),
        "sticky_display", "create SSD1677 panel");

    s_framebuffer = static_cast<uint8_t *>(
        heap_caps_malloc(kFramebufferSize, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    s_rotated_framebuffer = static_cast<uint8_t *>(
        heap_caps_malloc(kFramebufferSize, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (s_framebuffer == nullptr || s_rotated_framebuffer == nullptr) {
        heap_caps_free(s_framebuffer);
        heap_caps_free(s_rotated_framebuffer);
        s_framebuffer = nullptr;
        s_rotated_framebuffer = nullptr;
        return ESP_ERR_NO_MEM;
    }

    s_canvas = new (std::nothrow)
        Canvas(kStickyDisplayWidth, kStickyDisplayHeight, s_framebuffer, kFramebufferSize);
    if (s_canvas == nullptr) {
        heap_caps_free(s_framebuffer);
        heap_caps_free(s_rotated_framebuffer);
        s_framebuffer = nullptr;
        s_rotated_framebuffer = nullptr;
        return ESP_ERR_NO_MEM;
    }
    s_canvas->clear();
    return ESP_OK;
}

Canvas *sticky_display_canvas()
{
    return s_canvas;
}

esp_err_t sticky_display_refresh()
{
    if (s_panel == nullptr || s_canvas == nullptr || s_rotated_framebuffer == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }

    rotate_framebuffer_180(s_canvas->data(), s_rotated_framebuffer);

    const seeed_epaper_area_t full_screen = {
        0, 0, kStickyDisplayWidth, kStickyDisplayHeight,
    };
    ESP_RETURN_ON_ERROR(
        seeed_epaper_panel_write_bitmap_gray4(
            s_panel, &full_screen, s_rotated_framebuffer, s_canvas->stride()),
        "sticky_display", "write gray4 framebuffer");
    return seeed_epaper_panel_commit(s_panel, &full_screen, SEEED_EPAPER_REFRESH_GRAY4);
}

esp_err_t sticky_display_refresh_partial()
{
    if (s_panel == nullptr || s_canvas == nullptr ||
        s_rotated_framebuffer == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }

    // SSD1677 applies its partial waveform across the panel even when only a
    // small RAM window is written. Send a complete monochrome comparison frame
    // so unchanged pixels have identical previous/current values and remain
    // visually untouched. Callers should change only the intended Canvas area.
    rotate_framebuffer_180(s_canvas->data(), s_rotated_framebuffer);
    convert_gray4_to_monochrome_in_place(s_rotated_framebuffer);

    const seeed_epaper_area_t full_screen = {
        0, 0, kStickyDisplayWidth, kStickyDisplayHeight,
    };
    return seeed_epaper_panel_refresh_area(
        s_panel,
        &full_screen,
        s_rotated_framebuffer,
        kMonochromeStride,
        SEEED_EPAPER_PIXEL_FORMAT_MONO1_MSB,
        SEEED_EPAPER_REFRESH_PARTIAL);
}

// §12 wake paint: partial refresh with an EXPLICIT previous frame. After a
// deep-sleep power-down the controller's old-RAM plane is garbage, so a plain
// partial would collage (check-23's finding, power-down edition). The vendor
// driver's write_bitmap_diff takes both planes; the caller supplies what is
// PHYSICALLY on the glass (the goodbye card, redrawn deterministically).
// prev_canvas is a gray4 canvas-format buffer (unrotated); we rotate+mono it
// exactly like the current framebuffer so both planes go through one path.
esp_err_t sticky_display_refresh_partial_baseline(const uint8_t *prev_canvas)
{
    if (s_panel == nullptr || s_canvas == nullptr ||
        s_rotated_framebuffer == nullptr || prev_canvas == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    uint8_t *prev_rot = static_cast<uint8_t *>(
        heap_caps_malloc(kFramebufferSize, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (prev_rot == nullptr) return ESP_ERR_NO_MEM;
    rotate_framebuffer_180(prev_canvas, prev_rot);
    convert_gray4_to_monochrome_in_place(prev_rot);
    rotate_framebuffer_180(s_canvas->data(), s_rotated_framebuffer);
    convert_gray4_to_monochrome_in_place(s_rotated_framebuffer);
    const seeed_epaper_area_t full_screen = {
        0, 0, kStickyDisplayWidth, kStickyDisplayHeight,
    };
    esp_err_t err = seeed_epaper_panel_write_bitmap_diff(
        s_panel, &full_screen, prev_rot, s_rotated_framebuffer,
        kMonochromeStride, SEEED_EPAPER_PIXEL_FORMAT_MONO1_MSB,
        SEEED_EPAPER_REFRESH_PARTIAL);
    heap_caps_free(prev_rot);
    return err;
}

esp_err_t sticky_display_refresh_monochrome()
{
    if (s_panel == nullptr || s_canvas == nullptr ||
        s_rotated_framebuffer == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }

    rotate_framebuffer_180(s_canvas->data(), s_rotated_framebuffer);
    convert_gray4_to_monochrome_in_place(s_rotated_framebuffer);

    const seeed_epaper_area_t full_screen = {
        0, 0, kStickyDisplayWidth, kStickyDisplayHeight,
    };
    return seeed_epaper_panel_refresh_area(
        s_panel,
        &full_screen,
        s_rotated_framebuffer,
        kMonochromeStride,
        SEEED_EPAPER_PIXEL_FORMAT_MONO1_MSB,
        SEEED_EPAPER_REFRESH_FULL);
}

esp_err_t sticky_display_clear()
{
    if (s_panel == nullptr || s_canvas == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    s_canvas->clear();
    return seeed_epaper_panel_clear(s_panel, true, SEEED_EPAPER_REFRESH_FULL);
}

esp_err_t sticky_display_sleep()
{
    if (s_panel == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }

    ESP_RETURN_ON_ERROR(
        seeed_epaper_panel_sleep(s_panel),
        "sticky_display", "put panel to sleep");
    return gpio_set_level(static_cast<gpio_num_t>(PIN_EPD_EN), 0);
}
