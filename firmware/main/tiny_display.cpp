// tiny_display — card-spec JSON -> canvas -> e-ink.
//
// Card spec v1 (docs/ARCHITECTURE.md), the subset that ships:
//   {"type":"text","title":"…","body":"…"}
//   {"type":"list","title":"…","items":["…", …]}
//   {"type":"kv","title":"…","rows":{"k":"v", …}}      (or [["k","v"],…])
//   {"type":"chart","title":"…","data":[n,…]}          (+labels/style/unit/y_min/y_max)
//   optional on any card: "buttons":["Yes","No", …]    (max 4, bottom bar)
//   optional: "footer":"…" small line above the buttons
// Unknown type degrades to a text card showing the raw type — never a blank
// screen, the same honesty rule render_ui props follow elsewhere.
//
// Refresh policy: mono full refresh per card (the panel's own 1-2s is the
// bottleneck; policy hooks for partial land with the stream type).
#include "tiny/tiny_display.h"
#include "tiny/tiny_askq.h"
#include "esp_http_client.h"
#include "esp_crt_bundle.h"
#include "tiny/tiny_config.h"
#include "tiny/tiny_lock.h"
#include "tiny/tiny_node.h"
#include "tiny/tiny_shell.h"
#include "tiny/tiny_agent.h"

#include <algorithm>
#include <cstring>
#include <cstdarg>
#include <cstdlib>
#include <cctype>
#include <cstdio>

#include "cJSON.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "canvas.h"
#include "font.h"  // kFontWidth/kFontSpacing/kFontHeight: real glyph metrics for measuring
#include "sticky_battery.h"
#include "sticky_buzzer.h"
#include "sticky_display.h"
#include "sticky_sdcard.h"  // gallery-on-SD: frame cache at /sdcard/gallery
#include <errno.h>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#include "tiny/tiny_time.h"
#include "tiny/tiny_wifi.h"
#include "qrcode.h"   // espressif/qrcode — see firmware/main/idf_component.yml

static const char *TAG = "tiny_display";

// ---- display ownership ------------------------------------------------------
// THREE tasks call render_card: tiny_node (relay verbs), voice_ask (button ask),
// and the main task's serial card console. The Canvas and the SPI panel are a
// single resource; concurrent renders corrupt the framebuffer. Every path that
// draws or refreshes takes this mutex (audit DISPLAY P0-1).
static SemaphoreHandle_t s_display_mux = NULL;

static void display_lock(void) {
    if (!s_display_mux) s_display_mux = xSemaphoreCreateMutex();
    if (s_display_mux) xSemaphoreTake(s_display_mux, portMAX_DELAY);
}

static void display_unlock(void) {
    if (s_display_mux) xSemaphoreGive(s_display_mux);
}

// ---- layout constants (800x480 landscape) ----
// PHYSICAL panel geometry — the framebuffer, the screenshot and the 1-bit blit
// are always 800x480 no matter which way up the device is.
static constexpr int kW = 800, kH = 480;
// LOGICAL layout geometry — what the card renderer lays out against. Equal to
// the physical pair in landscape, swapped in portrait. Refreshed from the canvas
// at the top of every render, so every helper below sees one consistent frame.
static int s_w = kW, s_h = kH;
static constexpr int kMargin = 24;
static constexpr int kTitleScale = 3;   // 24x48 glyphs
static constexpr int kBodyScale = 2;    // 16x32 glyphs
static constexpr int kSmallScale = 1;   // 8x16 glyphs
static constexpr int kGlyphW = 8, kGlyphH = 16;
static constexpr int kButtonH = 56;

// Text-centering fix: the estimator claimed an 8px cell but
// Canvas::draw_text advances (kFontWidth+kFontSpacing)=6px per char and the
// last glyph paints only 5 columns — so every centered string sat ~25% of its
// width LEFT of true center (receipt: hero -41px in both orientations).
// This returns the exact ink extent of what draw_text will paint.
static int text_width(const char *s, int scale) {
    const int n = (int)strlen(s);
    if (n <= 0) return 0;
    return (n * (kFontWidth + kFontSpacing) - kFontSpacing) * scale;
}
// Vertical twin of the same bug: glyph ink is kFontHeight(7)*scale tall,
// not the 16px kGlyphH cell. Center boxes against this, not the cell.
static int text_ink_h(int scale) { return kFontHeight * scale; }

// Word-wrap `text` into the canvas starting at y; returns y after last line.
// UTF-8 → ASCII fold: the vendored bitmap font is ASCII-only, and agent
// prose is full of em-dashes, curly quotes, ✓/⚠/→ status glyphs and accented
// Latin. v2 (was: 22-entry byte-sequence allowlist, everything else '?'):
// decode REAL codepoints, then fold in three tiers —
//   1. symbol table (punctuation, arrows, math, status glyphs)
//   2. systematic Latin-1 + Latin-Ext-A diacritic strip (U+00C0..U+017F) via
//      two compact range tables — é→e, ñ→n, ß→ss, ç/ğ/ı/ö/ş/ü keep working
//   3. anything else (emoji, CJK) → '?'
// Some subs GROW the byte count ("°" -> " deg"), so folding always writes to
// a separate dst buffer — never in place.
static uint32_t utf8_next(const char **p) {
    const unsigned char *s = (const unsigned char *)*p;
    uint32_t cp; int n;
    if      (s[0] < 0x80)          { cp = s[0];        n = 1; }
    else if ((s[0] & 0xE0) == 0xC0){ cp = s[0] & 0x1F; n = 2; }
    else if ((s[0] & 0xF0) == 0xE0){ cp = s[0] & 0x0F; n = 3; }
    else if ((s[0] & 0xF8) == 0xF0){ cp = s[0] & 0x07; n = 4; }
    else { ++*p; return 0xFFFD; }  // stray continuation byte: skip one
    for (int i = 1; i < n; i++) {
        if ((s[i] & 0xC0) != 0x80) { *p += i; return 0xFFFD; }  // truncated
        cp = (cp << 6) | (s[i] & 0x3F);
    }
    *p += n;
    return cp;
}

struct CpFold { uint32_t cp; const char *sub; };
static const CpFold kCpFolds[] = {
    {0x2014, "-"}, {0x2013, "-"}, {0x2012, "-"}, {0x2010, "-"},   // — – ‒ ‐
    {0x2018, "'"}, {0x2019, "'"}, {0x201C, "\""}, {0x201D, "\""}, // ' ' " "
    {0x2026, "..."}, {0x2022, "*"}, {0x00B7, "*"},                // … • ·
    {0x00A0, " "},  {0x200B, ""},                                 // nbsp zwsp
    {0x00B0, " deg"}, {0x00B1, "+/-"}, {0x00D7, "x"}, {0x00F7, "/"},
    {0x2192, "->"}, {0x2190, "<-"}, {0x2191, "^"}, {0x2193, "v"}, // → ← ↑ ↓
    {0x2713, "[ok]"}, {0x2714, "[ok]"}, {0x2717, "[x]"}, {0x2718, "[x]"},
    {0x26A0, "[!]"},  {0x2705, "[ok]"}, {0x274C, "[x]"},          // ⚠ ✅ ❌
    {0x2B06, "^"},   {0x2B07, "v"},                               // ⬆ ⬇
    {0x20AC, "EUR"}, {0x00A3, "GBP"}, {0x00A5, "JPY"},
    {0x00A9, "(c)"}, {0x00AE, "(r)"}, {0x2122, "(tm)"},
    {0x00BC, "1/4"}, {0x00BD, "1/2"}, {0x00BE, "3/4"},
    {0x00DF, "ss"},  {0x00C6, "AE"},  {0x00E6, "ae"},
    {0x0152, "OE"},  {0x0153, "oe"},
    // Turkish -> extended font bank (font.cpp 0x80..0x8B): rendered as
    // written, not transliterated. Checked before the range tables below,
    // which otherwise fold these very codepoints to base letters.
    {0x00E7, "\x80"}, {0x00C7, "\x81"},  // ç Ç
    {0x011F, "\x82"}, {0x011E, "\x83"},  // ğ Ğ
    {0x0131, "\x84"}, {0x0130, "\x85"},  // ı İ
    {0x00F6, "\x86"}, {0x00D6, "\x87"},  // ö Ö
    {0x015F, "\x88"}, {0x015E, "\x89"},  // ş Ş
    {0x00FC, "\x8A"}, {0x00DC, "\x8B"},  // ü Ü
};
// U+00C0..U+00FF, base letter per slot ('?' = handled above or unmapped).
static const char kLatin1Base[] =
    "AAAAAA?CEEEEIIII"   // C0-CF (Æ at C6 handled in table)
    "DNOOOOO?OUUUUY??"   // D0-DF (× D7, ß DF in table)
    "aaaaaa?ceeeeiiii"   // E0-EF
    "dnooooo?ouuuuy?y";  // F0-FF (÷ F7 in table)
static void ascii_fold(char *dst, size_t cap, const char *src) {
    size_t o = 0;
    while (*src && o + 1 < cap) {
        unsigned char ch = (unsigned char)*src;
        if (ch < 0x80) { dst[o++] = *src++; continue; }
        uint32_t cp = utf8_next(&src);
        const char *sub = NULL;
        for (const CpFold &f : kCpFolds)
            if (f.cp == cp) { sub = f.sub; break; }
        char one[2] = {0, 0};
        if (!sub && cp >= 0xC0 && cp <= 0xFF && kLatin1Base[cp - 0xC0] != '?') {
            one[0] = kLatin1Base[cp - 0xC0]; sub = one;
        }
        if (!sub && cp >= 0x100 && cp <= 0x17F) {
            // Latin Extended-A alternates base/derived; even cp = uppercase.
            // Turkish set lives here (ğ 11F, ı 131, ş 15F...) — approximate by
            // the Unicode name's base letter, packed as pairs per 16-block.
            static const char kExtA[] =
                "AaAaAaCcCcCcCcDd" "DdEeEeEeEeEeGgGg"   // 100-11F
                "GgGgHhHhIiIiIiIi" "IiIiJjKkkLlLlLlL"   // 120-13F
                "lLlNnNnNnnNnOoOo" "OoOoRrRrRrSsSsSs"   // 140-15F
                "SsTtTtTtUuUuUuUu" "UuUuWwYyYZzZzZzs";  // 160-17F
            one[0] = kExtA[cp - 0x100]; sub = one;
        }
        if (!sub) {
            // R-4 ruling (spec v1.3 §6): emoji are TONE, not content — absence
            // is honest, '?' is a DIFFERENT SENTENCE ("Hey! 👋" must not
            // become "Hey! ?"). Strip emoji and their plumbing (VS16, ZWJ,
            // keycap). Unmapped CONTENT (CJK, unknown scripts) keeps '?':
            // there, position matters and silent absence would lie.
            // Status glyphs (✓ ⚠ ✅ ❌ …) sit inside these ranges but are
            // folded by the table ABOVE, so they still render as [ok]/[!]/[x].
            const bool emoji = (cp >= 0x1F000 && cp <= 0x1FAFF) ||  // pictographs
                               (cp >= 0x2600 && cp <= 0x27BF) ||    // symbols+dingbats
                               (cp >= 0x2B00 && cp <= 0x2BFF) ||    // arrows/stars (⭐)
                               cp == 0xFE0F || cp == 0x200D ||      // VS16, ZWJ
                               cp == 0x20E3;                        // keycap
            sub = emoji ? "" : "?";
        }
        for (const char *r = sub; *r && o + 1 < cap;) dst[o++] = *r++;
    }
    dst[o] = 0;
}

static int draw_wrapped(Canvas &c, int x, int y, const char *raw_text, int scale,
                        int max_w, int max_y, bool paint = true) {
    // Grow-on-demand fold buffer (render is single-flight: relay task or ask
    // task, never concurrent). Was `static char folded[1024]` — which cut an
    // 8KB relay body silently at ~1KB (open bug flagged by the dashboard's
    // composer lint; the scroll feature exists precisely to browse long
    // bodies). Worst-case fold expansion is 1.5x ("(c)" from a 2-byte ©), so
    // 2x input is a safe bound; the 16KB ceiling is unreachable through the
    // 8000-byte relay payload cap and exists so a hostile serial write cannot
    // OOM the render task.
    static char *folded = nullptr;
    static size_t folded_cap = 0;
    size_t need = strlen(raw_text) * 2 + 8;
    if (need > 16384) need = 16384;
    if (need > folded_cap) {
        char *nb = (char *)realloc(folded, need);
        if (nb) { folded = nb; folded_cap = need; }
    }
    if (!folded) {  // first alloc failed: say so on glass, honest-void idiom
        c.draw_text(x, y, "(no memory for text)", scale, GrayLevel::Black);
        return y + kGlyphH * scale;
    }
    ascii_fold(folded, folded_cap, raw_text);
    // If the fold filled the buffer to the brim, input was cut: mark it ON
    // the text instead of pretending the body ended there.
    size_t flen = strlen(folded);
    if (flen == folded_cap - 1 && folded_cap >= 8)
        strcpy(folded + folded_cap - 7, " [cut]");
    const char *text = folded;
    const int line_h = kGlyphH * scale + 6;
    const int chars_per_line = std::max(1, max_w / (kGlyphW * scale));
    const char *p = text;
    char line[128];
    while (*p && y + kGlyphH * scale <= max_y) {
        int take = (int)strlen(p);
        if (take > chars_per_line) {
            take = chars_per_line;
            // back off to the previous space when we're mid-word
            for (int i = take; i > 0; --i) {
                if (p[i] == ' ') { take = i; break; }
            }
        }
        int n = std::min(take, (int)sizeof(line) - 1);
        memcpy(line, p, n);
        line[n] = '\0';
        if (paint) c.draw_text(x, y, line, scale, GrayLevel::Black);
        y += line_h;
        p += take;
        while (*p == ' ') ++p;
        // explicit newlines
        if (*p == '\n') ++p;
    }
    return y;
}

// ---- rounded rects (the iOS bubble chrome on 4-gray) -----------------
// Midpoint-circle corners. Radius is clamped to half the shorter side so a
// degenerate bubble degrades to a plain rect instead of drawing outside
// itself. Fill walks rows and insets each by the circle equation.
static void fill_round_rect(Canvas &c, int x, int y, int w, int h, int r,
                            GrayLevel color) {
    if (w <= 0 || h <= 0) return;
    if (r > w / 2) r = w / 2;
    if (r > h / 2) r = h / 2;
    if (r <= 1) { c.fill_rect(x, y, w, h, color); return; }
    for (int row = 0; row < h; ++row) {
        int inset = 0;
        const int d = row < r ? (r - 1 - row) : (row >= h - r ? row - (h - r) : -1);
        if (d >= 0) {
            // horizontal inset of a circle of radius r at vertical offset d
            const int q = r * r - (d + 1) * (d + 1);
            const int half = q > 0 ? (int)__builtin_sqrt((double)q) : 0;
            inset = r - half;
        }
        c.fill_rect(x + inset, y + row, w - 2 * inset, 1, color);
    }
}

static void draw_round_rect(Canvas &c, int x, int y, int w, int h, int r,
                            GrayLevel color) {
    if (w <= 0 || h <= 0) return;
    if (r > w / 2) r = w / 2;
    if (r > h / 2) r = h / 2;
    if (r <= 1) { c.draw_rect(x, y, w, h, color); return; }
    // straight edges between the corner arcs
    c.fill_rect(x + r, y, w - 2 * r, 1, color);
    c.fill_rect(x + r, y + h - 1, w - 2 * r, 1, color);
    c.fill_rect(x, y + r, 1, h - 2 * r, color);
    c.fill_rect(x + w - 1, y + r, 1, h - 2 * r, color);
    // corner arcs: midpoint circle, one octant mirrored eight ways onto the
    // four corner centers
    const int cx0 = x + r, cy0 = y + r;             // top-left center
    const int cx1 = x + w - 1 - r, cy1 = y + h - 1 - r;
    int px = r, py = 0, err = 1 - r;
    while (px >= py) {
        c.draw_pixel(cx0 - px, cy0 - py, color); c.draw_pixel(cx0 - py, cy0 - px, color);
        c.draw_pixel(cx1 + px, cy0 - py, color); c.draw_pixel(cx1 + py, cy0 - px, color);
        c.draw_pixel(cx0 - px, cy1 + py, color); c.draw_pixel(cx0 - py, cy1 + px, color);
        c.draw_pixel(cx1 + px, cy1 + py, color); c.draw_pixel(cx1 + py, cy1 + px, color);
        ++py;
        if (err < 0) err += 2 * py + 1;
        else { --px; err += 2 * (py - px) + 1; }
    }
}

