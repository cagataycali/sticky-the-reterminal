#pragma once

// Power
#define PIN_POWER_BTN       4
#define PIN_POWER_HOLD      45
#define PIN_POWER_LOCK      46
// Battery charger BQ25616
#define PIN_BAT_CHG_EN      39  // EN_BAT_CHGn, active low
#define PIN_EXTERNAL_POWER  9   // USB/external power detect, high when present

// I2C1 - Sensor bus (BQ27220 + RTC + SHT40 + IMU)
#define PIN_SENSOR_SCL      0
#define PIN_SENSOR_SDA      1

// Battery fuel gauge BQ27220
#define BQ27220_I2C_ADDR    0x55

// RTC PCF8563
#define PCF8563_I2C_ADDR    0x51

// Buttons
#define PIN_BTN_UP          5
#define PIN_BTN_DOWN        6
#define PIN_BTN_OK          PIN_POWER_BTN

// Buzzer
#define PIN_BUZZER          48

// SHT40 temperature and humidity sensor
#define SHT40_I2C_ADDR      0x44

// LSM6DS3TR-C 6-axis IMU
#define LSM6DS3_I2C_ADDR    0x6A

// PDM microphone
#define PIN_MIC_CLK         19
#define PIN_MIC_DATA        20
#define PIN_MIC_EN          38

// I2C0 - Touch panel (GT911)
#define PIN_TOUCH_SCL       2
#define PIN_TOUCH_SDA       3
#define PIN_TOUCH_EN        42
#define PIN_TOUCH_INT       21
#define PIN_TOUCH_RST       41

// E-paper SSD1677 (SPI)
#define PIN_EPD_MOSI        14
#define PIN_EPD_CLK         13
#define PIN_EPD_MISO        12
#define PIN_EPD_CS          15
#define PIN_EPD_DC          16
#define PIN_EPD_RST         17
#define PIN_EPD_BUSY        18
#define PIN_EPD_EN          47

// MicroSD (shares the SPI signal lines with the e-paper display)
#define PIN_SD_CS           8
#define PIN_SD_DETECT       11
#define PIN_SD_EN           10
#define PIN_SD_MOSI         PIN_EPD_MOSI
#define PIN_SD_MISO         PIN_EPD_MISO
#define PIN_SD_CLK          PIN_EPD_CLK
