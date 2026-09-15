// host shim
#pragma once
#include <cstdio>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_SIZE 0x104
#define ESP_ERR_NOT_FOUND 0x105
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_STATE 0x103
static inline const char *esp_err_to_name(esp_err_t e){static char b[16];snprintf(b,sizeof b,"0x%x",e);return b;}
