#pragma once

#include <cstddef>
#include <cstdint>

enum class GrayLevel : uint8_t {
    Black = 0,
    DarkGray = 1,
    LightGray = 2,
    White = 3,
};

// Rotation lives HERE, not in the renderer and not in the panel driver, because
// every drawing primitive in this class funnels through draw_pixel(): one
// logical->physical mapping in that single function rotates the entire UI, text
// and QR modules included, with no layout code aware of it. The buffer stays
// physically 800x480 (that is what the panel wants); only the coordinates the
// callers use turn. width()/height() are therefore LOGICAL — a portrait canvas
// reports 480x800 — so layout code that already asks the canvas for its size
// keeps working when the device is stood on its side.
enum class CanvasRotation : uint8_t {
    Landscape = 0,       // 0   deg — the wall-mounted default
    PortraitRight = 1,   // 90  deg
    LandscapeFlipped = 2,// 180 deg
    PortraitLeft = 3,    // 270 deg
};

class Canvas {
public:
    Canvas(uint16_t width, uint16_t height, uint8_t *buffer, size_t buffer_size);

    // Logical dimensions: swapped when the canvas is in a portrait rotation.
    uint16_t width() const { return portrait() ? height_ : width_; }
    uint16_t height() const { return portrait() ? width_ : height_; }
    uint16_t physical_width() const { return width_; }
    uint16_t physical_height() const { return height_; }
    size_t stride() const { return stride_; }

    CanvasRotation rotation() const { return rotation_; }
    void set_rotation(CanvasRotation r) { rotation_ = r; }
    bool portrait() const {
        return rotation_ == CanvasRotation::PortraitRight ||
               rotation_ == CanvasRotation::PortraitLeft;
    }

    // Physical (panel/touch) -> logical (card layout). The inverse of the
    // mapping inside draw_pixel, kept next to it so the two can never drift:
    // touch coordinates MUST turn with the picture or every button lies.
    void to_logical(int px, int py, int &lx, int &ly) const;
    void to_physical(int lx, int ly, int &px, int &py) const;
    // Logical rect -> physical rect. A 90-degree turn keeps rectangles
    // axis-aligned, so mapping both corners and normalising is exact. Used to
    // publish touch hit-regions in PANEL coordinates: the screenshot and the
    // finger then share one coordinate system whatever way up the device is.
    void rect_to_physical(int lx, int ly, int lw, int lh,
                          int &px, int &py, int &pw, int &ph) const;

    // Vertical clip window in LOGICAL space [y0, y1). While set, draw_pixel
    // drops rows outside it. This is what lets a scrolled card render its FULL
    // body (so content height can be measured) while only the body box between
    // the title rule and the button bar receives ink — the one primitive-level
    // change that gives every card type scrolling for free.
    void set_clip_y(int y0, int y1) { clip_on_ = true; clip_y0_ = y0; clip_y1_ = y1; }
    void clear_clip_y() { clip_on_ = false; }

    uint8_t *data() { return buffer_; }
    const uint8_t *data() const { return buffer_; }

    void clear(GrayLevel color = GrayLevel::White);
    void draw_pixel(int x, int y, GrayLevel color = GrayLevel::Black);
    void draw_line(int x0, int y0, int x1, int y1,
                   GrayLevel color = GrayLevel::Black);
    void draw_rect(int x, int y, int width, int height,
                   GrayLevel color = GrayLevel::Black);
    void fill_rect(int x, int y, int width, int height, GrayLevel color);
    void draw_text(int x, int y, const char *text, uint8_t scale = 1,
                   GrayLevel color = GrayLevel::Black);

private:
    // Clip window state for set_clip_y() above — declared here because the
    // 0.14.0-m12 commit added the accessors without the members and HEAD did
    // not compile (2026-08-26).
    bool clip_on_ = false;
    int clip_y0_ = 0;
    int clip_y1_ = 0;

    CanvasRotation rotation_ = CanvasRotation::Landscape;
    uint16_t width_;
    uint16_t height_;
    size_t stride_;
    uint8_t *buffer_;
    size_t buffer_size_;
};