// Right-aligned glance cluster in the title bar (glanceable at arm's length): time,
// battery, wifi — on EVERY card, because the title bar is the one strip that
// is always drawn and so costs no extra refresh. Each part earns its place or
// is omitted: time only when actually synced (and suffixed Z — it is UTC until
// cagatay answers the timezone question, and an unlabeled wrong-zone clock is
// a lie), battery only when the gauge answers, wifi as w+/w- (w- matters MORE
// than w+ on a pocket device: it says "answers may be stale").
static int draw_glance_cluster(Canvas &c, int y) {
    char cluster[56] = "";
    size_t o = 0;
    if (tiny_time_is_synced()) {
        char ts[24]; tiny_time_utc_string(ts, sizeof ts);
        // "YYYY-MM-DD HH:MM:SS" -> take HH:MM
        if (strlen(ts) >= 16)
            o += snprintf(cluster + o, sizeof cluster - o, "%.5sZ ", ts + 11);
    }
    BatteryReading batt;
    if (sticky_battery_read(batt) == ESP_OK && batt.percent > 0)
        o += snprintf(cluster + o, sizeof cluster - o, "%d%% ", batt.percent);
    o += snprintf(cluster + o, sizeof cluster - o, tiny_wifi_is_up() ? "w+" : "w-");
    // §12 completes the cluster: unread only when there ARE any (a zero is
    // noise on a strip this small), lock glyph whenever locked — the pocket
    // glance "is it locked, did anything arrive" answered in the same
    // always-drawn strip as time/battery/wifi.
    const int unread = tiny_node_unread();
    if (unread > 0)
        o += snprintf(cluster + o, sizeof cluster - o, " dm%d", unread);
    // §16 law 2: questions waiting for the network wear a badge — the owner
    // typed them into what looked like a void; qN is the receipt that the
    // void is a queue. Zero is invisible, same rule as dm.
    const int queued = tiny_askq_count();
    if (queued > 0)
        o += snprintf(cluster + o, sizeof cluster - o, " q%d", queued);
    if (tiny_lock_is_locked())
        snprintf(cluster + o, sizeof cluster - o, " [L]");
    const int w = text_width(cluster, 1);
    c.draw_text(s_w - kMargin - w, y, cluster, 1, GrayLevel::Black);
    return w;  // the title must never paint under this (UI_SPEC §2)
}

static int draw_title_bar(Canvas &c, const char *title) {
    int y = kMargin;
    const int cluster_w = draw_glance_cluster(c, y);
    if (title && *title) {
        char ft[128]; ascii_fold(ft, sizeof ft, title);
        // UI_SPEC §2: title max width ends 16px before the
        // cluster. A title that does not fit is CUT AND SAYS SO ("...") —
        // never inked to the glass edge, never painted under the clock.
        const int max_w = s_w - 2 * kMargin - cluster_w - 16;
        if (text_width(ft, kTitleScale) > max_w) {
            size_t len = strlen(ft);
            while (len > 0) {
                strcpy(ft + len, "...");
                if (text_width(ft, kTitleScale) <= max_w) break;
                --len;
                ft[len] = 0;
            }
        }
        c.draw_text(kMargin, y, ft, kTitleScale, GrayLevel::Black);
        y += kGlyphH * kTitleScale + 10;
    } else {
        y += kGlyphH + 6;  // no title: still clear the cluster's strip
    }
    c.fill_rect(kMargin, y, s_w - 2 * kMargin, 3, GrayLevel::Black);
    return y + 18;
}

// ---- button regions: written by the render path, read by the touch task ----
// The touch task polls every 30ms; a render rewrites this table. Without the
// mutex a tap landing mid-render resolved against half of the new card and half
// of the old one (that is the "label=Status from the PREVIOUS card" defect,
// seen on the glass). Renders build into a staging table and publish it in one
// locked swap AFTER the panel commits.
// 48: a qwerty keyboard card stages 41 key regions. ~9KB of .bss,
// paid once, guarded by the same staging/publish discipline as before.
static constexpr int kMaxRegions = 48;
static tiny_button_region_t s_regions[kMaxRegions];
static int s_region_count = 0;
static tiny_button_region_t s_staged[kMaxRegions];
static int s_staged_count = 0;
// Orientation state. The canvas owns the transform; this is the copy the render
// path and the touch path both read, plus the JSON of the card on the glass so a
// rotation can REDRAW it. Without the cache, turning the device would leave the
// old landscape picture on the panel until the next relay envelope — which is
// exactly the "it does not follow my body" feeling we are removing.
static CanvasRotation s_rot = CanvasRotation::Landscape;
static char *s_card_cache = nullptr;      // heap (PSRAM if available)
static bool s_rerendering = false;        // guards rotate -> render -> rotate
static bool s_glance_partial = false;     // §12 glance strip -> partial refresh
static bool s_draw_only = false;          // §12 wake: render without refresh
static bool s_wake_partial_pending = false;  // next commit = diff-partial

static char s_card_id[24] = "";
static char s_card_type[16] = "";  // guards gestures: no page-flip over a composer
static SemaphoreHandle_t s_region_mux = NULL;
// Bumped once per COMMITTED card. tiny_touch samples it at finger-down and
// re-checks at lift: if the card changed under the finger, the tap is dropped
// rather than applied to a button the human never saw (audit TOUCH P0-1).
static uint32_t s_card_gen = 0;

static void regions_lock(void) {
    if (!s_region_mux) s_region_mux = xSemaphoreCreateMutex();
    if (s_region_mux) xSemaphoreTake(s_region_mux, portMAX_DELAY);
}

static void regions_unlock(void) {
    if (s_region_mux) xSemaphoreGive(s_region_mux);
}

// Publish the staged table + card_id as the live one. Called once, after the
// refresh returns — until then the OLD card is what the human can touch.
static void publish_regions(const char *card_id) {
    // Layout stages regions in LOGICAL coordinates; they are published in
    // PHYSICAL (panel) coordinates so the touch task — which reads raw GT911
    // panel coordinates — needs no idea that the UI is rotated, and so a tap
    // aimed at a spot on a screenshot always means the same spot.
    Canvas *canvas = sticky_display_canvas();
    regions_lock();
    for (int i = 0; i < s_staged_count; ++i) {
        s_regions[i] = s_staged[i];
        if (canvas) {
            canvas->rect_to_physical(s_staged[i].x, s_staged[i].y,
                                     s_staged[i].w, s_staged[i].h,
                                     s_regions[i].x, s_regions[i].y,
                                     s_regions[i].w, s_regions[i].h);
        }
    }
    s_region_count = s_staged_count;
    strlcpy(s_card_id, card_id ? card_id : "", sizeof s_card_id);
    for (int i = 0; i < s_region_count; ++i)
        strlcpy(s_regions[i].card_id, s_card_id, sizeof s_regions[i].card_id);
    ++s_card_gen;
    regions_unlock();
}

// Stage one extra tappable region from a body renderer (keyboard keys, menu
// rows). Same table, same locked publish — a key is just a small button.
// Regions store the ORIGINAL UTF-8 label, truncated at a rune
// boundary — the folded form (private font-bank bytes 0x80..0x8B) belongs to
// the renderer alone. The stored label is what ui_tap echoes upstream, where
// 0x8x bytes are not UTF-8 and every consumer paints U+FFFD. Folding is a
// display concern; it happens where the glass consumes the text (draw_button
// folds at paint), never in what we remember.
static size_t utf8_complete_len(const char *s, size_t len);
static void copy_label_utf8(char *dst, size_t cap, const char *src) {
    if (!src) { dst[0] = 0; return; }
    size_t len = strlen(src);
    if (len >= cap) len = cap - 1;
    len = utf8_complete_len(src, len);   // never cut a rune mid-sequence
    memcpy(dst, src, len);
    dst[len] = 0;
}

static void stage_region(int x, int y, int w, int h, const char *label,
                         const char *id) {
    if (s_staged_count >= kMaxRegions) return;
    tiny_button_region_t &r = s_staged[s_staged_count];
    r = {x, y, w, h, {0}, {0}, {0}};
    copy_label_utf8(r.label, sizeof r.label, label);
    strlcpy(r.id, id ? id : (label ? label : ""), sizeof r.id);
    ++s_staged_count;
}

// Button geometry — the ONE definition. tiny_touch hit-tests against the
// regions this produces, and the dashboard mirror mirrors the same math.
static void button_box(int i, int n, int *x, int *y, int *w, int *h) {
    const int gap = 16;
    *w = (s_w - 2 * kMargin - gap * (n - 1)) / n;
    *x = kMargin + i * (*w + gap);
    *y = s_h - kMargin - kButtonH;
    *h = kButtonH;
}

// Draw button i of n into the canvas. `inverted` = filled black with knocked-out
// text, which is what a tap ack looks like on a 4-gray panel.
static void draw_button(Canvas &c, int i, int n, const char *label,
                        bool inverted) {
    int x, y, w, h;
    button_box(i, n, &x, &y, &w, &h);
    if (inverted) {
        c.fill_rect(x, y, w, h, GrayLevel::Black);
    } else {
        c.fill_rect(x, y, w, h, GrayLevel::White);
        c.draw_rect(x, y, w, h, GrayLevel::Black);
        c.draw_rect(x + 1, y + 1, w - 2, h - 2, GrayLevel::Black);
    }
    char fl[64]; ascii_fold(fl, sizeof fl, label);
    label = fl;  // fold BEFORE measuring: centering must see the drawn bytes
    // Root cause: a label wider than its face used to spill INTO the gutter
    // ("Bluetooth" at scale 2 = 108px in a 96px button -> 3px visual gap).
    // Ink stays inside the box: drop a scale, then truncate honestly.
    int scale = kBodyScale;
    if (text_width(label, scale) > w - 12) scale = 1;
    if (text_width(label, scale) > w - 12) {
        size_t len = strlen(fl);
        while (len > 0 && text_width(fl, scale) > w - 12) fl[--len] = 0;
        if (len > 2) { fl[len - 2] = '.'; fl[len - 1] = '.'; }
    }
    const int tw = text_width(label, scale);
    const int tx = x + std::max(6, (w - tw) / 2);
    const int ty = y + (kButtonH - kGlyphH * scale) / 2;
    c.draw_text(tx, ty, label, scale,
                inverted ? GrayLevel::White : GrayLevel::Black);
}

// A button entry is either a bare string ("Done") or the documented object
// form {"id":"done","label":"Done ✓"} (docs/ARCHITECTURE.md card spec v1).
// Only strings were handled before, so every agent-composed card — and every
// dashboard preset — painted "?" and reported taps as label=?.
static void button_text(const cJSON *b, const char **label, const char **id) {
    *label = "?";
    *id = NULL;
    if (cJSON_IsString(b)) {
        *label = b->valuestring;
        return;
    }
    if (!cJSON_IsObject(b)) return;
    const cJSON *l = cJSON_GetObjectItem(b, "label");
    const cJSON *i = cJSON_GetObjectItem(b, "id");
    if (cJSON_IsString(l) && l->valuestring[0]) *label = l->valuestring;
    if (cJSON_IsString(i) && i->valuestring[0]) {
        *id = i->valuestring;
        // an object with only an id still deserves a legible face
        if (!cJSON_IsString(l) || !l->valuestring[0]) *label = i->valuestring;
    }
}

static void draw_buttons(Canvas &c, cJSON *buttons, int *content_bottom) {
    s_staged_count = 0;
    if (!cJSON_IsArray(buttons)) return;
    int n = cJSON_GetArraySize(buttons);
    if (n <= 0) return;
    n = std::min(n, 5);  // settings needs 5; button_box math is generic (§1)
    for (int i = 0; i < n; ++i) {
        const cJSON *b = cJSON_GetArrayItem(buttons, i);
        const char *label = NULL, *id = NULL;
        button_text(b, &label, &id);
        draw_button(c, i, n, label, false);
        int x, y, w, h;
        button_box(i, n, &x, &y, &w, &h);
        // UI_SPEC §3: the strip DRAWS 56px (visual weight) but a fingertip
        // gets the 80px law — grow the staged box upward and into the bottom
        // margin. Drawn-small/staged-big is the established precedent.
        const int floor_h = 80;
        if (h < floor_h) {
            const int grow = floor_h - h;
            int top = y - (grow / 2);
            if (top + floor_h > s_h) top = s_h - floor_h;
            y = top;
            h = floor_h;
        }
        s_staged[i] = {x, y, w, h, {0}, {0}, {0}};
        copy_label_utf8(s_staged[i].label, sizeof s_staged[i].label, label);
        strlcpy(s_staged[i].id, id ? id : label, sizeof s_staged[i].id);
        s_staged_count = i + 1;
    }
    *content_bottom = s_staged[0].y - 16;
}

// Returns the CLAMPED count actually written (the old version returned the
// unclamped total, so a caller with a smaller buffer read uninitialized stack —
// audit TOUCH P1-4).
extern "C" int tiny_display_button_regions(tiny_button_region_t *out, int max) {
    if (!out || max <= 0) return 0;
    regions_lock();
    const int n = std::min(s_region_count, max);
    for (int i = 0; i < n; ++i) out[i] = s_regions[i];
    regions_unlock();
    return n;
}

extern "C" const char *tiny_display_current_card_id(void) { return s_card_id; }

extern "C" uint32_t tiny_display_card_gen(void) {
    regions_lock();
    const uint32_t g = s_card_gen;
    regions_unlock();
    return g;
}

// ---- orientation ------------------------------------------------------------
// Degrees are the human unit (0/90/180/270) and clockwise as the device turns.
extern "C" int tiny_display_rotation(void) {
    return static_cast<int>(s_rot) * 90;
}

// §12 glance: redraw the current card so the title-bar cluster (time,
// battery, wifi, unread, lock) speaks NOW — via the partial path, because the
// acceptance is explicit: from awake, the strip updates with no full-panel
// flash. The whole card redraws (the cluster lives inside the title bar, not
// in a private zone) but only changed pixels flip, which IS the strip.
// §12 wake paint (designer ruling 67b8f22: the full flash was the wrong
// refresh, not a physics floor). The goodbye card is deterministic from the
// sleep verb's secs argument, so the baseline needs no flash persistence:
// redraw goodbye into a scratch buffer = the "previous" plane (what the glass
// physically shows through 0mA sleep), render the wake card into the canvas =
// the "current" plane, and hand both to the driver's diff-partial. One honest
// drift: none (the goodbye card embeds no clock). Arm BEFORE the wake card
// render; the next render_card commit uses the diff path once, then rearms
// the normal policy — the designer's lazy clean-flash runs 6s later from
// tiny_main so any waveform residue self-heals.
static uint8_t *s_wake_baseline = nullptr;  // gray4 canvas-format, PSRAM
// The goodbye card's variable content + the rotation it was drawn at, kept
// in RTC slow RAM (survives deep sleep, dies on power loss — and on power
// loss the glass is stale anyway, so the guard below refuses the partial).
RTC_DATA_ATTR static char s_rtc_goodbye[160];
RTC_DATA_ATTR static int  s_rtc_sleep_rot = -1;
extern "C" void tiny_display_note_sleep_card(const char *body) {
    strlcpy(s_rtc_goodbye, body ? body : "", sizeof s_rtc_goodbye);
    s_rtc_sleep_rot = static_cast<int>(s_rot);
}
extern "C" esp_err_t tiny_display_arm_wake_baseline(void) {
    // No note (cold boot / power loss / pre-0.18.4 sleep) = no baseline =
    // the caller falls back to the honest full flash.
    if (!s_rtc_goodbye[0] || s_rtc_sleep_rot < 0) return ESP_ERR_INVALID_STATE;
    const char *goodbye_body = s_rtc_goodbye;
    s_rot = static_cast<CanvasRotation>(s_rtc_sleep_rot);
    s_rtc_goodbye[0] = 0;  // one shot: a baseline may only be spent once
    s_rtc_sleep_rot = -1;
    Canvas *c = sticky_display_canvas();
    if (!c || !goodbye_body) return ESP_ERR_INVALID_ARG;
    display_lock();
    char card[340];
    snprintf(card, sizeof card,
             "{\"type\":\"text\",\"title\":\"zzz\",\"card_id\":\"sleep\","
             "\"body\":\"%s\","
             "\"footer\":\"deep sleep - the panel keeps this image at 0 mA\"}",
             goodbye_body);
    display_unlock();
    // Render the goodbye spec through the normal card path with the refresh
    // suppressed: draw-only, then snapshot the canvas as the baseline.
    s_draw_only = true;
    esp_err_t err = tiny_display_render_card(card);
    s_draw_only = false;
    if (err != ESP_OK) return err;
    display_lock();
    const size_t len = c->stride() * 480;
    if (!s_wake_baseline)
        s_wake_baseline = (uint8_t *)heap_caps_malloc(
            len, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_wake_baseline) { display_unlock(); return ESP_ERR_NO_MEM; }
    memcpy(s_wake_baseline, c->data(), len);
    s_wake_partial_pending = true;
    display_unlock();
    return ESP_OK;
}

extern "C" esp_err_t tiny_display_glance(void) {
    if (!s_card_cache) return ESP_ERR_INVALID_STATE;
    s_glance_partial = true;
    const esp_err_t err = tiny_display_rerender();
    s_glance_partial = false;
    return err;
}

extern "C" esp_err_t tiny_display_rerender(void) {
    if (!s_card_cache) return ESP_ERR_INVALID_STATE;
    s_rerendering = true;   // render_card must not free the string it is reading
    const esp_err_t err = tiny_display_render_card(s_card_cache);
    s_rerendering = false;
    return err;
}

extern "C" esp_err_t tiny_display_set_rotation(int degrees) {
    const int d = ((degrees % 360) + 360) % 360;
    if (d % 90) return ESP_ERR_INVALID_ARG;
    const CanvasRotation r = static_cast<CanvasRotation>(d / 90);
    if (r == s_rot) return ESP_OK;   // an e-ink flash costs ~600ms; don't spend
                                     // it to redraw an identical picture
    s_rot = r;
    esp_err_t err = tiny_display_rerender();
    if (err == ESP_ERR_INVALID_STATE) {
        // Nothing cached yet (fresh boot, splash, raw blit). Leaving the old
        // picture would mean a panel that disagrees with the body, so say so.
        char card[192];
        snprintf(card, sizeof card,
                 "{\"type\":\"text\",\"title\":\"stickyOS\","
                 "\"body\":\"rotated to %d deg\",\"card_id\":\"rot%d\"}", d, d);
        err = tiny_display_render_card(card);
    }
    ESP_LOGI(TAG, "rotation -> %d deg (%s)", d, esp_err_to_name(err));
    return err;
}

