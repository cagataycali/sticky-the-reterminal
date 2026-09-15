idf_component_register(
  SRCS "tiny_main.cpp" "tiny_lock.cpp"
       "board.cpp" "sticky_display.cpp" "sticky_power.cpp" "sticky_buzzer.cpp" "sticky_sdcard.cpp"
       "canvas.cpp" "font.cpp"
       "tiny_display.cpp" "tiny_touch.cpp" "tiny_config.cpp" "tiny_wifi.cpp" "tiny_node.cpp" "tiny_provision.cpp" "tiny_shell.cpp" "tiny_ble.cpp"
       "tiny_audio.cpp" "tiny_upload.cpp" "tiny_ota.cpp" "tiny_button.cpp" "sticky_battery.cpp" "tiny_screenshot.cpp"
       "tiny_sensors.cpp" "sticky_sht40.cpp" "sticky_imu.cpp" "sticky_rtc.cpp"
       "tiny_time.cpp" "tiny_orient.cpp" "tiny_stream.cpp" "tiny_askq.cpp" "tiny_bootmark.cpp" "tiny_agent.cpp" "tiny_onboard.cpp"
       # further tiny/* modules register here as milestones land (M3+)
  INCLUDE_DIRS "." "tiny"
  REQUIRES bt esp_wifi esp_http_server mbedtls esp_http_client esp_https_ota nvs_flash app_update json esp_netif lwip
           seeed_epaper gt911 bq27220 button driver esp_driver_ledc esp_driver_uart esp_driver_i2s vfs
           fatfs sdmmc esp_driver_sdspi
)

# TINY_FW_COMMIT: build-injected git sha — retires the
# version-collision class: "which commit is on the glass" becomes a read).
set(TINY_COMMIT_H ${CMAKE_CURRENT_BINARY_DIR}/gen/tiny_commit.h)
add_custom_target(tiny_gen_commit ALL
  COMMAND ${CMAKE_COMMAND}
          -DREPO_DIR=${CMAKE_CURRENT_SOURCE_DIR}
          -DOUT=${TINY_COMMIT_H}
          -P ${CMAKE_CURRENT_SOURCE_DIR}/cmake/gen_commit.cmake
  BYPRODUCTS ${TINY_COMMIT_H}
  COMMENT "gen tiny_commit.h (git describe --always --dirty)")
add_dependencies(${COMPONENT_LIB} tiny_gen_commit)
target_include_directories(${COMPONENT_LIB} PRIVATE ${CMAKE_CURRENT_BINARY_DIR}/gen)
