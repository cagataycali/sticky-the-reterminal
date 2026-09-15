// tiny_agent — THE UNIVERSE ON THE GLASS (grammar v11).
// Which tiny answers the asks. Empty slug = the owner's own tiny, and the
// ask body stays byte-identical to pre-universe firmware (the backend pins
// this with a regression test). A non-empty slug rides the ask as
// `tiny:"<slug>"` (backend contract, tinyai-id 61b404f0) and the turn runs
// as that PUBLIC tiny — its identity, no owner tools (backend enforces).
//
// State is NVS-backed ("tinyagent"): the active slug survives reboot, plus
// a small roster of slugs the owner can switch between from the universe
// card. Roster is editable over relay (`agent add/rm`) and seeded once
// with "tiny" so the card is never an empty page on first open.
#pragma once
#include <stddef.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define TINY_AGENT_SLUG_MAX 64   // backend clamps at 64 — refuse locally too
#define TINY_AGENT_ROSTER_MAX 8

// Active slug, "" when the owner's own tiny (default). Never NULL.
const char *tiny_agent_current(void);

// Switch. NULL/"" clears back to the owner's own tiny. Trims whitespace,
// lowercases, refuses >64 chars or embedded spaces/quotes. Persists to NVS
// and (on a real switch) adds the slug to the roster.
esp_err_t tiny_agent_set(const char *slug);

// Roster access: returns count, copies up to max slugs into out[i] (each
// cap TINY_AGENT_SLUG_MAX+1 bytes).
int tiny_agent_roster(char out[][TINY_AGENT_SLUG_MAX + 1], int max);
esp_err_t tiny_agent_roster_add(const char *slug);
esp_err_t tiny_agent_roster_remove(const char *slug);

#ifdef __cplusplus
}
#endif