extern "C" esp_err_t tiny_display_flash_button(int index) {
    tiny_button_region_t regions[4];
    const int n = tiny_display_button_regions(regions, 4);
    if (index < 0 || index >= n) return ESP_ERR_INVALID_ARG;
    Canvas *c = sticky_display_canvas();
    if (!c) return ESP_ERR_INVALID_STATE;
    // invert -> partial refresh (the human's proof the tap landed) -> restore.
    display_lock();
    draw_button(*c, index, n, regions[index].label, true);
    esp_err_t err = sticky_display_refresh_partial();
    vTaskDelay(pdMS_TO_TICKS(220));
    draw_button(*c, index, n, regions[index].label, false);
    esp_err_t back = sticky_display_refresh_partial();
    display_unlock();
    ESP_LOGI(TAG, "tap ack button=%d \"%s\" -> %s/%s", index,
             regions[index].label, esp_err_to_name(err), esp_err_to_name(back));
    return err == ESP_OK ? back : err;
}

// ---- ack worker -------------------------------------------------------------
// The e-ink busy-wait can stall a caller for seconds. The touch task must never
// pay that (it would stop sampling fingers), so taps only post an index here and
// this task owns the visible ack (audit TOUCH P0-2: "not on the touch task").
static QueueHandle_t s_ack_q = NULL;

static void ack_task(void *) {
    int index = 0;
    for (;;) {
        if (xQueueReceive(s_ack_q, &index, portMAX_DELAY) != pdTRUE) continue;
        tiny_display_flash_button(index);
    }
}

extern "C" esp_err_t tiny_display_ack_button(int index) {
    if (!s_ack_q) return ESP_ERR_INVALID_STATE;
    // Depth-2 queue, no wait: a human cannot outrun the panel, and dropping a
    // redundant ack is better than blocking the sampler.
    return xQueueSend(s_ack_q, &index, 0) == pdTRUE ? ESP_OK : ESP_ERR_NO_MEM;
}

// ---- scroll state -------------------------------------------------------
// One scroll offset, owned by the card on the glass. text/list/kv/menu bodies
// render their FULL content through the canvas y-clip; what does not fit is
// measured instead of painted, a thin right-edge scrollbar says where you are,
// and swipe / UP/DOWN move the window via partial refresh (~300-500ms).
static int s_scroll = 0;        // px scrolled off the top of the body
static int s_content_h = 0;     // measured content height of the last body
static int s_body_top = 0;      // body box of the last scrollable render
static int s_body_bot = 0;
static char s_scroll_card[24] = "";   // which card s_scroll belongs to
static bool s_scroll_partial = false; // scroll re-render -> partial refresh
// Visible window body renderers may stage tap regions into. Menu rows drawn
// off-window are measured but MUST NOT be tappable: a region the human cannot
// see is a button that lies (same honesty rule as publish-after-refresh).
static int s_view_top = 0;
static int s_view_bot = 1 << 28;

// ---- streaming text state -------------------------
// One stream at a time, one caller task at a time (the ask engine). The
// buffer lives in PSRAM and is capped at the relay's own reply ceiling: an
// answer the reply PATCH cannot carry is an answer the stream should not
// pretend to hold either.
#define STREAM_BUF_MAX 8192
static bool    s_stream_on = false;
static bool    s_stream_partial = false;   // commit -> partial refresh gate
static bool    s_stream_caret = false;     // draw the typer caret this render
static bool    s_stream_rendering = false; // our own render, not an outsider
static char   *s_stream_buf = nullptr;
static size_t  s_stream_len = 0;
static size_t  s_stream_cap = 0;
static bool    s_stream_overflow = false;  // bytes dropped past the cap
static char    s_stream_title[64];
static char    s_stream_cid[24];
static char    s_stream_q[256];            // user bubble; "" = text card
static int64_t s_stream_last_commit_us = 0;

// Design rule: the ask lives ON the home surface.
// The note is the canvas's status line while the answer has no words yet —
// "(( listening ))" during mic capture, "(( thinking ))" after — drawn only
// while stream_body is empty, so the first real token replaces it.
static char s_stream_note[40];

static void stream_reset(void) {
    s_stream_on = false;
    s_stream_q[0] = 0;
    s_stream_note[0] = 0;
    s_stream_caret = false;
    s_stream_overflow = false;
    if (s_stream_buf) { free(s_stream_buf); s_stream_buf = nullptr; }
    s_stream_len = s_stream_cap = 0;
}

// Bytes of `s` that end on a COMPLETE UTF-8 sequence. An SSE delta may split
// a multibyte char across two reads; rendering the half would fold it to '?'
// and the finished glyph would then appear NEXT commit — a flicker of
// garbage where a Turkish letter belongs. Hold the tail back instead.
static size_t utf8_complete_len(const char *s, size_t len) {
    if (len == 0) return 0;
    size_t i = len, cont = 0;
    while (i > 0 && cont < 3 && ((unsigned char)s[i - 1] & 0xC0) == 0x80) { --i; ++cont; }
    if (i == 0) return len;  // all continuation bytes: not UTF-8, let fold cope
    const unsigned char lead = (unsigned char)s[i - 1];
    int need;
    if      (lead < 0x80)          need = 1;
    else if ((lead & 0xE0) == 0xC0) need = 2;
    else if ((lead & 0xF0) == 0xE0) need = 3;
    else if ((lead & 0xF8) == 0xF0) need = 4;
    else return len;               // invalid lead: fold will answer 0xFFFD
    return (len - (i - 1)) < (size_t)need ? i - 1 : len;
}

static int render_text_card(Canvas &c, cJSON *root, int y, int max_y) {
    cJSON *body = cJSON_GetObjectItem(root, "body");
    if (cJSON_IsString(body)) {
        y = draw_wrapped(c, kMargin, y, body->valuestring, kBodyScale,
                         s_w - 2 * kMargin, max_y);
    }
    return y;
}

static int render_list_card(Canvas &c, cJSON *root, int y, int max_y) {
    cJSON *items = cJSON_GetObjectItem(root, "items");
    if (!cJSON_IsArray(items)) return y;
    const int line_h = kGlyphH * kBodyScale + 12;
    cJSON *it = nullptr;
    cJSON_ArrayForEach(it, items) {
        if (y + kGlyphH * kBodyScale > max_y) break;
        const char *s = cJSON_IsString(it) ? it->valuestring : "?";
        c.fill_rect(kMargin + 4, y + kGlyphH * kBodyScale / 2 - 4, 10, 10,
                    GrayLevel::Black);  // bullet
        y = draw_wrapped(c, kMargin + 28, y, s, kBodyScale,
                         s_w - 2 * kMargin - 28, max_y);
        y += line_h - (kGlyphH * kBodyScale + 6);
    }
    return y;
}

static int render_kv_card(Canvas &c, cJSON *root, int y, int max_y) {
    cJSON *rows = cJSON_GetObjectItem(root, "rows");
    if (!rows) {
        // A kv card with no "rows" used to render as a title, a footer and a
        // blank middle — indistinguishable from a card that worked (found on
        // glass, 0.9.4-m9 sensors card, which had used "kv" as the key). Say
        // what is wrong ON the panel instead of showing a convincing void.
        c.draw_text(kMargin, y, "(kv card has no \"rows\" object)", kBodyScale,
                    GrayLevel::Black);
        return y + kGlyphH * kBodyScale;
    }
    const int line_h = kGlyphH * kBodyScale + 14;
    const int key_col = kMargin;
    const int val_col = s_w / 2 - 40;
    // Rows that do not fit used to vanish without a trace: the 6th row of the
    // 0.9.6-m9 sensors card was simply absent, and nothing anywhere said so.
    // Count them and admit it on the last usable line.
    int dropped = 0;
    auto draw_row = [&](const char *k, const char *v) {
        if (y + kGlyphH * kBodyScale > max_y) { ++dropped; return; }
        char fk[96]; ascii_fold(fk, sizeof fk, k);
        c.draw_text(key_col, y, fk, kBodyScale, GrayLevel::Black);
        // UI_SPEC §4: an atomic token (no spaces — MAC, id, version)
        // never wraps. If it cannot fit at body scale, it drops to scale 1
        // on ONE line instead of shedding orphan fragments.
        const int val_w = s_w - kMargin - val_col;
        int vscale = kBodyScale;
        if (!strchr(v, ' ') && text_width(v, kBodyScale) > val_w) vscale = 1;
        // draw_wrapped returns the y AFTER the last wrapped line — a long
        // value must push the next row down, not overprint it (bug found by
        // the device's own screenshot verb, 0.8.0-m8 boot card).
        int after = draw_wrapped(c, val_col, y, v, vscale,
                                 val_w, max_y);
        y = (after > y + line_h) ? after + 8 : y + line_h;
    };
    if (cJSON_IsObject(rows)) {
        cJSON *row = nullptr;
        cJSON_ArrayForEach(row, rows) {
            draw_row(row->string ? row->string : "?",
                     cJSON_IsString(row) ? row->valuestring : "?");
        }
    } else if (cJSON_IsArray(rows)) {
        cJSON *pair = nullptr;
        cJSON_ArrayForEach(pair, rows) {
            if (cJSON_IsArray(pair) && cJSON_GetArraySize(pair) >= 2) {
                cJSON *k = cJSON_GetArrayItem(pair, 0);
                cJSON *v = cJSON_GetArrayItem(pair, 1);
                draw_row(cJSON_IsString(k) ? k->valuestring : "?",
                         cJSON_IsString(v) ? v->valuestring : "?");
            }
        }
    }
    if (dropped > 0) {
        char note[40];
        snprintf(note, sizeof note, "(+%d row%s did not fit)", dropped,
                 dropped == 1 ? "" : "s");
        const int ny = std::min(y, max_y - kGlyphH * kBodyScale);
        c.draw_text(kMargin, ny, note, kBodyScale, GrayLevel::Black);
    }
    return y;
}

// keyboard: {"type":"keyboard","card_id":"kb","title":…,"value":…,"shift":bool,
//            "sym":bool}
// A qwerty grid; every key staged as a region (id "k:<char>", specials
// k:shift/k:sym/k:bksp/k:space/k:cancel/k:ok). The VALUE LINE is the tap ack —
// the touch layer skips the invert-ack and the upstream ui_tap for "k:" ids
// (passwords must not leak into the event feed).
// v2 (0.21.0): the SYMBOLS PLANE. A keyboard that cannot type
// ? ' @ . writes telegrams, not messages — "sym":true swaps the letter rows
// for iOS-arranged punctuation, chars ride the same k:<char> path. Keys are
// filled rounded rects (LightGray face, iOS feel) instead of hairline boxes:
// on 4-gray e-ink a filled face survives partial-refresh ghosting far better
// than a 1px outline. OK is the one black key — the eye finds "send" first.
static void kb_key_face(Canvas &c, int x, int y, int w, int h, const char *lab,
                        int scale, bool dark, bool inverted) {
    // dark: the OK key (black face, white glyph). inverted: active shift.
    if (dark || inverted) {
        fill_round_rect(c, x, y, w, h, 8, GrayLevel::Black);
        c.draw_text(x + (w - text_width(lab, scale)) / 2,
                    y + (h - text_ink_h(scale)) / 2, lab, scale, GrayLevel::White);
    } else {
        fill_round_rect(c, x, y, w, h, 8, GrayLevel::LightGray);
        c.draw_text(x + (w - text_width(lab, scale)) / 2,
                    y + (h - text_ink_h(scale)) / 2, lab, scale, GrayLevel::Black);
    }
}

static void render_keyboard_card(Canvas &c, cJSON *root, int y, int max_y) {
    cJSON *val = cJSON_GetObjectItem(root, "value");
    cJSON *sh  = cJSON_GetObjectItem(root, "shift");
    cJSON *sy  = cJSON_GetObjectItem(root, "sym");
    const bool shift = cJSON_IsBool(sh) && cJSON_IsTrue(sh);
    const bool sym   = cJSON_IsBool(sy) && cJSON_IsTrue(sy);
    // value box
    const int vh = 44;
    c.draw_rect(kMargin, y, s_w - 2 * kMargin, vh, GrayLevel::Black);
    char vbuf[64] = "";
    if (cJSON_IsString(val)) strlcpy(vbuf, val->valuestring, sizeof vbuf);
    // TAIL-CLIP (iter-4 finding): right-anchor the tail chars that fit; the
    // ellipsis '~' marks the cut. A text field shows its TAIL while you type.
    const int avail_px  = s_w - 2 * kMargin - 20 - 5;  // pad-left + caret room
    const int max_chars = avail_px / (kGlyphW * 2);
    const char *vshow = vbuf;
    char tail[64];
    const int vlen = (int)strlen(vbuf);
    if (vlen > max_chars && max_chars > 1) {
        snprintf(tail, sizeof tail, "%c%s", '~',
                 vbuf + (vlen - (max_chars - 1)));
        vshow = tail;
    }
    c.draw_text(kMargin + 10, y + (vh - kGlyphH * 2) / 2, vshow, 2,
                GrayLevel::Black);
    // caret so an empty field still reads as "type here" — clamped in-box
    int caret_x = kMargin + 10 + text_width(vshow, 2) + 4;  // real ink + gap
    const int caret_max = kMargin + (s_w - 2 * kMargin) - 6;
    if (caret_x > caret_max) caret_x = caret_max;
    c.fill_rect(caret_x, y + 8, 3, vh - 16, GrayLevel::DarkGray);
    int top = y + vh + 12;

    // Two planes, same geometry. Symbols follow iOS's ?123 arrangement so a
    // phone-trained thumb finds @ and ? where it expects them.
    static const char *kLetters[4] = { "1234567890", "qwertyuiop", "asdfghjkl",
                                       "zxcvbnm" };
    static const char *kSymbols[4] = { "1234567890", "-/:;()$&@\"",
                                       ".,?!'#%^*+", "=_\\|~<>[]" };
    const char **kRows = sym ? kSymbols : kLetters;
    const int gap = 6;
    const int rows_h = max_y - top - gap;
    // UI_SPEC §3/§5: portrait has the height — keys grow to the 80px floor
    // and the block BOTTOM-ANCHORS (one-thumb reachability). Landscape keeps
    // its sanctioned 54px exemption. Any slack becomes a gap between the
    // value box and the keys, not dead glass under OK.
    const bool portrait = s_h > s_w;
    const int kh = std::min(portrait ? 84 : 54, rows_h / 5 - gap);
    const int block_h = 5 * kh + 4 * gap;
    if (max_y - block_h > top) top = max_y - block_h;
    for (int r = 0; r < 4; ++r) {
        const char *row = kRows[r];
        int n = (int)strlen(row);
        int extra = (r == 3) ? 2 : 0;  // shift(/plane hint) + backspace flank row 4
        int total = n + extra;
        int kw = (s_w - 2 * kMargin - gap * (total - 1)) / total;
        int x = kMargin;
        int ry = top + r * (kh + gap);
        if (r == 3) {  // shift key first (letters); on sym it toggles back
            if (sym) {
                kb_key_face(c, x, ry, kw, kh, "abc", 1, false, false);
                stage_region(x, ry, kw, kh, "abc", "k:sym");
            } else {
                kb_key_face(c, x, ry, kw, kh, "^", 2, false, shift);
                stage_region(x, ry, kw, kh, "shift", "k:shift");
            }
            x += kw + gap;
        }
        for (int i = 0; i < n; ++i) {
            char ch = row[i];
            char lab[2] = { (!sym && shift) ? (char)toupper((unsigned char)ch)
                                            : ch, 0 };
            char id[8];
            snprintf(id, sizeof id, "k:%c", lab[0]);
            kb_key_face(c, x, ry, kw, kh, lab, 2, false, false);
            stage_region(x, ry, kw, kh, lab, id);
            x += kw + gap;
        }
        if (r == 3) {  // backspace last
            kb_key_face(c, x, ry, kw, kh, "<", 2, false, false);
            stage_region(x, ry, kw, kh, "bksp", "k:bksp");
        }
    }
    // bottom row: cancel / plane toggle / space / period / OK — the phone
    // arrangement. Period earns a dedicated key (the single most typed
    // symbol); the toggle names the OTHER plane, like iOS.
    int ry = top + 4 * (kh + gap);
    const char *plane_lab = sym ? "abc" : "?123";
    struct { const char *lab; const char *id; int units; int scale; bool dark; }
    specials[5] = {
        { "x",       "k:cancel", 1, 3, false },  // UI_SPEC §6: close reads BIG
        { plane_lab, "k:sym",    2, 2, false },
        { "space",   "k:space",  4, 2, false },
        { ".",       "k:.",      1, 2, false },
        { "OK",      "k:ok",     2, 2, true  } };
    int units = 10;
    int uw = (s_w - 2 * kMargin - gap * 4) / units;
    int x = kMargin;
    for (auto &sp : specials) {
        int w = uw * sp.units + (sp.units - 1);
        kb_key_face(c, x, ry, w, kh, sp.lab, sp.scale, sp.dark, false);
        stage_region(x, ry, w, kh, sp.lab, sp.id);
        x += w + gap;
    }
}

