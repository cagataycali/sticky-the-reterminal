# tiny firmware for reTerminal Sticky — ESP-IDF v5.4
cmake_minimum_required(VERSION 3.16)
# Reuse Seeed's hardware components (seeed_epaper, gt911, bq27220, button) as-is.
set(EXTRA_COMPONENT_DIRS ${CMAKE_CURRENT_LIST_DIR}/../vendor/Sticky_dashboard_demo/components)
# Pin IDF's app_desc version explicitly (second head of the same bug as
# gen_commit.cmake): with PROJECT_VER unset, IDF runs its OWN repo-wide `git
# describe --dirty` — so uncommitted docs elsewhere in the repo tainted the app
# descriptor even after tiny_commit.h was scoped to firmware/. One version, one source.
# …and reconfigure when the version header changes, or THIS read goes stale
# on incremental builds (the exact staleness class gen_commit.cmake retired).
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
             ${CMAKE_CURRENT_LIST_DIR}/main/tiny/tiny_version.h)
file(READ ${CMAKE_CURRENT_LIST_DIR}/main/tiny/tiny_version.h TINY_VER_H)
string(REGEX MATCH "TINY_FW_VERSION \"([^\"]+)\"" _ ${TINY_VER_H})
set(PROJECT_VER ${CMAKE_MATCH_1})
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(tiny_sticky)
