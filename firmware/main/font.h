#pragma once

#include <cstdint>

struct FontGlyph {
    uint8_t columns[5];
};

constexpr uint8_t kFontWidth = 5;
constexpr uint8_t kFontHeight = 7;
constexpr uint8_t kFontSpacing = 1;

// Returns a compact 5x7 glyph. Unsupported characters use the '?' glyph.
const FontGlyph &font_get_glyph(char character);