// menu: {"type":"menu","items":[{"label":…,"id":…,"note":…} | "plain", …]}
// Full-width tappable rows — the settings-list building block. Up to 24 items
// with scroll; only rows inside the visible window become tap regions.
static int render_menu_card(Canvas &c, cJSON *root, int y, int max_y) {
    cJSON *items = cJSON_GetObjectItem(root, "items");
    if (!cJSON_IsArray(items)) return y;
    // UI_SPEC §3: 64px rows, 16px gutters — wifi and dm-inbox both ride this.
    const int gap = 16, rh = 64;
    int n = std::min(cJSON_GetArraySize(items), 24);
    for (int i = 0; i < n && y + rh <= max_y; ++i) {
        cJSON *it = cJSON_GetArrayItem(items, i);
        // id is 48 like tiny_button_region_t: the messages app puts its
        // destination in a row id ("m:t:<login>"), and a clipped id opens the
        // wrong conversation instead of failing loudly.
        // `raw` (original UTF-8) is what the region remembers and
        // ui_tap reports; `lab` (folded) is what the glass draws. Two labels,
        // two audiences — the fold must never travel upstream.
        char lab[64] = "", raw[64] = "", note[40] = "", id[48] = "";
        if (cJSON_IsString(it)) {
            copy_label_utf8(raw, sizeof raw, it->valuestring);
            ascii_fold(lab, sizeof lab, it->valuestring);
            snprintf(id, sizeof id, "m:%d", i);
        } else if (cJSON_IsObject(it)) {
            cJSON *l = cJSON_GetObjectItem(it, "label");
            cJSON *o = cJSON_GetObjectItem(it, "note");
            cJSON *d = cJSON_GetObjectItem(it, "id");
            if (cJSON_IsString(l)) {
                copy_label_utf8(raw, sizeof raw, l->valuestring);
                ascii_fold(lab, sizeof lab, l->valuestring);
            }
            if (cJSON_IsString(o)) ascii_fold(note, sizeof note, o->valuestring);
            if (cJSON_IsString(d)) strlcpy(id, d->valuestring, sizeof id);
            else snprintf(id, sizeof id, "m:%d", i);
        } else continue;
        c.draw_rect(kMargin, y, s_w - 2 * kMargin, rh, GrayLevel::Black);
        // UI_SPEC §4: interior ink stops >=12px before the border and a cut
        // is visible ("..."), never a mid-token slice against the frame.
        const int row_w = s_w - 2 * kMargin;
        const int note_w = note[0] ? text_width(note, 1) + 16 : 0;
        const int lab_max = row_w - 12 - 12 - note_w;
        if (text_width(lab, 2) > lab_max) {
            size_t ll = strlen(lab);
            while (ll > 0) {
                strcpy(lab + ll, "...");
                if (text_width(lab, 2) <= lab_max) break;
                lab[--ll] = 0;
            }
        }
        c.draw_text(kMargin + 12, y + (rh - kGlyphH * 2) / 2, lab, 2,
                    GrayLevel::Black);
        if (note[0]) {
            int nx = s_w - kMargin - 12 - text_width(note, 1);
            c.draw_text(nx, y + (rh - kGlyphH) / 2, note, 1, GrayLevel::DarkGray);
        }
        // Off-window rows (scrolled away) are drawn-and-clipped, never tappable.
        if (y >= s_view_top - 4 && y + rh <= s_view_bot + 4)
            stage_region(kMargin, y, s_w - 2 * kMargin, rh, raw, id);
        y += rh + gap;
    }
    return y;
}

// chat: {"type":"chat","messages":[{"role":"user|assistant","body":"…"},…]}
// The iOS conversation, translated to 4-gray (ios/DESIGN.md):
//   user      = LightGray fill, right-aligned   (accent 22% bubble)
//   assistant = White fill + Black outline, left (secondary-bg bubble)
// Radius 12 ~ the 18pt bubble at this pixel density. Bubbles size to their
// text via draw_wrapped's measure pass — the SAME wrap rules that paint,
// so the fill can never disagree with the words by a line.
static int render_chat_card(Canvas &c, cJSON *root, int y, int max_y) {
    cJSON *msgs = cJSON_GetObjectItem(root, "messages");
    if (!cJSON_IsArray(msgs)) {
        c.draw_text(kMargin, y, "(chat card has no \"messages\" array)",
                    kBodyScale, GrayLevel::Black);
        return y + kGlyphH * kBodyScale;
    }
    const int gap = 14, pad = 12, radius = 12;
    const int lane_w = s_w - 2 * kMargin;
    const int bub_max = lane_w * 7 / 10;         // §6 v1.2: 70% max-width —
    cJSON *m = nullptr;
    cJSON_ArrayForEach(m, msgs) {
        if (!cJSON_IsObject(m)) continue;
        const cJSON *rl = cJSON_GetObjectItem(m, "role");
        const cJSON *bd = cJSON_GetObjectItem(m, "body");
        const char *body = cJSON_IsString(bd) ? bd->valuestring : "";
        const bool user = cJSON_IsString(rl) && strcmp(rl->valuestring, "user") == 0;
        if (!*body) continue;
        // Measure with the painting code (paint=false), then shrink a short
        // single line to its own width so "Yes." is a chip, not a banner.
        const int text_w = bub_max - 2 * pad;
        const int end = draw_wrapped(c, 0, 0, body, kBodyScale, text_w,
                                     1 << 28, /*paint=*/false);
        const int th = end - 0 - 6;              // last line's +6 trimmed
        int bw = bub_max;
        if (th <= kGlyphH * kBodyScale) {        // single line: fit to text
            char ft[128];
            ascii_fold(ft, sizeof ft, body);
            bw = std::min(bub_max, text_width(ft, kBodyScale) + 2 * pad);
        }
        const int bh = th + 2 * pad;
        const int bx = user ? (s_w - kMargin - bw) : kMargin;
        if (user) {
            fill_round_rect(c, bx, y, bw, bh, radius, GrayLevel::LightGray);
        } else {
            fill_round_rect(c, bx, y, bw, bh, radius, GrayLevel::White);
            draw_round_rect(c, bx, y, bw, bh, radius, GrayLevel::Black);
        }
        draw_wrapped(c, bx + pad, y + pad, body, kBodyScale, bw - 2 * pad,
                     1 << 28);
        y += bh + gap;
    }
    return y;
}

// qr: {"type":"qr","text":"WIFI:S:tiny-1a2b;T:WPA;P:tinysetup;;", "caption":"…"}
// Why this exists: the softAP provisioning portal has to hand a phone an SSID and
// a password, and typing them off a 4-gray panel is the worst part of setting the
// device up. Modules are painted with fill_rect, one rect per dark module — never
// per pixel.
//
// Two honesty rules, because an unscannable QR looks exactly like a scannable one
// from across the room:
//   * below 3 px/module a phone camera gives up on e-ink, so instead of painting
//     a decorative square we say how much room the symbol needed;
//   * if the text does not fit the version cap, we name the byte count.
struct QrPaint {
    Canvas *c;
    int box_x, box_y, box_w, box_h;
    int modules;      // side length in modules, 0 if the callback never ran
    int scale;        // px per module actually painted, 0 = refused to paint
    bool grow;        // scroll path: paint at the 3 px floor even if it overflows
    int  side;        // px actually painted (span * scale), for the flow's end-y
};

static void qr_paint_cb(esp_qrcode_handle_t qr, void *user) {
    QrPaint *p = static_cast<QrPaint *>(user);
    const int n = esp_qrcode_get_size(qr);
    if (n <= 0) return;
    p->modules = n;

    // 4 modules is the spec's quiet zone; the card margin already supplies white
    // around the box, so 2 inside the box plus the margin is plenty in practice.
    const int quiet = 2;
    const int span = n + 2 * quiet;
    int scale = std::min(p->box_w, p->box_h) / span;
    if (scale > 12) scale = 12;   // no point wasting the panel on a huge symbol
    if (scale < 3) {
        // In a scrolling composite the symbol may run past the view — the
        // rail brings it back. Only a card that CANNOT scroll refuses.
        if (!p->grow) { p->scale = 0; return; }
        scale = 3;
    }

    const int side = span * scale;
    p->side = side;
    const int ox = p->box_x + (p->box_w - side) / 2;
    // Centre only when it fits; a grown symbol is top-aligned so the flow's
    // end-y (box_y + side) is where the ink actually stops.
    const int oy = side <= p->box_h ? p->box_y + (p->box_h - side) / 2 : p->box_y;
    p->c->fill_rect(ox, oy, side, side, GrayLevel::White);
    const int dx = ox + quiet * scale;
    const int dy = oy + quiet * scale;
    for (int my = 0; my < n; ++my) {
        for (int mx = 0; mx < n; ++mx) {
            if (esp_qrcode_get_module(qr, mx, my))
                p->c->fill_rect(dx + mx * scale, dy + my * scale, scale, scale,
                                GrayLevel::Black);
        }
    }
    p->scale = scale;
}

// Returns the y AFTER the symbol (+caption) so a composite's flow continues
// below it. In the scroll path max_y is the measuring horizon (1<<28), not a
// floor: the symbol sizes itself against the VISIBLE remainder (s_view_bot)
// and, if that is under 3 px/module, paints at the floor and lets the rail
// scroll to it. Before this fix a qr part in a composite computed a 2^27 px
// box, centred the symbol off-canvas and reported max_y as its end — a blank
// page with a phantom scrollbar (first seen on the onboarding wi-fi page).
static int render_qr_card(Canvas &c, cJSON *root, int y, int max_y) {
    cJSON *t = cJSON_GetObjectItem(root, "text");
    if (!cJSON_IsString(t)) t = cJSON_GetObjectItem(root, "data");
    if (!cJSON_IsString(t)) t = cJSON_GetObjectItem(root, "payload");
    if (!cJSON_IsString(t) || t->valuestring[0] == '\0') {
        c.draw_text(kMargin, y, "(qr card has no \"text\" to encode)", kBodyScale,
                    GrayLevel::Black);
        return y + kGlyphH * kBodyScale + 6;
    }

    const bool measuring = max_y > s_h;             // scroll path horizon
    const int floor = measuring ? std::min(s_view_bot, s_h - kMargin) : max_y;
    int box_h = floor - y;
    if (!measuring && box_h < 40) return max_y;
    if (box_h < 40) box_h = 40;                      // grown symbol decides

    // Optional caption under the symbol — the payload in glyphs, for a human with
    // no camera or a phone that refuses to focus.
    cJSON *cap = cJSON_GetObjectItem(root, "caption");
    const bool have_cap = cJSON_IsString(cap) && cap->valuestring[0];
    if (have_cap) box_h -= kGlyphH + 8;

    QrPaint paint = {&c, kMargin, y, s_w - 2 * kMargin, box_h, 0, 0, measuring, 0};
    esp_qrcode_config_t cfg = {};
    cfg.display_func_with_cb = qr_paint_cb;
    cfg.max_qrcode_version = 10;              // 10 = 57x57 modules, ~200 B at MED
    cfg.qrcode_ecc_level = ESP_QRCODE_ECC_MED;
    cfg.user_data = &paint;                   // non-NULL selects the _with_cb arm

    const size_t len = strlen(t->valuestring);
    const esp_err_t err = esp_qrcode_generate(&cfg, t->valuestring);
    char note[112];
    if (err != ESP_OK) {
        snprintf(note, sizeof note,
                 "could not encode %u bytes as a QR (%s)", (unsigned)len,
                 esp_err_to_name(err));
        c.draw_text(kMargin, y, note, kBodyScale, GrayLevel::Black);
        return draw_wrapped(c, kMargin, y + kGlyphH * kBodyScale + 10, t->valuestring,
                            kSmallScale, s_w - 2 * kMargin, max_y);
    }
    if (paint.scale == 0) {
        snprintf(note, sizeof note,
                 "qr needs more room: %d modules, %d px tall box (3 px/module min)",
                 paint.modules, box_h);
        c.draw_text(kMargin, y, note, kSmallScale, GrayLevel::Black);
        return draw_wrapped(c, kMargin, y + kGlyphH + 10, t->valuestring, kSmallScale,
                            s_w - 2 * kMargin, max_y);
    }
    // Ink stops at the symbol's real bottom (centred: the box; grown: y+side).
    const int ink_bot = paint.side <= box_h ? y + box_h : y + paint.side;
    int end = ink_bot;
    if (have_cap) {
        char cf[160];
        ascii_fold(cf, sizeof cf, cap->valuestring);
        const int cw = text_width(cf, kSmallScale);
        const int cx = std::max(kMargin, (s_w - cw) / 2);
        c.draw_text(cx, ink_bot + 6, cf, kSmallScale, GrayLevel::Black);
        end = ink_bot + 6 + kGlyphH + 2;
    }
    ESP_LOGI(TAG, "qr: %u bytes -> %d modules at %d px/module%s", (unsigned)len,
             paint.modules, paint.scale, paint.side > box_h ? " (grown, scrolls)" : "");
    return end;
}

// Body dispatcher, shared by whole cards and by each part of a composite.
// `y` is the first free row, `max_y` the last row the body may touch.
// Returns the y AFTER the content it drew (or would have drawn, when the
// canvas clip swallowed rows) — that is how scroll measures content height.
static int render_body(Canvas &c, cJSON *root, const char *type_s, int y,
                       int max_y);


// chart: {"type":"chart","data":[n,…],"labels":["…",…]?,"style":"line"|"bar"?,
//         "unit":"%"?,"y_min":n?,"y_max":n?}
// Sparkline-class, not gnuplot: one series, auto-scaled unless y_min/y_max pin
// the range (pin them for battery% so 87->86 does not render as a cliff).
// Scale values are DRAWN on the axis — a chart that hides its scale is a mood,
// not a measurement. Honest-void idiom: bad input says so on glass.
static int render_chart_card(Canvas &c, cJSON *root, int y, int max_y) {
    cJSON *data = cJSON_GetObjectItem(root, "data");
    int n = data ? cJSON_GetArraySize(data) : 0;
    if (!cJSON_IsArray(data) || n < 2) {
        c.draw_text(kMargin, y, "(chart needs \"data\": [2+ numbers])",
                    kBodyScale, GrayLevel::Black);
        return y + kGlyphH * kBodyScale;
    }
    if (n > 160) n = 160;  // ~4px/point at full width is already dense

    static float vals[160];  // render is single-flight (same rule as fold buf)
    float vmin = 1e30f, vmax = -1e30f;
    for (int i = 0; i < n; i++) {
        cJSON *it = cJSON_GetArrayItem(data, i);
        vals[i] = cJSON_IsNumber(it) ? (float)it->valuedouble : 0.0f;
        if (vals[i] < vmin) vmin = vals[i];
        if (vals[i] > vmax) vmax = vals[i];
    }
    cJSON *jmin = cJSON_GetObjectItem(root, "y_min");
    cJSON *jmax = cJSON_GetObjectItem(root, "y_max");
    if (cJSON_IsNumber(jmin)) vmin = (float)jmin->valuedouble;
    if (cJSON_IsNumber(jmax)) vmax = (float)jmax->valuedouble;
    if (vmax <= vmin) vmax = vmin + 1.0f;  // flat series still draws mid-plot

    const cJSON *style = cJSON_GetObjectItem(root, "style");
    const bool bars = cJSON_IsString(style) && strcmp(style->valuestring, "bar") == 0;
    const cJSON *unit = cJSON_GetObjectItem(root, "unit");
    const char *unit_s = cJSON_IsString(unit) ? unit->valuestring : "";

    const int plot_h = std::min(220, max_y - y - kGlyphH * kBodyScale - 8);
    if (plot_h < 60) {  // composite squeezed us: refuse an unreadable smear
        c.draw_text(kMargin, y, "(no room for chart)", kBodyScale, GrayLevel::Black);
        return y + kGlyphH * kBodyScale;
    }
    char axis[32];
    snprintf(axis, sizeof axis, "%.4g%s", (double)vmax, unit_s);
    int gutter = std::max(text_width(axis, kBodyScale), 40) + 8;
    snprintf(axis, sizeof axis, "%.4g%s", (double)vmin, unit_s);
    gutter = std::max(gutter, text_width(axis, kBodyScale) + 8);
    const int px0 = kMargin + gutter, px1 = s_w - kMargin;
    const int py0 = y, py1 = y + plot_h;
    const int pw = px1 - px0;

    // Frame + scale (max top-left, min bottom-left) + dotted midline.
    c.draw_line(px0, py0, px0, py1);
    c.draw_line(px0, py1, px1, py1);
    snprintf(axis, sizeof axis, "%.4g%s", (double)vmax, unit_s);
    c.draw_text(kMargin, py0, axis, kBodyScale, GrayLevel::Black);
    snprintf(axis, sizeof axis, "%.4g%s", (double)vmin, unit_s);
    c.draw_text(kMargin, py1 - kGlyphH * kBodyScale, axis, kBodyScale,
                GrayLevel::Black);
    for (int x = px0; x < px1; x += 8)
        c.draw_pixel(x, (py0 + py1) / 2, GrayLevel::DarkGray);

    auto ymap = [&](float v) {
        float f = (v - vmin) / (vmax - vmin);
        if (f < 0) f = 0;
        if (f > 1) f = 1;
        return py1 - (int)(f * (py1 - py0 - 2)) - 1;
    };
    if (bars) {
        int bw = std::max(2, pw / n - 2);
        for (int i = 0; i < n; i++) {
            int bx = px0 + 1 + (int)((int64_t)i * pw / n);
            int by = ymap(vals[i]);
            c.fill_rect(bx, by, bw, py1 - by, GrayLevel::Black);
        }
    } else {
        for (int i = 1; i < n; i++) {
            int xa = px0 + 1 + (int)((int64_t)(i - 1) * (pw - 2) / (n - 1));
            int xb = px0 + 1 + (int)((int64_t)i * (pw - 2) / (n - 1));
            int ya = ymap(vals[i - 1]), yb = ymap(vals[i]);
            c.draw_line(xa, ya, xb, yb);
            c.draw_line(xa, ya + 1, xb, yb + 1);  // 2px stroke: 1px ghosts on e-ink
        }
    }
    // Labels row: first left-aligned, last right-aligned. More would collide
    // at this glyph size; senders get exactly the two that fit.
    y = py1 + 4;
    cJSON *labels = cJSON_GetObjectItem(root, "labels");
    if (cJSON_IsArray(labels) && cJSON_GetArraySize(labels) >= 1) {
        cJSON *first = cJSON_GetArrayItem(labels, 0);
        cJSON *last  = cJSON_GetArrayItem(labels, cJSON_GetArraySize(labels) - 1);
        char fl[64];
        if (cJSON_IsString(first)) {
            ascii_fold(fl, sizeof fl, first->valuestring);
            c.draw_text(px0, y, fl, kBodyScale, GrayLevel::Black);
        }
        if (cJSON_IsString(last) && last != first) {
            ascii_fold(fl, sizeof fl, last->valuestring);
            c.draw_text(px1 - text_width(fl, kBodyScale), y, fl, kBodyScale,
                        GrayLevel::Black);
        }
        y += kGlyphH * kBodyScale;
    }
    return y + 4;
}

