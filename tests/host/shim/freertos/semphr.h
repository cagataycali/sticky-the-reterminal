#pragma once
typedef void* SemaphoreHandle_t;
#define pdTRUE 1
static inline SemaphoreHandle_t xSemaphoreCreateMutex(void){ static int d; return &d; }
static inline int xSemaphoreTake(SemaphoreHandle_t, unsigned long){ return 1; }
static inline int xSemaphoreGive(SemaphoreHandle_t){ return 1; }
