// tiny_askq — §16 law 2: NVS-backed FIFO of text asks awaiting the network.
// Depth 8, survives reboot. See tiny_askq.cpp for the rules.
#pragma once
#include <stddef.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define TINY_ASKQ_MAX_LEN 480  // one NVS string slot; kb asks are far shorter

int tiny_askq_count(void);
// NO_MEM when full (caller tells the human the queue is full — honestly).
esp_err_t tiny_askq_push(const char *text);
esp_err_t tiny_askq_peek(char *out, size_t cap);  // NOT_FOUND when empty
esp_err_t tiny_askq_pop(void);                    // after a successful send

#ifdef __cplusplus
}
#endif