// composite: {"type":"composite","parts":[{card}, {card}, …]}
// FLOW layout: each part renders at
// its NATURAL height — small heading, hairline between, running y — and the
// true end-y is returned so the scroll rail can measure overflow instead
// of the old equal-slot split silently squeezing parts (the old bug: the
// settings vitals lost rows to a slot they never asked for). No buttons/footer
// per part — those belong to the card.
static int render_composite_card(Canvas &c, cJSON *root, int y, int max_y) {
    cJSON *parts = cJSON_GetObjectItem(root, "parts");
    if (!cJSON_IsArray(parts)) parts = cJSON_GetObjectItem(root, "cards");
    if (!cJSON_IsArray(parts)) return y;
    int n = cJSON_GetArraySize(parts);
    if (n <= 0) return y;
    n = std::min(n, 12);  // sanity bound; scroll makes >4 legible now
    for (int i = 0; i < n; ++i) {
        cJSON *p = cJSON_GetArrayItem(parts, i);
        if (!cJSON_IsObject(p)) continue;
        if (i > 0) {
            y += 10;
            c.fill_rect(kMargin, y, s_w - 2 * kMargin, 1, GrayLevel::Black);
            y += 12;
        }
        cJSON *pt = cJSON_GetObjectItem(p, "title");
        if (cJSON_IsString(pt) && pt->valuestring[0]) {
            char ft[96]; ascii_fold(ft, sizeof ft, pt->valuestring);
            c.draw_text(kMargin, y, ft, kBodyScale, GrayLevel::Black);
            y += kGlyphH * kBodyScale + 6;
        }
        cJSON *ptype = cJSON_GetObjectItem(p, "type");
        // max_y passes through untouched: in the scroll path it is the
        // measuring horizon (1<<28) and the canvas y-clip guards the glass;
        // in a fitting card it is the real body floor.
        y = render_body(c, p, cJSON_IsString(ptype) ? ptype->valuestring : "text",
                        y, max_y);
    }
    return y;
}

// agent_home: the bespoke conversation surface (the owner's own design,
// hand-tuned rather than derived from a generic card).
// Not a composite: home earns its own geometry because a generic
// card cannot place corner glyphs or a bottom input bar. Layout, top to
// bottom: the always-drawn glance strip (title bar, no title) → gear glyph
// top-LEFT / chat bubble + unread top-RIGHT (tap targets 88px ≥ the 80px
// law) → conversation canvas (greeting, or the last answer preview — the
// typer streams onto this same card_id, so answers appear HERE, no page
// change) → "Message tiny..." input bar + mic zone. Region ids reuse the
// touch router's existing vocabulary (settings / m:inbox / type / speak):
// zero routing changes, the defuse rule holds (ids present ⇒ labels inert).
static void render_agent_home_card(Canvas &c, cJSON *root, int y, int max_y) {
    const cJSON *pv = cJSON_GetObjectItem(root, "preview");
    const char *preview = cJSON_IsString(pv) ? pv->valuestring : "";
    const int unread = tiny_node_unread();

    // ---- corner targets ----
    const int T = 88;                       // tap square (≥80px law)
    const int icon_y = y + 2;
    {   // settings: slider-triplet glyph in a rounded outline (drawable with
        // rect primitives, reads as "controls" at arm's length)
        const int gx = kMargin, gy = icon_y, gs = 60;
        draw_round_rect(c, gx, gy, gs, gs, 12, GrayLevel::Black);
        for (int i = 0; i < 3; ++i) {
            const int ly = gy + 15 + i * 15;
            c.fill_rect(gx + 11, ly, gs - 22, 3, GrayLevel::Black);
            const int knob_x = gx + 11 + (i == 1 ? gs - 22 - 14 : 6);
            c.fill_rect(knob_x, ly - 4, 8, 11, GrayLevel::Black);
        }
        stage_region(gx - 10, gy - 10, T, T, "settings", "settings");
    }
    {   // gallery: framed-photo glyph — mountains + sun,
        // readable at arm's length with rect primitives only.
        const int gx = kMargin + 88 + 8, gy = icon_y, gs = 60;
        draw_round_rect(c, gx, gy, gs, gs, 12, GrayLevel::Black);
        c.fill_rect(gx + 38, gy + 12, 9, 9, GrayLevel::Black);       // sun
        for (int i = 0; i < 5; ++i) {                                 // peak L
            const int w2 = 3 + i * 4;
            c.fill_rect(gx + 22 - w2 / 2, gy + 26 + i * 4, w2, 4,
                        GrayLevel::Black);
        }
        for (int i = 0; i < 3; ++i) {                                 // peak R
            const int w2 = 3 + i * 4;
            c.fill_rect(gx + 40 - w2 / 2, gy + 34 + i * 4, w2, 4,
                        GrayLevel::DarkGray);
        }
        stage_region(gx - 10, gy - 10, T, T, "gallery", "g:open");
    }
    {   // chats: speech bubble; unread count inside when there is one
        const int bw = 62, bh = 48;
        const int bx = s_w - kMargin - bw, by = icon_y + 2;
        draw_round_rect(c, bx, by, bw, bh, 14, GrayLevel::Black);
        // Tail starts PAST the corner radius (14) and tapers flush from
        // the bottom edge — the old L-stroke at x+12 collided with the curve
        // and read as detached ink.
        for (int i = 0; i < 4; ++i)
            c.fill_rect(bx + 18, by + bh - 1 + i * 2, 10 - i * 3, 2,
                        GrayLevel::Black);
        if (unread > 0) {
            char n[8];
            snprintf(n, sizeof n, "%d", unread > 99 ? 99 : unread);
            const int tw = text_width(n, 2);
            c.draw_text(bx + (bw - tw) / 2, by + (bh - kGlyphH * 2) / 2, n, 2,
                        GrayLevel::Black);
        } else {
            for (int i = 0; i < 3; ++i)     // idle: ellipsis dots
                c.fill_rect(bx + 15 + i * 12, by + bh / 2 - 2, 5, 5,
                            GrayLevel::DarkGray);
        }
        stage_region(s_w - kMargin - T + 10, by - 10, T, T, "chats", "m:inbox");
    }

    // ---- bottom input bar ----
    // v11: the input bar names WHO ANSWERS. When a universe agent is active
    // the field reads "Message @slug..." and a badge sits in the header —
    // the owner must never mistake a persona's words for his own tiny's.
    const char *agent = tiny_agent_current();
    if (agent[0]) {
        char badge[TINY_AGENT_SLUG_MAX + 2];
        snprintf(badge, sizeof badge, "@%s", agent);
        // Centered between the gallery icon and the chats bubble; tappable —
        // it IS the universe page's front door on home.
        const int bx0 = kMargin + 2 * (88 + 8), bx1 = s_w - kMargin - 88;
        int bw2 = text_width(badge, 2);
        int bx = bx0 + (bx1 - bx0 - bw2) / 2;
        if (bx < bx0) bx = bx0;
        c.draw_text(bx, icon_y + (60 - kGlyphH * 2) / 2, badge, 2,
                    GrayLevel::Black);
        c.fill_rect(bx, icon_y + (60 - kGlyphH * 2) / 2 + kGlyphH * 2 + 3,
                    bw2, 2, GrayLevel::Black);   // underline: "this is a link"
        stage_region(bx - 12, icon_y - 10, bw2 + 24, 88, badge, "u:open");
    }
    const int bar_h = 72;
    const int bar_y = max_y - bar_h;
    const int bar_x = kMargin, bar_w = s_w - 2 * kMargin;
    const int mic_w = 84;
    draw_round_rect(c, bar_x, bar_y, bar_w, bar_h, 20, GrayLevel::Black);
    c.fill_rect(bar_x + bar_w - mic_w, bar_y + 12, 2, bar_h - 24,
                GrayLevel::LightGray);      // field/mic divider
    {   // mic glyph: filled capsule + stem + base
        const int cx = bar_x + bar_w - mic_w / 2;
        const int cy = bar_y + bar_h / 2 - 6;
        draw_round_rect(c, cx - 8, cy - 16, 16, 26, 8, GrayLevel::Black);
        c.fill_rect(cx - 5, cy - 12, 10, 18, GrayLevel::Black);
        c.fill_rect(cx - 1, cy + 10, 3, 8, GrayLevel::Black);
        c.fill_rect(cx - 10, cy + 18, 21, 3, GrayLevel::Black);
    }
    {   // v11: the placeholder names the active agent — "Message @slug..."
        char ph[TINY_AGENT_SLUG_MAX + 16];
        snprintf(ph, sizeof ph, "Message %s%s...",
                 agent[0] ? "@" : "", agent[0] ? agent : "tiny");
        c.draw_text(bar_x + 20, bar_y + (bar_h - text_ink_h(2)) / 2, ph,
                    2, GrayLevel::DarkGray);
    }
    stage_region(bar_x, bar_y, bar_w - mic_w, bar_h, "Message tiny", "type");
    stage_region(bar_x + bar_w - mic_w, bar_y, mic_w, bar_h, "mic", "speak");

    // ---- conversation canvas ----
    const int canvas_top = icon_y + T;
    const int hint_h = kGlyphH + 8;
    const int canvas_bot = bar_y - hint_h - 6;
    const cJSON *sb = cJSON_GetObjectItem(root, "stream_body");
    const cJSON *qq = cJSON_GetObjectItem(root, "q");
    const cJSON *nt = cJSON_GetObjectItem(root, "note");
    if (cJSON_IsString(sb)) {
        // Conversation mode (the ask streams HERE and stays).
        int yy = canvas_top;
        if (cJSON_IsString(qq) && qq->valuestring[0]) {
            char qline[140];
            snprintf(qline, sizeof qline, "you: %s", qq->valuestring);
            yy = draw_wrapped(c, kMargin, yy, qline, 1,
                              s_w - 2 * kMargin, canvas_bot) + 4;
        }
        const char *body = sb->valuestring;
        const bool caret = cJSON_IsTrue(cJSON_GetObjectItem(root, "caret"));
        if (!body[0]) {
            const char *note = cJSON_IsString(nt) && nt->valuestring[0]
                                   ? nt->valuestring : "...";
            const int tw = text_width(note, 2);
            c.draw_text(std::max(kMargin, (s_w - tw) / 2),
                        yy + (canvas_bot - yy) / 2 - kGlyphH, note, 2,
                        GrayLevel::DarkGray);
        } else {
            // Tail-follow: when the answer outgrows the canvas, drop head
            // lines so the NEWEST words are always visible (the typer must
            // never type off-glass). A leading "..." names the trim.
            const int cpl = std::max(1, (s_w - 2 * kMargin) / (kGlyphW * 2));
            const char *show = body;
            bool trimmed = false;
            while (*show && draw_wrapped(c, kMargin, yy, show, 2,
                                         s_w - 2 * kMargin, 1 << 28,
                                         /*paint=*/false) > canvas_bot) {
                const char *adv = show + cpl;
                while (*adv && *adv != ' ' && *adv != '\n') ++adv;
                while (*adv == ' ' || *adv == '\n') ++adv;
                if (!*adv) break;
                show = adv;
                trimmed = true;
            }
            int ty = yy;
            if (trimmed) {
                c.draw_text(kMargin, ty, "...", 1, GrayLevel::DarkGray);
                ty += kGlyphH + 4;
            }
            const int end = draw_wrapped(c, kMargin, ty, show, 2,
                                         s_w - 2 * kMargin, canvas_bot);
            if (caret) {
                const int ch = kGlyphH * 2;
                const int cy2 = end + ch <= canvas_bot ? end : canvas_bot - ch;
                c.fill_rect(kMargin, cy2, 10, ch, GrayLevel::DarkGray);
            }
        }
    } else if (preview[0]) {
        c.draw_text(kMargin, canvas_top, "tiny:", 1, GrayLevel::DarkGray);
        draw_wrapped(c, kMargin, canvas_top + kGlyphH + 6, preview, 2,
                     s_w - 2 * kMargin, canvas_bot);
    } else {
        const char *hi = "hi - i'm tiny";
        int tw = text_width(hi, 3);
        int cy0 = canvas_top + (canvas_bot - canvas_top) / 2 - kGlyphH * 3;
        if (cy0 < canvas_top) cy0 = canvas_top;
        c.draw_text((s_w - tw) / 2, cy0, hi, 3, GrayLevel::Black);
        const char *sub = "ask me anything";
        tw = text_width(sub, 2);
        c.draw_text((s_w - tw) / 2, cy0 + kGlyphH * 3 + 12, sub, 2,
                    GrayLevel::DarkGray);
    }
    const char *hint = "tap to type - mic or hold AI to speak";
    const int hw = text_width(hint, 1);
    c.draw_text(std::max(kMargin, (s_w - hw) / 2), bar_y - hint_h, hint, 1,
                GrayLevel::DarkGray);
}

static int render_body(Canvas &c, cJSON *root, const char *type_s, int y,
                       int max_y) {
    if (strcmp(type_s, "list") == 0)           return render_list_card(c, root, y, max_y);
    else if (strcmp(type_s, "kv") == 0)        return render_kv_card(c, root, y, max_y);
    else if (strcmp(type_s, "text") == 0)      return render_text_card(c, root, y, max_y);
    else if (strcmp(type_s, "composite") == 0) return render_composite_card(c, root, y, max_y);
    else if (strcmp(type_s, "chart") == 0)     return render_chart_card(c, root, y, max_y);
    else if (strcmp(type_s, "qr") == 0)        return render_qr_card(c, root, y, max_y);
    else if (strcmp(type_s, "keyboard") == 0)  { render_keyboard_card(c, root, y, max_y); return max_y; }
    else if (strcmp(type_s, "menu") == 0)      return render_menu_card(c, root, y, max_y);
    else if (strcmp(type_s, "chat") == 0)      return render_chat_card(c, root, y, max_y);
    else if (strcmp(type_s, "agent_home") == 0) { render_agent_home_card(c, root, y, max_y); return max_y; }
    else if (strcmp(type_s, "buttons") == 0)   { return y; /* bar is drawn by the caller */ }
    else {
        // Honest degrade: name the type we could not draw, then show whatever
        // `body` it carried — never a blank screen.
        char msg[96];
        snprintf(msg, sizeof(msg), "unsupported card type: %s", type_s);
        c.draw_text(kMargin, y, msg, kBodyScale, GrayLevel::Black);
        return render_text_card(c, root, y + kGlyphH * kBodyScale + 12, max_y);
    }
}

extern "C" esp_err_t tiny_display_init(void) {
    esp_err_t err = sticky_display_init();
    if (!s_display_mux) s_display_mux = xSemaphoreCreateMutex();
    if (!s_region_mux) s_region_mux = xSemaphoreCreateMutex();
    if (!s_ack_q) {
        s_ack_q = xQueueCreate(2, sizeof(int));
        // prio 4: below touch (5) so sampling always wins, above idle work.
        if (s_ack_q) xTaskCreate(ack_task, "disp_ack", 3072, NULL, 4, NULL);
    }
    return err;
}

// ---- image card -------------------------------------------------------
// {"type":"image","url":"https://.../<sha16>/0.raw"} — the remote render rail
// (docs/API_CONTRACT.md). The device is a dumb pixel-pusher: the server
// dithers and packs CANVAS-SPACE bytes; length picks the format. 48000 B =
// 1-bit (expand to gray4 canvas), 96000 B = gray4 (memcpy — the canvas IS
// 2bpp packed). Fetch happens BEFORE display_lock: a network stall must
// never hold the glass hostage. Capability URL = the secret; https only.
static const size_t kImg1bitLen  = 800UL * 480UL / 8UL;   // 48000
static const size_t kImgGray4Len = 800UL * 480UL / 4UL;   // 96000
extern "C" esp_err_t tiny_display_fetch_raw(const char *url, uint8_t **out, size_t *out_len) {
    if (strncmp(url, "https://", 8) != 0) return ESP_ERR_INVALID_ARG;
    esp_http_client_config_t hc = {};
    hc.url = url;
    hc.timeout_ms = 15000;
    hc.crt_bundle_attach = esp_crt_bundle_attach;
    esp_http_client_handle_t c = esp_http_client_init(&hc);
    if (!c) return ESP_FAIL;
    esp_err_t err = esp_http_client_open(c, 0);
    if (err != ESP_OK) { esp_http_client_cleanup(c); return err; }
    int64_t clen = esp_http_client_fetch_headers(c);
    int status = esp_http_client_get_status_code(c);
    if (status != 200 || (clen != (int64_t)kImg1bitLen &&
                          clen != (int64_t)kImgGray4Len)) {
        ESP_LOGE(TAG, "image fetch refused: status=%d len=%lld (want 48000|96000)",
                 status, (long long)clen);
        esp_http_client_cleanup(c);
        return ESP_ERR_INVALID_SIZE;
    }
    uint8_t *buf = (uint8_t *)heap_caps_malloc((size_t)clen, MALLOC_CAP_SPIRAM);
    if (!buf) { esp_http_client_cleanup(c); return ESP_ERR_NO_MEM; }
    size_t got = 0;
    while (got < (size_t)clen) {
        int r = esp_http_client_read(c, (char *)buf + got, (size_t)clen - got);
        if (r <= 0) break;
        got += (size_t)r;
    }
    esp_http_client_cleanup(c);
    if (got != (size_t)clen) { heap_caps_free(buf); return ESP_ERR_INVALID_RESPONSE; }
    *out = buf; *out_len = got;
    return ESP_OK;
}
// Blit fetched canvas-space bytes onto the panel. Caller holds NO lock; the
// canvas is pinned landscape because raw frames are packed at the panel's
// native origin — rotation belongs to drawn cards, not server bytes.
static void blit_raw_locked(Canvas *c, const uint8_t *buf, size_t len) {
    c->set_rotation(CanvasRotation::Landscape);
    if (len == kImgGray4Len) {
        memcpy((void *)c->data(), buf, len);      // canvas is 2bpp packed
    } else {
        for (int y = 0; y < kH; ++y)
            for (int x = 0; x < kW; ++x) {
                size_t bit = (size_t)y * kW + x;
                bool black = !(buf[bit / 8] & (0x80 >> (bit % 8)));
                c->draw_pixel(x, y, black ? GrayLevel::Black : GrayLevel::White);
            }
    }
}

