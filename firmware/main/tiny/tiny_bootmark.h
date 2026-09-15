// P0-BOUNCE flight recorder (0.25.36 probe): the 0.25.35 trial died pre-log
// and rolled back — nobody saw the stage. Each boot writes a stage number to
// NVS SYNCHRONOUSLY before/after the risky steps; a crash freezes bm_last at
// the last stage reached, and NOTHING clears it except the next
// bootmark-aware boot (0.25.8 never touches the namespace, so the evidence
// SURVIVES the rollback). `status` reports the previous boot's final stage.
// Stages: 1 board · 2 display · 3 config/buzzer restores · 4 splash full ·
// 5 battery+clock · 6 inputs · 7 shell/home · 8 wifi walk returned ·
// 9 validity mark (heartbeat-200). A previous-boot value of 9 is a clean run.
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
void tiny_bootmark_stage(uint8_t stage);
// The stage the PREVIOUS boot reached (read once at this boot's first write;
// -1 = no write has happened yet this boot).
int tiny_bootmark_prev(void);
#ifdef __cplusplus
}
#endif
