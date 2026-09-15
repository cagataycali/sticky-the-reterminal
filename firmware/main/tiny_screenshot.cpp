// tiny_screenshot — what is ON the panel, as a PNG, from the device itself.
// The canvas is 2 bits/pixel (4 grays) so the PNG is written EXACTLY as
// 2-bit grayscale with STORED deflate blocks: no compression library, fully
// deterministic, 800x480 → ~96.6KB (base64 ~129KB, far under the 6MB media
// cap). crc32 comes from ROM (esp_rom_crc32_le(0,…) == zlib crc32); adler32
// is 8 lines. Upload goes through the same tiny_upload_media path the mic
// uses, so the reply is a hosted URL the owner's surfaces can already show.
#include "tiny/tiny_screenshot.h"

#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_rom_crc.h"
#include "tiny/tiny_display.h"
#include "tiny/tiny_upload.h"

static const char *TAG = "tiny_shot";

#define W 800
#define H 480
#define ROW_BYTES (W * 2 / 8)          /* 200 */
#define RAW_LEN ((size_t)H * (ROW_BYTES + 1)) /* +1 filter byte per row */

static void put_u32(uint8_t *p, uint32_t v) {
    p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v;
}

// One PNG chunk: length + type + data + crc32(type||data).
static size_t chunk(uint8_t *dst, const char *type, const uint8_t *data,
                    size_t len) {
    put_u32(dst, (uint32_t)len);
    memcpy(dst + 4, type, 4);
    if (len) memcpy(dst + 8, data, len);
    uint32_t crc = esp_rom_crc32_le(0, dst + 4, len + 4);
    put_u32(dst + 8 + len, crc);
    return len + 12;
}

// Stage autopsy (P1 hunt 2026-08-26): the verb-level black box convicted
// `screenshot` 3/3 deterministic but not the stage. These markers ride the
// same RTC-noinit crumb; a panic now reads screenshot@snapshot/@png/@upload.
extern "C" void tiny_node_crumb_step(const char *step);

extern "C" esp_err_t tiny_screenshot_png(uint8_t **out, size_t *out_len) {
    if (!out || !out_len) return ESP_ERR_INVALID_ARG;
    uint8_t *g8 = NULL; size_t g8_len = 0;
    tiny_node_crumb_step("snapshot");
    esp_err_t err = tiny_display_snapshot_gray8(&g8, &g8_len);
    if (err != ESP_OK) return err;
    if (g8_len != (size_t)W * H) { free(g8); return ESP_ERR_INVALID_SIZE; }
    tiny_node_crumb_step("png");

    // Raw scanlines: filter byte 0, then 4 pixels/byte, 2 bits each,
    // MSB-first (PNG bit order). gray8 {00,55,AA,FF} >> 6 → 0..3.
    uint8_t *raw = (uint8_t *)heap_caps_malloc(RAW_LEN, MALLOC_CAP_SPIRAM);
    if (!raw) raw = (uint8_t *)malloc(RAW_LEN);
    if (!raw) { free(g8); return ESP_ERR_NO_MEM; }
    size_t ri = 0;
    for (int y = 0; y < H; ++y) {
        raw[ri++] = 0;  // filter: None
        for (int xb = 0; xb < ROW_BYTES; ++xb) {
            const uint8_t *px = g8 + (size_t)y * W + xb * 4;
            raw[ri++] = (uint8_t)(((px[0] >> 6) << 6) | ((px[1] >> 6) << 4) |
                                  ((px[2] >> 6) << 2) | (px[3] >> 6));
        }
    }
    free(g8);

    // zlib stream around STORED deflate blocks (≤65535 raw bytes each).
    uint32_t a = 1, b = 0;  // adler32 over raw
    for (size_t i = 0; i < RAW_LEN; ++i) {
        a = (a + raw[i]) % 65521;
        b = (b + a) % 65521;
    }
    const size_t nblocks = (RAW_LEN + 65534) / 65535;
    const size_t zlen = 2 + RAW_LEN + nblocks * 5 + 4;
    const size_t cap = 8 + 25 + (zlen + 12) + 12;  // sig+IHDR+IDAT+IEND
    uint8_t *png = (uint8_t *)heap_caps_malloc(cap, MALLOC_CAP_SPIRAM);
    if (!png) png = (uint8_t *)malloc(cap);
    if (!png) { free(raw); return ESP_ERR_NO_MEM; }

    static const uint8_t sig[8] = {0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'};
    memcpy(png, sig, 8);
    size_t n = 8;

    uint8_t ihdr[13];
    put_u32(ihdr, W); put_u32(ihdr + 4, H);
    ihdr[8] = 2;   // bit depth 2
    ihdr[9] = 0;   // grayscale
    ihdr[10] = ihdr[11] = ihdr[12] = 0;
    n += chunk(png + n, "IHDR", ihdr, 13);

    // Assemble IDAT payload in place (after the 8-byte chunk header slot).
    uint8_t *z = png + n + 8;
    size_t zi = 0;
    z[zi++] = 0x78; z[zi++] = 0x01;  // zlib: deflate, 32K window, no dict
    size_t off = 0;
    while (off < RAW_LEN) {
        size_t take = RAW_LEN - off > 65535 ? 65535 : RAW_LEN - off;
        z[zi++] = (off + take == RAW_LEN) ? 1 : 0;  // BFINAL on last block
        z[zi++] = take & 0xFF; z[zi++] = take >> 8;
        z[zi++] = ~take & 0xFF; z[zi++] = (~take >> 8) & 0xFF;
        memcpy(z + zi, raw + off, take);
        zi += take; off += take;
    }
    put_u32(z + zi, (b << 16) | a); zi += 4;
    free(raw);
    // IDAT header written directly — the payload was assembled in place at
    // png+n+8, so a memcpy through chunk() would overlap itself.
    put_u32(png + n, (uint32_t)zi);
    memcpy(png + n + 4, "IDAT", 4);
    uint32_t crc = esp_rom_crc32_le(0, png + n + 4, zi + 4);
    put_u32(png + n + 8 + zi, crc);
    n += zi + 12;

    n += chunk(png + n, "IEND", NULL, 0);

    *out = png;
    *out_len = n;
    ESP_LOGI(TAG, "png ready: %u bytes", (unsigned)n);
    return ESP_OK;
}

extern "C" esp_err_t tiny_screenshot_upload(char *url, size_t url_cap) {
    uint8_t *png = NULL; size_t len = 0;
    esp_err_t err = tiny_screenshot_png(&png, &len);
    if (err != ESP_OK) return err;
    tiny_node_crumb_step("upload");
    err = tiny_upload_media(png, len, "image/png", url, url_cap);
    free(png);
    return err;
}