// ---- photos-on-SD (the 128GB card is the photo home).
// Every image card that PAINTS also lands in /sdcard/photos/NNNNN.raw —
// an append-only archive independent of the 10-slot gallery, so photos
// sent to the glass outlive the relay's hosted URLs. Guards: re-renders
// (rotation/glance replaying the cached spec) and a same-URL resend do
// not duplicate; no card = no archive, the paint is untouched.
static char s_photo_last_url[256];

#define PHOTOS_SD_DIR STICKY_SD_MOUNT_POINT "/photos"

static void photos_sd_save(const char *url, const uint8_t *buf, size_t len) {
    if (s_rerendering) return;                       // replay, not a new photo
    if (strcmp(url, s_photo_last_url) == 0) return;  // same-URL resend
    if (sticky_sdcard_ensure() != ESP_OK) return;
    mkdir(PHOTOS_SD_DIR, 0775);
    // Next sequence number = 1 + highest NNNNN.raw already on the card.
    int seq = 0;
    DIR *d = opendir(PHOTOS_SD_DIR);
    if (d) {
        struct dirent *e;
        while ((e = readdir(d)) != nullptr) {
            int n = 0;
            if (sscanf(e->d_name, "%5d.raw", &n) == 1 && n > seq) seq = n;
        }
        closedir(d);
    }
    ++seq;
    char path[48], tmp[52];
    snprintf(path, sizeof path, PHOTOS_SD_DIR "/%05d.raw", seq);
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    FILE *f = fopen(tmp, "wb");
    if (!f) { ESP_LOGW(TAG, "photos sd: fopen(%s) errno=%d", tmp, errno); return; }
    const size_t put = fwrite(buf, 1, len, f);
    fclose(f);
    if (put != len) { unlink(tmp); return; }
    unlink(path);
    if (rename(tmp, path) != 0) { unlink(tmp); return; }
    strlcpy(s_photo_last_url, url, sizeof s_photo_last_url);
    ESP_LOGI(TAG, "photos sd: archived %05d.raw (%u B)", seq, (unsigned)len);
}

static esp_err_t render_image_card(cJSON *root) {
    Canvas *c = sticky_display_canvas();
    const cJSON *ju = cJSON_GetObjectItem(root, "url");
    if (!c || !cJSON_IsString(ju)) return ESP_ERR_INVALID_ARG;
    uint8_t *buf = nullptr; size_t len = 0;
    esp_err_t err = tiny_display_fetch_raw(ju->valuestring, &buf, &len);  // network: NO lock held
    if (err != ESP_OK) return err;
    photos_sd_save(ju->valuestring, buf, len);  // archive before the canvas eats it
    display_lock();
    blit_raw_locked(c, buf, len);
    heap_caps_free(buf);
    err = (len == kImgGray4Len) ? sticky_display_refresh()
                                : sticky_display_refresh_monochrome();
    s_staged_count = 0;                  // an image has no buttons
    if (err == ESP_OK) publish_regions("image");
    display_unlock();
    return err;
}

// ---- gallery (grammar v10, shared iOS wire contract) ---------------------
// -------------------------------------------------------------------------
// {"type":"gallery","urls":["https://…/a.raw",…],"index":0} — hosted raw
// frames, same 48000|96000B contract as the image card; fetch-on-page.
// The SET persists in PSRAM so `page gallery` and the home top-bar entry
// reopen it (RAM-persistent: survives navigation, honest about reboots).
static constexpr int kGalMax = 10;      // iOS caps at 10; relay cap agrees
EXT_RAM_BSS_ATTR static char s_gal_urls[kGalMax][256];
static int s_gal_count = 0;
static int s_gal_index = 0;

// Reboot-persistence (finishes what 0.23.5 left undone):
// the manifest — urls + count + last page — lives in NVS; the frames stay
// on the media host and re-fetch on demand, same as a cold `page gallery`.
// Lazy, once: first ask after boot pulls the manifest back into PSRAM.
// LRU-2 PSRAM frame cache — offline flips LAND on any photo
// this boot has already shown. Frames are immutable hosted bytes (a URL's
// content never changes), so cached == fresh, no staleness question. Two
// slots × ≤96KB against 8MB PSRAM. DEVIATION from the desk spec's "current
// + one NEIGHBOR": no background prefetch — esp_http in a fresh task is a
// convicted crash, and a synchronous prefetch would tax every flip
// ~1-2s to speed up a hypothetical one. Visited-LRU gives the same promise
// the reviewer named ("at least one flip always lands") once 2 photos have
// been seen, at zero added network. Cache OWNS its buffers.
struct GalCache { int idx; size_t len; uint8_t *buf; uint32_t age; };
static GalCache s_gal_cache[2] = {{-1, 0, nullptr, 0}, {-1, 0, nullptr, 0}};
static uint32_t s_gal_cache_clock = 0;

// ---- gallery-on-SD (the 128GB card is the photo home).
// Layer UNDER the PSRAM LRU, ABOVE the network: frames download ONCE into
// /sdcard/gallery/NNN.raw and every later flip is a disk read (~100ms) not
// a TLS fetch (~1-2s). manifest.json on the card names the set the frames
// belong to; a NEW set rewrites it and unlinks stale frames (an index remap
// makes cached frames lie — same rule as gal_cache_clear). No card = the
// exact pre-SD behavior: NVS manifest + fetch-on-flip. All writes happen on
// the caller's task (tiny_node loop, 16KB stack) — buffers are PSRAM, the
// stack only carries paths.
#define GAL_SD_DIR STICKY_SD_MOUNT_POINT "/gallery"

static bool gal_sd_ok(void) {
    return sticky_sdcard_ensure() == ESP_OK;
}

static void gal_sd_frame_path(char *out, size_t cap, int idx) {
    snprintf(out, cap, GAL_SD_DIR "/%03d.raw", idx);
}

// Read frame idx from the card. Returns a PSRAM buffer the CALLER owns
// (hand it to gal_cache_put like a fetched one), or nullptr on miss.
static uint8_t *gal_sd_read(int idx, size_t *out_len) {
    if (!gal_sd_ok()) return nullptr;
    char path[48];
    gal_sd_frame_path(path, sizeof path, idx);
    struct stat st;
    if (stat(path, &st) != 0) return nullptr;
    const size_t len = (size_t)st.st_size;
    if (len != kImg1bitLen && len != kImgGray4Len) {
        unlink(path);  // truncated by a yank mid-write: not a frame, remove
        return nullptr;
    }
    FILE *f = fopen(path, "rb");
    if (!f) return nullptr;
    uint8_t *buf = (uint8_t *)heap_caps_malloc(len, MALLOC_CAP_SPIRAM);
    if (!buf) { fclose(f); return nullptr; }
    const size_t got = fread(buf, 1, len, f);
    fclose(f);
    if (got != len) { heap_caps_free(buf); return nullptr; }
    ESP_LOGI(TAG, "gallery sd: frame %d served from card (%u B, no fetch)",
             idx, (unsigned)len);
    *out_len = len;
    return buf;
}

// Persist a fetched frame. Write to .tmp then rename: a yank mid-write
// leaves no half-frame under a real name. Failure is silent-but-logged —
// the RAM path already has the bytes, the card is an optimization.
static void gal_sd_write(int idx, const uint8_t *buf, size_t len) {
    if (!gal_sd_ok()) return;
    mkdir(GAL_SD_DIR, 0775);
    char path[48], tmp[52];
    gal_sd_frame_path(path, sizeof path, idx);
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    FILE *f = fopen(tmp, "wb");
    if (!f) { ESP_LOGW(TAG, "gallery sd: fopen(%s) failed errno=%d", tmp, errno); return; }
    const size_t put = fwrite(buf, 1, len, f);
    fclose(f);
    if (put != len) { unlink(tmp); return; }
    unlink(path);            // FATFS rename refuses to overwrite
    if (rename(tmp, path) != 0) unlink(tmp);
    else ESP_LOGI(TAG, "gallery sd: frame %d cached (%u B)", idx, (unsigned)len);
}

// New set: stale frames lie. Drop every NNN.raw + manifest, rewrite the
// manifest from the live set so the card names what it now holds.
static void gal_sd_reset(const char (*urls)[256], int count) {
    if (!gal_sd_ok()) return;
    mkdir(GAL_SD_DIR, 0775);
    for (int i = 0; i < kGalMax; ++i) {
        char p[52];
        gal_sd_frame_path(p, sizeof p, i);
        unlink(p);
        snprintf(p, sizeof p, GAL_SD_DIR "/%03d.raw.tmp", i);
        unlink(p);
    }
    cJSON *root = cJSON_CreateObject();
    cJSON *ja = cJSON_AddArrayToObject(root, "urls");
    for (int i = 0; i < count; ++i)
        cJSON_AddItemToArray(ja, cJSON_CreateString(urls[i]));
    char *js = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!js) return;
    FILE *f = fopen(GAL_SD_DIR "/manifest.json", "w");
    if (f) { fputs(js, f); fclose(f); }
    free(js);
}

static uint8_t *gal_cache_get(int idx, size_t *len) {
    for (auto &e : s_gal_cache)
        if (e.buf && e.idx == idx) {
            e.age = ++s_gal_cache_clock;
            *len = e.len;
            return e.buf;
        }
    return nullptr;
}

// Takes ownership of buf (caller must NOT free), evicting the oldest slot.
static void gal_cache_put(int idx, uint8_t *buf, size_t len) {
    GalCache *v = nullptr;
    for (auto &e : s_gal_cache)          // same photo refetched: replace it
        if (e.buf && e.idx == idx) v = &e;
    if (!v)
        for (auto &e : s_gal_cache)      // else an empty slot
            if (!e.buf) { v = &e; break; }
    if (!v) {                            // else evict the least recent
        v = &s_gal_cache[0];
        for (auto &e : s_gal_cache)
            if (e.age < v->age) v = &e;
    }
    if (v->buf && v->buf != buf) heap_caps_free(v->buf);
    v->idx = idx; v->buf = buf; v->len = len; v->age = ++s_gal_cache_clock;
}

// A NEW SET remaps every index — cached frames would show the wrong photo
// under the right chip, which is worse than a dead tap (the review's own words
// about wrong-photo galleries).
static void gal_cache_clear(void) {
    for (auto &e : s_gal_cache) {
        if (e.buf) heap_caps_free(e.buf);
        e.buf = nullptr; e.idx = -1; e.len = 0; e.age = 0;
    }
}

static bool s_gal_nvs_tried = false;
static void gal_lazy_load(void) {
    if (s_gal_nvs_tried || s_gal_count > 0) return;
    s_gal_nvs_tried = true;
    uint8_t n = 0, idx = 0;
    if (tiny_config_gallery_load(s_gal_urls, sizeof s_gal_urls,
                                 &n, &idx) != ESP_OK) return;
    if (n > kGalMax) n = kGalMax;
    // Slot geometry check the config layer cannot do: every occupied slot
    // must be NUL-terminated https — one bad slot refuses the whole set
    // (a gallery that opens the wrong photo is worse than an empty one).
    for (int i = 0; i < n; ++i) {
        s_gal_urls[i][sizeof s_gal_urls[0] - 1] = 0;
        if (strncmp(s_gal_urls[i], "https://", 8) != 0) return;
    }
    s_gal_count = n;
    s_gal_index = idx;
    ESP_LOGI(TAG, "gallery manifest restored from NVS: %d photos, page %d",
             n, idx + 1);
}

extern "C" int tiny_display_gallery_count(void) {
    gal_lazy_load();
    return s_gal_count;
}

// dir: -1/+1 page (wraps), 0 = reopen current. Fetches OUTSIDE the display
// lock like every other network path. Empty set → INVALID_STATE (callers
// show the friendly card; this layer never invents one).
extern "C" esp_err_t tiny_display_gallery_nav(int dir) {
    gal_lazy_load();
    if (s_gal_count <= 0) return ESP_ERR_INVALID_STATE;
    int idx = (s_gal_index + dir) % s_gal_count;
    if (idx < 0) idx += s_gal_count;
    Canvas *c = sticky_display_canvas();
    if (!c) return ESP_ERR_INVALID_STATE;
    uint8_t *buf = nullptr; size_t len = 0;
    esp_err_t err = ESP_OK;
    // Read order: PSRAM LRU (this boot) → SD card (any boot) → network.
    // A frame from SD or network enters the LRU so the next flip back is
    // a memcpy; a frame from the network also lands on the card so the
    // next BOOT never fetches it again.
    if ((buf = gal_cache_get(idx, &len)) != nullptr) {
        // LRU hit — buf stays cache-owned, nothing to do.
    } else if ((buf = gal_sd_read(idx, &len)) != nullptr) {
        gal_cache_put(idx, buf, len);  // cache takes ownership
    } else {
        err = tiny_display_fetch_raw(s_gal_urls[idx], &buf, &len);
        if (err != ESP_OK) return err;  // no RAM, no disk, no net: honest miss
        gal_sd_write(idx, buf, len);   // download-once (before cache owns it)
        gal_cache_put(idx, buf, len);  // cache takes ownership
    }
    // Page position survives too: a u8 write per flip (no blob churn), so
    // a reboot reopens the photo the owner was looking at, not photo 1.
    if (idx != s_gal_index) tiny_config_gallery_save_index((uint8_t)idx);
    s_gal_index = idx;
    display_lock();
    blit_raw_locked(c, buf, len);  // cache owns buf — no free here
    {   // page indicator chip, bottom-right: white ground so it reads on any
        // photo; tells the truth about position ("2/7"), per the contract.
        char pg[16];
        snprintf(pg, sizeof pg, "%d/%d", s_gal_index + 1, s_gal_count);
        const int tw = text_width(pg, 2);
        const int chip_w = tw + 24, chip_h = 40;
        const int cx = kW - 16 - chip_w, cy = kH - 16 - chip_h;
        fill_round_rect(*c, cx, cy, chip_w, chip_h, 10, GrayLevel::White);
        draw_round_rect(*c, cx, cy, chip_w, chip_h, 10, GrayLevel::Black);
        c->draw_text(cx + 12, cy + (chip_h - text_ink_h(2)) / 2, pg, 2,
                     GrayLevel::Black);
    }
    // Regions (panel space): home 88x88 top-left; prev/next = 120px edge
    // bands below it. Ids ride the touch router's "g:" namespace; labels
    // stay inert under the defuse rule.
    s_staged_count = 0;
    stage_region(0, 0, 88, 88, "home", "home");
    stage_region(0, 100, 120, kH - 100, "prev", "g:prev");
    stage_region(kW - 120, 100, 120, kH - 100, "next", "g:next");
    strlcpy(s_card_id, "gallery", sizeof s_card_id);
    strlcpy(s_card_type, "gallery", sizeof s_card_type);
    err = (len == kImgGray4Len) ? sticky_display_refresh()
                                : sticky_display_refresh_monochrome();
    if (err == ESP_OK) publish_regions("gallery");
    display_unlock();
    return err;
}

static esp_err_t render_gallery_card(cJSON *root) {
    const cJSON *ja = cJSON_GetObjectItem(root, "urls");
    if (!cJSON_IsArray(ja)) return ESP_ERR_INVALID_ARG;
    int n = 0;
    const cJSON *u = nullptr;
    cJSON_ArrayForEach(u, ja) {
        if (n >= kGalMax) break;
        if (!cJSON_IsString(u) ||
            strncmp(u->valuestring, "https://", 8) != 0) continue;
        if (strlen(u->valuestring) >= sizeof s_gal_urls[0]) continue;
        strlcpy(s_gal_urls[n++], u->valuestring, sizeof s_gal_urls[0]);
    }
    if (n == 0) return ESP_ERR_INVALID_ARG;
    // A RE-LAYOUT (rotation/glance replaying the cached spec) must not
    // reset the page: the spec's "index" describes where the set STARTED,
    // not where the owner is now. Keep the live page and skip the NVS
    // save — replaying index:0 here is exactly how a rotation flap both
    // lost the owner's page and poisoned the saved one (0.25.9 field bug).
    if (s_rerendering && n == s_gal_count) {
        s_gal_index = (s_gal_index < n) ? s_gal_index : 0;
        return tiny_display_gallery_nav(0);
    }
    gal_cache_clear();  // new set = new index space, stale frames lie
    gal_sd_reset(s_gal_urls, n);  // card mirrors the rule: stale NNN.raw lie too
    s_gal_count = n;
    const cJSON *ji = cJSON_GetObjectItem(root, "index");
    int idx = cJSON_IsNumber(ji) ? (int)ji->valuedouble : 0;
    if (idx < 0 || idx >= n) idx = 0;
    s_gal_index = idx;
    // Commit the manifest before the first fetch: a set that painted once
    // is a set that survives reboot. Only occupied slots
    // travel; a save failure is logged, never fatal — the RAM set works.
    esp_err_t perr = tiny_config_gallery_save(
        s_gal_urls, (size_t)n * sizeof s_gal_urls[0], (uint8_t)n, (uint8_t)idx);
    if (perr != ESP_OK)
        ESP_LOGW(TAG, "gallery manifest NVS save failed: %s (set is RAM-only "
                      "this boot)", esp_err_to_name(perr));
    return tiny_display_gallery_nav(0);
}

