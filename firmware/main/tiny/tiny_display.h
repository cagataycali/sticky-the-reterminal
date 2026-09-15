// tiny_display — card-spec JSON -> canvas -> e-ink, with refresh policy.
// Card spec v1 (docs/ARCHITECTURE.md): text|list|kv|chart|image|buttons|composite.
// Refresh policy: mono full for cards, partial for status bar/stream frames,
// gray4 full only when card says so. Min 1.5s between refreshes; coalesce.
// The vendor driver rotates 180° on refresh — tiny_display owns orientation so
// screenshot and touch agree on coordinates.
#pragma once
#include "esp_err.h"
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
esp_err_t tiny_display_init(void);
esp_err_t tiny_display_render_card(const char *card_json);   // parse+draw+refresh
esp_err_t tiny_display_splash(const char *version, const char *state);
esp_err_t tiny_display_blit_1bit(const uint8_t *buf, size_t len, bool partial);
// Fetch a server-packed canvas-space .raw (48000 B mono / 96000 B gray4) into
// PSRAM over https. Shared by the image card and tiny_stream. No lock taken.
esp_err_t tiny_display_fetch_raw(const char *url, uint8_t **out, size_t *out_len);
// Gallery (grammar v10): dir=-1/+1 pages the stored set (wraps), 0 reopens.
// ESP_ERR_INVALID_STATE when no set has been sent yet.
esp_err_t tiny_display_gallery_nav(int dir);
int tiny_display_gallery_count(void);
// Screenshot support: copy of the LAST RENDERED framebuffer as 8-bit grayscale
// rows (800x480), for the screenshot verb -> PNG -> /api/media.
esp_err_t tiny_display_snapshot_gray8(uint8_t **out, size_t *out_len);
// Button hit-regions of the LAST rendered card (for tiny_touch + ui_tap).
// Returns count; fills up to max entries. Regions are published atomically
// with the render commit (copy-out under a mutex), so a tap can never resolve
// against a half-written table or the previous card's labels.
typedef struct {
    int x, y, w, h;
    char label[32];
    char id[48];       // spec `buttons[i].id`, or the label when unnamed.
                       // 48 because the messages app encodes its destination in
                       // the id ("m:t:<login>", login up to 39 chars) — a clipped
                       // id would silently open somebody else's conversation.
    char card_id[24];  // spec `card_id` of the card that owns this button
} tiny_button_region_t;
int tiny_display_button_regions(tiny_button_region_t *out, int max);

// Tap acknowledgement: invert button `index`'s rect, partial-refresh so the
// human SEES it, then restore. The e-ink partial path is ~300-500ms and the
// panel busy-wait can reach seconds, so this BLOCKS — never call it from the
// touch task (it would stop sampling fingers). Use tiny_display_ack_button().
esp_err_t tiny_display_flash_button(int index);

// Queue the ack above onto the display's own worker task and return at once.
// This is what the touch path calls. A tap the panel does not answer reads as
// a dead device, so the ack must never be gated on the network round-trip.
esp_err_t tiny_display_ack_button(int index);

// Card the panel is showing right now ("" when none) — for ui_tap events.
const char *tiny_display_current_card_id(void);

// ---- orientation ------------------------------------------------------------
// Rotation of the CARD LAYOUT, in degrees clockwise (0/90/180/270). The
// framebuffer, the screenshot and the touch hit-regions all stay in panel
// coordinates; only the layout turns, so 90/270 lay a card out 480x800.
// set_rotation() redraws the card currently on the glass — a device that has
// been turned must not keep showing the old picture until the next envelope.
int tiny_display_rotation(void);
esp_err_t tiny_display_set_rotation(int degrees);
esp_err_t tiny_display_rerender(void);   // redraw the last card (ESP_ERR_INVALID_STATE if none)
esp_err_t tiny_display_glance(void);     // §12: same redraw, partial refresh (no full flash)
// §12 wake paint: redraw the (deterministic) goodbye card as the diff
// baseline; the NEXT render_card commit becomes one diff-partial (~300-500ms)
// instead of the full flash. Call before the wake card render.
void tiny_display_note_sleep_card(const char *body);  // call at sleep time
esp_err_t tiny_display_arm_wake_baseline(void);

// Generation counter, bumped once per card actually COMMITTED to the panel.
// Sample at finger-down, re-check at lift: if it changed, the card moved under
// the finger and the tap must be dropped rather than applied to a button the
// human never saw.
uint32_t tiny_display_card_gen(void);

// ---- scroll -------------------------------------------------------------
// text/list/kv/menu bodies taller than the body box scroll: full content is
// measured through the canvas y-clip, a right-edge scrollbar shows position,
// steps re-render the cached card via PARTIAL refresh (~300-500ms).
bool tiny_display_can_scroll(void);                  // overflow on the glass?
void tiny_display_scroll_state(int *offset, int *content_h, int *view_h);
esp_err_t tiny_display_scroll(int delta_px);         // clamped; +down into content
esp_err_t tiny_display_scroll_page(int dir);         // ±2/3 view; INVALID_STATE if fits
// Vertical swipe in PANEL coords (both endpoints) -> scroll, rotation-aware.
// NOT_SUPPORTED = not a vertical swipe (caller may treat it as a page gesture).
esp_err_t tiny_display_scroll_gesture(int px0, int py0, int px1, int py1);
// Outcome of the LAST routed gesture, in words ("scrolled 0 -> 82 of 82 px",
// "scroll clamped at top (offset 0 of 82)", "home (bottom-band up)", ...).
// The lesson: receipts that only say "route completed" make a correct
// clamp indistinguishable from a dead rail. Written on the act task by the
// router; read by the swipe receipt after the route-seq bump.
const char *tiny_display_last_gesture(void);

// ---- streaming text -------------------------------
// The human-typer: an agent answer arrives as text deltas and the body
// re-renders through the PARTIAL refresh path (~300-500ms) at <=2Hz, with a
// caret at the tail. begin = one full refresh (chrome + empty body);
// append = buffer only (cheap, call per SSE delta); commit = partial
// re-render, self-limited to >=400ms between commits (extra calls coalesce);
// end = final render WITHOUT the caret via a FULL refresh (clears partial
// ghosting) and only then publishes tap regions — buttons must not exist on
// an answer that may still change. `final_card_json` non-NULL replaces the
// text card entirely (the ```card``` block case).
// `question` non-NULL renders the stream as a `chat` card: the question as
// a right-aligned user bubble (the receipt), the arriving answer typing into
// a left-aligned assistant bubble. NULL = plain text card (voice asks: the
// device never sees its own transcript, so there is nothing true to echo).
esp_err_t tiny_display_stream_begin(const char *title, const char *card_id,
                                    const char *question);
esp_err_t tiny_display_stream_append(const char *text);
esp_err_t tiny_display_stream_commit(void);
// Canvas status line while a home stream has no words yet ("(( listening ))",
// "(( thinking ))"); stored always, painted until the first token replaces it.
esp_err_t tiny_display_stream_note(const char *note);
esp_err_t tiny_display_stream_end(const char *final_card_json);
bool tiny_display_stream_active(void);
#ifdef __cplusplus
}
#endif
