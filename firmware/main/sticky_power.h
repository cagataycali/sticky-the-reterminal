#pragma once

// Logs whether this boot followed deep sleep.
void sticky_power_log_wakeup_reason();

// Waits for the AI button release, configures it as the wake source, preserves
// the board power latch, and enters deep sleep. This function does not return.
[[noreturn]] void sticky_power_enter_deep_sleep();