extern "C" esp_err_t tiny_display_render_card(const char *card_json) {
    if (!card_json) return ESP_ERR_INVALID_ARG;
    Canvas *c = sticky_display_canvas();
    if (!c) return ESP_ERR_INVALID_STATE;

    // A relay envelope (or any other card) outranks the typer: an active
    // stream ends silently rather than fighting the new card for the glass.
    if (s_stream_on && !s_stream_rendering) {
        ESP_LOGW(TAG, "render during active stream — stream ended by new card");
        stream_reset();
    }

    cJSON *root = cJSON_Parse(card_json);
    if (!root) {
        ESP_LOGE(TAG, "card json parse failed near: %.32s",
                 cJSON_GetErrorPtr() ? cJSON_GetErrorPtr() : "?");
        return ESP_ERR_INVALID_ARG;
    }

    // Keep the spec that produced what is on the glass, so a rotation (or any
    // other re-layout) can redraw it without asking the network again. Skipped
    // when we ARE the re-render, or we would free the string we are reading.
    if (!s_rerendering) {
        const size_t n = strlen(card_json) + 1;
        char *copy = (char *)heap_caps_malloc(n, MALLOC_CAP_SPIRAM);
        if (!copy) copy = (char *)malloc(n);
        if (copy) {
            memcpy(copy, card_json, n);
            char *old_copy = s_card_cache;
            s_card_cache = copy;
            free(old_copy);
        } else {
            ESP_LOGW(TAG, "could not cache %u B card - rotation will not redraw"
                          " this one", (unsigned)n);
        }
    }

    // The image card takes its own exit — raw canvas-space bytes from
    // the capability URL, no text pipeline. Cached above like any card, so a
    // rotation/rerender refetches honestly (the URL is content-addressed;
    // same sha16 = same pixels).
    {
        const cJSON *jt = cJSON_GetObjectItem(root, "type");
        if (cJSON_IsString(jt) && strcmp(jt->valuestring, "universe") == 0) {
            // v11: the universe page owns its own layout (roster lives in
            // tiny_agent, not in the spec) — merge any pushed slugs, then
            // the shell renders a normal composite through this function.
            cJSON_Delete(root);
            return tiny_shell_universe_push(card_json);
        }
        if (cJSON_IsString(jt) && strcmp(jt->valuestring, "gallery") == 0) {
            esp_err_t gerr = render_gallery_card(root);
            cJSON_Delete(root);
            return gerr;
        }
        if (cJSON_IsString(jt) && strcmp(jt->valuestring, "image") == 0) {
            const cJSON *jc = cJSON_GetObjectItem(root, "card_id");
            strlcpy(s_card_id, cJSON_IsString(jc) ? jc->valuestring : "image",
                    sizeof s_card_id);
            strlcpy(s_card_type, "image", sizeof s_card_type);
            esp_err_t ierr = render_image_card(root);
            cJSON_Delete(root);
            return ierr;
        }
    }

    cJSON *type = cJSON_GetObjectItem(root, "type");
    cJSON *title = cJSON_GetObjectItem(root, "title");
    cJSON *cid = cJSON_GetObjectItem(root, "card_id");
    const char *type_s = cJSON_IsString(type) ? type->valuestring : "text";
    const char *title_s = cJSON_IsString(title) ? title->valuestring : "";
    const char *cid_s = cJSON_IsString(cid) ? cid->valuestring : "";
    // FINDING 2 (check 23): an empty card_id committed an ANONYMOUS card — the
    // mirror couldn't assert what was on glass and ui_tap reported card_id="".
    // Never an empty identity: synthesize a stable one from the card's own
    // bytes (FNV-1a over type+title), so the same card gets the same id and
    // the dashboard can still correlate.
    char cid_auto[16];
    if (!cid_s[0]) {
        uint32_t h = 2166136261u;
        for (const char *s = type_s; *s; ++s) h = (h ^ (uint8_t)*s) * 16777619u;
        for (const char *s = title_s; *s; ++s) h = (h ^ (uint8_t)*s) * 16777619u;
        snprintf(cid_auto, sizeof cid_auto, "auto-%08lx", (unsigned long)h);
        cid_s = cid_auto;
        ESP_LOGW(TAG, "card had no card_id - synthesized %s", cid_s);
    }

    // "priority" (T1, reserved ahead of the notification model): "alert" or
    // "normal" (default). Parsed NOW so senders can start declaring it and no
    // future firmware breaks them; today's whole behavior is a "! " title
    // prefix — visible, honest, and nothing a normal card could mistake for
    // its own. The focus/queue semantics arrive with the notification model.
    cJSON *prio = cJSON_GetObjectItem(root, "priority");
    const bool is_alert =
        cJSON_IsString(prio) && strcmp(prio->valuestring, "alert") == 0;
    char titled[160];
    if (is_alert) {
        snprintf(titled, sizeof titled, "! %s", title_s);
        title_s = titled;
    }

    // One writer at a time: the relay task, the voice_ask task and the serial
    // card console all land here, and canvas+panel are a single resource.
    display_lock();

    // Rotation is applied here, once per render: the layout helpers below all
    // read s_w/s_h, and the canvas turns every pixel they draw.
    c->set_rotation(s_rot);
    s_w = c->width();
    s_h = c->height();

    c->clear(GrayLevel::White);
    int y = draw_title_bar(*c, title_s);
    int max_y = s_h - kMargin;

    cJSON *buttons = cJSON_GetObjectItem(root, "buttons");
    draw_buttons(*c, buttons, &max_y);

    cJSON *footer = cJSON_GetObjectItem(root, "footer");
    if (cJSON_IsString(footer)) {
        max_y -= kGlyphH + 10;
        char ff[160]; ascii_fold(ff, sizeof ff, footer->valuestring);
        c->draw_text(kMargin, max_y + 4, ff, kSmallScale,
                     GrayLevel::Black);
    }

    // ---- scroll-aware body render ----
    // Scrollable types render their FULL content shifted up by s_scroll, with
    // the canvas clip guarding the body box. The returned end-y measures real
    // content height; overflow earns a thin right-edge scrollbar.
    const bool scrollable = strcmp(type_s, "text") == 0 ||
                            strcmp(type_s, "list") == 0 ||
                            strcmp(type_s, "kv") == 0 ||
                            strcmp(type_s, "menu") == 0 ||
                            strcmp(type_s, "chat") == 0 ||
                            strcmp(type_s, "composite") == 0;
    if (scrollable) {
        if (strcmp(s_scroll_card, cid_s) != 0) {   // new card starts at the top
            s_scroll = 0;
            strlcpy(s_scroll_card, cid_s, sizeof s_scroll_card);
        }
        s_body_top = y;
        s_body_bot = max_y;
        s_view_top = y;
        s_view_bot = max_y;
        c->set_clip_y(y, max_y);
        const int start = y - s_scroll;
        const int end = render_body(*c, root, type_s, start, 1 << 28);
        c->clear_clip_y();
        s_content_h = end - start;
        const int view_h = max_y - y;
        if (s_content_h > view_h) {
            // Track + thumb at the right edge, inside the margin.
            const int tx = s_w - 12, tw = 5;
            c->fill_rect(tx, y, tw, view_h, GrayLevel::LightGray);
            int th = std::max(24, view_h * view_h / s_content_h);
            th = std::min(th, view_h);
            const int span = s_content_h - view_h;
            const int ty = y + (span > 0 ? (view_h - th) * s_scroll / span : 0);
            c->fill_rect(tx, ty, tw, th, GrayLevel::Black);
        }
    } else {
        s_view_top = 0;
        s_view_bot = 1 << 28;
        s_content_h = 0;   // "fits" — can_scroll() says no
        s_body_top = y;
        s_body_bot = max_y;
        render_body(*c, root, type_s, y, max_y);
    }

    // Typer caret: a block cursor where the next words will land,
    // drawn ONLY while the stream may still deliver bytes — a caret on a
    // finished answer is a lie about liveness. Logical tail y = body start
    // minus scroll plus measured content; clamped into the body box.
    if (s_stream_caret) {
        int tail_y = (y - s_scroll) + s_content_h + 4;
        const int ch = kGlyphH * kBodyScale;
        if (tail_y > max_y - ch) tail_y = max_y - ch;
        if (tail_y < y) tail_y = y;
        c->fill_rect(kMargin, tail_y, 10, ch, GrayLevel::DarkGray);
    }

    // Typing feel: a keyboard re-rendering ITSELF (same card_id, e.g.
    // one more char in the value line) refreshes partial (~300-500ms), not the
    // full 1-2s mono wipe. First render of the keyboard is still full. A
    // scroll step takes the same fast path — paging a body is typing-
    // cadence, not new-card cadence.
    strlcpy(s_card_type, type_s, sizeof s_card_type);
    const bool kb_rerender = strcmp(type_s, "keyboard") == 0 &&
                             strcmp(s_card_id, cid_s) == 0 && cid_s[0];
    // FINDING 3 (check 23): partial refresh is a COMPARISON against the last
    // pushed frame. After a rotation that baseline is a differently-oriented
    // picture, and "unchanged" pixels survive as a collage of the old layout
    // (seen on glass: home buttons + rotated splash text around a new card).
    // Rotation change forces the full wipe, whatever fast path was earned.
    // -1 = "nothing pushed yet", which is why this is an int and not a
    // CanvasRotation: the enum is a uint8_t with no spare value for "unknown",
    // and the first frame after boot must count as a rotation change. Compared
    // through an explicit cast because enum class does not convert implicitly —
    // HEAD did not compile without it.
    static int s_pushed_rot = -1;
    // §12 wake: baseline capture pass — draw into the canvas, push nothing.
    // Regions are NOT published (nothing new is on the glass).
    if (s_draw_only) { display_unlock(); return ESP_OK; }
    const bool rot_changed = (s_pushed_rot != static_cast<int>(s_rot));
    esp_err_t err;
    if (s_wake_partial_pending && s_wake_baseline) {
        // §12 wake paint: diff-partial against the redrawn goodbye card —
        // the ONE commit where "nothing pushed yet" (s_pushed_rot == -1)
        // does not force a full flash, because the previous plane is
        // supplied explicitly instead of trusted from controller RAM.
        err = sticky_display_refresh_partial_baseline(s_wake_baseline);
        s_wake_partial_pending = false;
        heap_caps_free(s_wake_baseline);
        s_wake_baseline = nullptr;
    } else {
        err = (!rot_changed && (kb_rerender || s_scroll_partial ||
                                s_stream_partial || s_glance_partial))
                  ? sticky_display_refresh_partial()
                  : sticky_display_refresh_monochrome();
    }
    if (err == ESP_OK) s_pushed_rot = static_cast<int>(s_rot);
    // Buttons become touchable ONLY once the human can see them. Publishing at
    // draw time made taps in the ~2s refresh window resolve against a layout
    // that was not on the glass yet.
    if (err == ESP_OK) {
        publish_regions(cid_s);
    } else {
        ESP_LOGW(TAG, "refresh failed (%s) — keeping previous tap regions",
                 esp_err_to_name(err));
    }
    display_unlock();

    ESP_LOGI(TAG, "card rendered type=%s title=\"%s\" id=\"%s\" buttons=%d -> %s",
             type_s, title_s, cid_s, s_staged_count, esp_err_to_name(err));
    cJSON_Delete(root);  // AFTER the log: type_s/title_s point into root
    return err;
}

// Splash (owner's spec, verbatim: "only tiny and loading
// bar ... rest can be clear"): the word and ONE bar. Version lives in
// Settings and `status` — the glass owes the owner exactly what he asked for.
// Bar geometry shared with the progress API below.
static constexpr int kSplashBarX = 280, kSplashBarY = 260;
static constexpr int kSplashBarW = 240, kSplashBarH = 12;

extern "C" esp_err_t tiny_display_splash(const char *version, const char *state) {
    // Signature kept for the callers; the strings go to the LOG, not the
    // glass — the splash spec cleared them, and a boot log is where fw/state
    // prose belongs anyway.
    ESP_LOGI(TAG, "splash: fw %s, %s", version ? version : "?",
             state ? state : "-");
    Canvas *c = sticky_display_canvas();
    if (!c) return ESP_ERR_INVALID_STATE;
    display_lock();
    c->clear(GrayLevel::White);
    c->draw_text(280, 190, "tiny", 10, GrayLevel::Black);
    // ONE hollow bar at a fixed 25%. It used to advance by partial refresh
    // at boot stages; those partials were the prime suspect in a boot-bounce
    // and were removed in 0.25.36 — the bootmark flight recorder took over.
    c->draw_rect(kSplashBarX, kSplashBarY, kSplashBarW, kSplashBarH,
                 GrayLevel::Black);
    c->fill_rect(kSplashBarX + 2, kSplashBarY + 2,
                 (kSplashBarW - 4) * 25 / 100, kSplashBarH - 4,
                 GrayLevel::Black);  // 25%: the display is up (you can see this)
    esp_err_t err = sticky_display_refresh_monochrome();
    // The splash has no buttons: retire the previous card's tap regions, or a
    // tap on a blank corner would still fire the last card's action.
    s_staged_count = 0;
    if (err == ESP_OK) publish_regions("splash");
    display_unlock();
    return err;
}

extern "C" esp_err_t tiny_display_blit_1bit(const uint8_t *buf, size_t len,
                                            bool partial) {
    // Partial honored — a stream frame diffs against the previous frame
    // already in controller RAM (same power state, valid baseline).
    Canvas *c = sticky_display_canvas();
    if (!c || !buf) return ESP_ERR_INVALID_ARG;
    const size_t expected = (size_t)kW * kH / 8;
    if (len < expected) return ESP_ERR_INVALID_SIZE;
    display_lock();
    // Server bytes are canvas-space at the panel's native landscape origin —
    // same law as the image card. Without this, a portrait canvas (IMU
    // auto-rotate on a pocket device) remaps/clips every draw_pixel and the
    // "frame" arrives as white glass: take 2's white-paint bug, convicted.
    c->set_rotation(CanvasRotation::Landscape);
    for (int y = 0; y < kH; ++y) {
        for (int x = 0; x < kW; ++x) {
            size_t bit = (size_t)y * kW + x;
            bool black = !(buf[bit / 8] & (0x80 >> (bit % 8)));
            c->draw_pixel(x, y, black ? GrayLevel::Black : GrayLevel::White);
        }
    }
    esp_err_t err = partial ? sticky_display_refresh_partial()
                            : sticky_display_refresh_monochrome();
    s_staged_count = 0;  // a raw blit has no known buttons
    if (err == ESP_OK) publish_regions("blit");
    display_unlock();
    return err;
}

extern "C" esp_err_t tiny_display_snapshot_gray8(uint8_t **out, size_t *out_len) {
    Canvas *c = sticky_display_canvas();
    if (!c || !out || !out_len) return ESP_ERR_INVALID_ARG;
    const size_t len = (size_t)kW * kH;
    uint8_t *buf = (uint8_t *)heap_caps_malloc(len, MALLOC_CAP_SPIRAM);
    if (!buf) buf = (uint8_t *)malloc(len);
    if (!buf) return ESP_ERR_NO_MEM;
    // Canvas stores 2 bits/pixel packed; expand via its data() + stride.
    const uint8_t *src = c->data();
    const size_t stride = c->stride();
    static const uint8_t lut[4] = {0x00, 0x55, 0xAA, 0xFF};
    display_lock();  // a screenshot taken mid-render would be half a card
    for (int y = 0; y < kH; ++y) {
        for (int x = 0; x < kW; ++x) {
            uint8_t byte = src[y * stride + x / 4];
            uint8_t px = (byte >> (6 - 2 * (x % 4))) & 0x3;
            buf[(size_t)y * kW + x] = lut[px];
        }
    }
    display_unlock();
    *out = buf;
    *out_len = len;
    return ESP_OK;
}

// ---- scroll -------------------------------------------------------------
// The offset lives here, next to the card cache: a scroll step is "re-render
// the cached card with a different window", so it inherits rotation, region
// republish and the honesty rules without any new drawing code.

extern "C" bool tiny_display_can_scroll(void) {
    return s_card_cache && s_content_h > (s_body_bot - s_body_top);
}

extern "C" void tiny_display_scroll_state(int *offset, int *content_h,
                                          int *view_h) {
    if (offset) *offset = s_scroll;
    if (content_h) *content_h = s_content_h;
    if (view_h) *view_h = s_body_bot - s_body_top;
}

extern "C" esp_err_t tiny_display_scroll(int delta_px) {
    if (!s_card_cache) return ESP_ERR_INVALID_STATE;
    const int view_h = s_body_bot - s_body_top;
    const int max_scroll = s_content_h - view_h;
    if (max_scroll <= 0) return ESP_ERR_INVALID_STATE;  // content fits
    int ns = s_scroll + delta_px;
    if (ns < 0) ns = 0;
    if (ns > max_scroll) ns = max_scroll;
    if (ns == s_scroll) return ESP_OK;   // already at the edge — no flash spent
    s_scroll = ns;
    s_scroll_partial = true;             // paging cadence -> partial refresh
    const esp_err_t err = tiny_display_rerender();
    s_scroll_partial = false;
    ESP_LOGI(TAG, "scroll -> %d/%d px (view %d) %s", s_scroll, max_scroll,
             view_h, esp_err_to_name(err));
    return err;
}

extern "C" esp_err_t tiny_display_scroll_page(int dir) {
    if (!tiny_display_can_scroll()) return ESP_ERR_INVALID_STATE;
    // 2/3 of the view per step keeps a reading anchor across the flip.
    const int step = std::max(48, (s_body_bot - s_body_top) * 2 / 3);
    return tiny_display_scroll(dir < 0 ? -step : step);
}

// Outcome of the LAST gesture the router classified — the lesson: three
// competent readers of "route ESP_OK + nothing visibly moved" reached three
// different convictions (axis bug / stream latch / dead rail) because the
// receipt never said WHAT the gesture did. One task (act) writes this; the
// receipt rail reads it after the route-seq bump, so no lock is needed.
static char s_gesture[96] = "none";
static void set_gesture(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(s_gesture, sizeof s_gesture, fmt, ap);
    va_end(ap);
}
extern "C" const char *tiny_display_last_gesture(void) { return s_gesture; }

extern "C" esp_err_t tiny_display_scroll_gesture(int px0, int py0,
                                                 int px1, int py1) {
    Canvas *c = sticky_display_canvas();
    if (!c) { set_gesture("refused: no canvas"); return ESP_ERR_INVALID_STATE; }
    // D-UX1: this function is the GESTURE ROUTER (UX_SPEC §2) and it owns ALL
    // gesture feedback — the caller must not beep, or every accept would sound
    // twice. Vocabulary (§0): single blip = accepted, double = recognized but
    // refused, silence = below thresholds (not a gesture).
    //
    // Streaming answer: the stream owns the display.
    // Every gesture is acknowledged with one blip and DISCARDED — deferring is
    // predictable because a stream is bounded; the one exit is the physical
    // long-press-AI abort, which must work through fabric.
    if (s_stream_on) {
        set_gesture("discarded: stream owns the display");
        sticky_buzzer_beep();
        return ESP_OK;
    }
    // Touch reports PANEL coordinates; the picture may be rotated. Map both
    // endpoints to logical space so "finger moved toward the title" means
    // "scroll up" whichever way the body is turned.
    int lx0, ly0, lx1, ly1;
    c->to_logical(px0, py0, lx0, ly0);
    c->to_logical(px1, py1, lx1, ly1);
    const int dx = lx1 - lx0, dy = ly1 - ly0;
    const bool kb = strcmp(s_card_type, "keyboard") == 0;
    // Edge bands (§1: travel >=120 dominant axis, >=2x dominance; bands are
    // LAYOUT-space: last 56 rows / first 48 columns of the current rotation).
    // Edge-origin marks NAV intent — one spatial rule, no modes. The accept
    // blip fires BEFORE the render: on e-ink the ack must be FELT before it
    // can be seen (§0), and home/back block seconds on the panel.
    if (ly0 >= (int)c->height() - 56 && -dy >= 120 && -dy >= 2 * std::abs(dx)) {
        sticky_buzzer_beep();
        // Keyboard: bottom-band-up = CANCEL, don't save (§2) — routed through
        // the key handler so each composer purpose keeps its own exit path
        // (wifi -> list, compose -> thread, ask -> home).
        if (kb) { set_gesture("kb-cancel (bottom-band up)"); return tiny_shell_key("k:cancel"); }
        set_gesture("home (bottom-band up)");
        return tiny_shell_home();
    }
    if (lx0 < 48 && dx >= 120 && dx >= 2 * std::abs(dy)) {
        if (kb) {
            set_gesture("back refused: draft open (keyboard)");
            sticky_buzzer_beep_double();
            return ESP_ERR_NOT_ALLOWED;
        }
        sticky_buzzer_beep();
        set_gesture("back (left-band right)");
        return tiny_shell_nav_back();  // pushed card pops; ring root goes prev
    }
    if (std::abs(dy) <= std::abs(dx)) {
        // Horizontal: page-swipe (stock-UX gesture). Not scroll's call
        // to RENDER — but it is scroll's call to CLASSIFY, since only logical
        // space knows which axis is which after rotation. >=80px so a sloppy
        // diagonal scroll doesn't flip the page; swipe left = next (content
        // moves left, like every phone), right = prev.
        if (std::abs(dx) < 80) {
            set_gesture("ignored: horizontal travel under 80px (twitch/diagonal)");
            return ESP_ERR_NOT_SUPPORTED;  // twitch/diagonal
        }
        if (kb) {
            set_gesture("silenced: draft open (no page-flip over keyboard)");
            return ESP_ERR_NOT_SUPPORTED;  // a draft outranks a gesture: no
                                               // page-flip over the composer
                                               // (§2: keyboard mid-content =
                                               // silence, not dead-end)
        }
        // §2 pushed-card row (the cell that once crashed the device): a
        // pushed card is a PLACE you
        // drilled to, not a ring position. Mid-content → POPS to the parent —
        // phone muscle memory, and a one-thumb target the 48px left band
        // can't be while walking. The pop path has no radio bring-up,
        // so the P0 crash family stays unreachable. Mid-content ← stays the
        // ANNOUNCED dead-end (double blip): "forward" from a drilled place
        // has nowhere to go, and saying so beats pretending.
        if (!tiny_shell_at_root()) {
            if (dx > 0) {
                set_gesture("back (pop from pushed card)");
                sticky_buzzer_beep();
                return tiny_shell_nav_back();
            }
            set_gesture("dead-end: no forward from a pushed card");
            sticky_buzzer_beep_double();
            return ESP_ERR_NOT_ALLOWED;
        }
        set_gesture("page %s (ring)", dx < 0 ? "next" : "prev");
        const esp_err_t r = tiny_shell_cycle(dx < 0 ? +1 : -1);
        if (r == ESP_OK) sticky_buzzer_beep();  // original timing kept: sound on turn
        return r;
    }
    if (std::abs(dy) < 40) {
        set_gesture("ignored: vertical travel under 40px (twitch)");
        return ESP_ERR_NOT_SUPPORTED;                                // twitch
    }
    // Natural scrolling: finger up drags the content up -> offset grows.
    int ob = 0, ch = 0, vh = 0;
    tiny_display_scroll_state(&ob, &ch, &vh);
    const esp_err_t r = tiny_display_scroll(-dy);
    int oa = ob;
    tiny_display_scroll_state(&oa, &ch, &vh);
    const int mx = ch - vh > 0 ? ch - vh : 0;
    // Name the outcome: a clamp and a movement both return ESP_OK,
    // and on e-ink a clamp is INVISIBLE — the receipt must tell them apart.
    if (r == ESP_ERR_INVALID_STATE)
        set_gesture("scroll refused: content fits the view");
    else if (oa == ob)
        set_gesture("scroll clamped at %s (offset %d of %d)",
                    ob <= 0 ? "top" : "bottom", ob, mx);
    else
        set_gesture("scrolled %d -> %d of %d px", ob, oa, mx);
    if (r == ESP_OK) sticky_buzzer_beep();  // shipped semantics unchanged
    return r;
}

// ---- streaming text API ---------------------------
// The stream renders through the ONE normal card funnel: a synthesized
// {"type":"text"} spec + the s_stream_caret flag. No forked drawing code —
// fold, wrap, scroll, rotation and the refresh gate are all inherited.

// Build the stream card spec (cJSON so the buffered text is escaped — an
// answer containing a quote must not break its own card) and render it.
// `caret` also selects the refresh path: caret on = partial, off = full.
static esp_err_t stream_render(bool caret, bool follow_tail) {
    cJSON *card = cJSON_CreateObject();
    if (!card) return ESP_ERR_NO_MEM;
    // Stream-on-home: when the stream targets the home
    // card, the frame IS the agent-home surface — chrome intact, the
    // conversation in the canvas, the renderer's own caret at the text tail
    // (the generic caret below is suppressed: its y math is for scrollable
    // bodies, and home is fixed geometry).
    if (strcmp(s_stream_cid, "home") == 0) {
        cJSON_AddStringToObject(card, "type", "agent_home");
        cJSON_AddStringToObject(card, "card_id", "home");
        if (s_stream_q[0]) cJSON_AddStringToObject(card, "q", s_stream_q);
        const size_t hsafe = s_stream_buf ? utf8_complete_len(s_stream_buf, s_stream_len) : 0;
        char hheld = 0;
        if (s_stream_buf) { hheld = s_stream_buf[hsafe]; s_stream_buf[hsafe] = 0; }
        cJSON_AddStringToObject(card, "stream_body", s_stream_buf ? s_stream_buf : "");
        if (s_stream_buf) s_stream_buf[hsafe] = hheld;
        if (caret) cJSON_AddTrueToObject(card, "caret");
        if (s_stream_note[0]) cJSON_AddStringToObject(card, "note", s_stream_note);
        if (!caret && s_stream_overflow)
            cJSON_AddStringToObject(card, "note", "answer exceeded 8KB - tail dropped");
        char *hcs = cJSON_PrintUnformatted(card);
        cJSON_Delete(card);
        if (!hcs) return ESP_ERR_NO_MEM;
        s_stream_caret = false;        // renderer draws its own tail caret
        s_stream_partial = caret;      // mid-stream = partial; final = full
        s_stream_rendering = true;
        const esp_err_t herr = tiny_display_render_card(hcs);
        s_stream_rendering = false;
        s_stream_partial = false;
        free(hcs);
        return herr;
    }
    const bool chat = s_stream_q[0] != 0;
    cJSON_AddStringToObject(card, "type", chat ? "chat" : "text");
    cJSON_AddStringToObject(card, "card_id", s_stream_cid);
    cJSON_AddStringToObject(card, "title", s_stream_title);
    // Render only COMPLETE UTF-8: a split delta's half-char waits in the
    // buffer for its continuation bytes instead of flashing as '?'.
    const size_t safe = s_stream_buf ? utf8_complete_len(s_stream_buf, s_stream_len) : 0;
    char held = 0;
    if (s_stream_buf) { held = s_stream_buf[safe]; s_stream_buf[safe] = 0; }
    if (chat) {
        // iOS shape: the question as the user bubble (the receipt every ask
        // deserves), the accumulating answer as the assistant bubble. An
        // EMPTY assistant body renders no bubble — the caret alone says
        // "thinking", which is honest (there are no words yet).
        cJSON *msgs = cJSON_AddArrayToObject(card, "messages");
        cJSON *um = cJSON_CreateObject();
        cJSON_AddStringToObject(um, "role", "user");
        cJSON_AddStringToObject(um, "body", s_stream_q);
        cJSON_AddItemToArray(msgs, um);
        if (s_stream_buf && s_stream_buf[0]) {
            cJSON *am = cJSON_CreateObject();
            cJSON_AddStringToObject(am, "role", "assistant");
            cJSON_AddStringToObject(am, "body", s_stream_buf);
            cJSON_AddItemToArray(msgs, am);
        }
    } else {
        cJSON_AddStringToObject(card, "body", s_stream_buf ? s_stream_buf : "");
    }
    if (s_stream_buf) s_stream_buf[safe] = held;
    if (!caret && s_stream_overflow)
        cJSON_AddStringToObject(card, "footer",
                                "answer exceeded 8KB - tail dropped");
    char *cs = cJSON_PrintUnformatted(card);
    cJSON_Delete(card);
    if (!cs) return ESP_ERR_NO_MEM;
    s_stream_caret = caret;
    s_stream_partial = caret;      // mid-stream = partial; final = full wipe
    s_stream_rendering = true;     // our render must not end our own stream
    const esp_err_t err = tiny_display_render_card(cs);
    s_stream_rendering = false;
    s_stream_partial = false;
    s_stream_caret = false;
    free(cs);
    // Chat feel: the human reads the newest words. Only when overflowing,
    // and through the scroll path so the scrollbar stays honest.
    if (err == ESP_OK && follow_tail && tiny_display_can_scroll()) {
        s_stream_partial = caret;  // the follow-up render keeps the fast path
        s_stream_caret = caret;
        s_stream_rendering = true;
        tiny_display_scroll(1 << 20);
        s_stream_rendering = false;
        s_stream_partial = false;
        s_stream_caret = false;
    }
    return err;
}

extern "C" bool tiny_display_stream_active(void) { return s_stream_on; }

extern "C" esp_err_t tiny_display_stream_begin(const char *title,
                                               const char *card_id,
                                               const char *question) {
    // Re-begin on the SAME empty home stream (voice: recording began it
    // for the listening frame, the SSE path begins it again): keep the frame,
    // skip the second full wipe — nothing on the glass would change.
    if (s_stream_on && card_id && strcmp(card_id, "home") == 0 &&
        strcmp(s_stream_cid, "home") == 0 && s_stream_len == 0 &&
        !(question && *question))
        return ESP_OK;
    if (s_stream_on) stream_reset();   // a new answer replaces a stale one
    if (question && *question) {
        strlcpy(s_stream_q, question, sizeof s_stream_q);
        // strlcpy may have cut mid-UTF-8; retreat to the last complete char
        // (a clipped Turkish question must not end in a fold-'?')
        s_stream_q[utf8_complete_len(s_stream_q, strlen(s_stream_q))] = 0;
    }
    s_stream_buf = (char *)heap_caps_malloc(STREAM_BUF_MAX, MALLOC_CAP_SPIRAM);
    if (!s_stream_buf) s_stream_buf = (char *)malloc(STREAM_BUF_MAX);
    if (!s_stream_buf) return ESP_ERR_NO_MEM;
    s_stream_cap = STREAM_BUF_MAX;
    s_stream_len = 0;
    s_stream_buf[0] = 0;
    s_stream_overflow = false;
    strlcpy(s_stream_title, title && *title ? title : "tiny", sizeof s_stream_title);
    strlcpy(s_stream_cid, card_id && *card_id ? card_id : "stream", sizeof s_stream_cid);
    s_stream_on = true;
    s_stream_last_commit_us = 0;
    // Chrome up front with one FULL refresh; every commit after this is a
    // partial against this baseline (same card_id -> same layout skeleton).
    const esp_err_t err = stream_render(/*caret=*/true, /*follow_tail=*/false);
    if (err != ESP_OK) stream_reset();
    else ESP_LOGI(TAG, "stream begin: \"%s\" (%s)", s_stream_title, s_stream_cid);
    return err;
}

extern "C" esp_err_t tiny_display_stream_append(const char *text) {
    if (!s_stream_on || !s_stream_buf) return ESP_ERR_INVALID_STATE;
    if (!text || !*text) return ESP_OK;
    const size_t n = strlen(text);
    const size_t room = s_stream_cap - 1 - s_stream_len;
    const size_t take = n < room ? n : room;
    if (take < n) s_stream_overflow = true;   // named at stream_end, not lost
    memcpy(s_stream_buf + s_stream_len, text, take);
    s_stream_len += take;
    s_stream_buf[s_stream_len] = 0;
    return take < n ? ESP_ERR_NO_MEM : ESP_OK;
}

extern "C" esp_err_t tiny_display_stream_commit(void) {
    if (!s_stream_on) return ESP_ERR_INVALID_STATE;
    // 400ms floor: the panel's partial path is ~300-500ms and commits that
    // arrive faster COALESCE (the next one carries the accumulated text) —
    // never queue, a queue of stale frames is the opposite of live.
    const int64_t now = esp_timer_get_time();
    if (now - s_stream_last_commit_us < 400000) return ESP_OK;
    s_stream_last_commit_us = now;
    return stream_render(/*caret=*/true, /*follow_tail=*/true);
}

extern "C" esp_err_t tiny_display_stream_end(const char *final_card_json) {
    if (!s_stream_on) return ESP_ERR_INVALID_STATE;
    esp_err_t err;
    // Stream-on-home: a backend ```card``` finale is deliberately NOT painted
    // over home — the rule is that the answer stays on the home
    // surface. The card is logged so nothing disappears silently.
    if (final_card_json && *final_card_json && strcmp(s_stream_cid, "home") == 0) {
        ESP_LOGI(TAG, "final card suppressed on home stream (%d B) - home stays",
                 (int)strlen(final_card_json));
        final_card_json = NULL;
    }
    if (final_card_json && *final_card_json) {
        // The answer carried a ```card``` block: the structured card IS the
        // final frame. stream_reset() first so render_card doesn't log the
        // ended-by-new-card warning about our own finale.
        stream_reset();
        err = tiny_display_render_card(final_card_json);
    } else {
        // Final render: no caret, full refresh — the wipe that says "this is
        // the whole answer and it stays". Regions publish inside render_card
        // as for any card (none staged here; the ask engine's summary reply
        // is the actionable surface).
        err = stream_render(/*caret=*/false, /*follow_tail=*/false);
        stream_reset();
    }
    ESP_LOGI(TAG, "stream end -> %s", esp_err_to_name(err));
    return err;
}

// Canvas status line while the answer has no words yet ("(( listening ))",
// "(( thinking ))"). Stores always; repaints (partial) only when a wordless
// home stream is on the glass — the first real token replaces the note.
extern "C" esp_err_t tiny_display_stream_note(const char *note) {
    strlcpy(s_stream_note, note ? note : "", sizeof s_stream_note);
    if (s_stream_on && s_stream_len == 0 && strcmp(s_stream_cid, "home") == 0)
        return stream_render(/*caret=*/true, /*follow_tail=*/false);
    return ESP_OK;
}
