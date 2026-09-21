// tiny_beacon — TinyBeacon v2 on the Sticky (NimBLE broadcaster + peripheral).
//
// Packet layout (SPEC §2.4, 31-byte legacy PDUs):
//   ADV_IND : flags(3) + Complete 128-bit UUID list(18) + mfg SHORT(10) = 31 ✔
//   SCAN_RSP: mfg FULL 'T''N'+header(15) + Complete Local Name(≤14+2)
// Passive observers (another Sticky) read the SHORT form witnessed by the UUID;
// active scanners (iPhone, Mac, bleak) get the FULL header + name.
//
// Catalog GATT (SPEC §3): identity (read) · tools (read/write page) · hello
// (write) · endpoint (read). Reads > MTU are served through NimBLE's
// os_mbuf long-read path (the stack handles offsets). No pairing: nothing
// served here is secret — the device id is hashed, the name is what the
// owner already chose to show.
#include "tiny/tiny_beacon.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "host/ble_gap.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "mbedtls/sha256.h"
#include "os/os_mbuf.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

static const char *TAG = "tiny_beacon";

// ---- pinned constants (SPEC §0) --------------------------------------------
#define TB_VERSION2   0x10
#define TB_KIND_STICKY 8
#define TB_FLAG_PROVISIONED (1u << 0)
#define TB_FLAG_SOCIAL      (1u << 1)
#define TB_FLAG_CATALOG     (1u << 2)
#define TB_FLAG_BATTERY     (1u << 3)
#define TB_FLAG_AGENT       (1u << 6)
#define TB_TOOLS_PER_PAGE 6
#define TB_ROTATE_US (15ULL * 60 * 1000 * 1000)

// f0e60ba4-b906-41fb-9d15-bb1312d27564 (little-endian on air)
static const ble_uuid128_t UUID_CATALOG = BLE_UUID128_INIT(
    0x64, 0x75, 0xd2, 0x12, 0x13, 0xbb, 0x15, 0x9d, 0xfb, 0x41, 0x06, 0xb9, 0xa4, 0x0b, 0xe6, 0xf0);
// characteristics: ...0001 identity, ...0002 tools, ...0003 hello, ...0004 endpoint
#define TB_CHR(last) BLE_UUID128_INIT(last, 0x00, 0xd2, 0x12, 0x13, 0xbb, 0x15, 0x9d, 0xfb, 0x41, 0x06, 0xb9, 0xa4, 0x0b, 0xe6, 0xf0)
static const ble_uuid128_t UUID_IDENTITY = TB_CHR(0x01);
static const ble_uuid128_t UUID_TOOLS    = TB_CHR(0x02);
static const ble_uuid128_t UUID_HELLO    = TB_CHR(0x03);
static const ble_uuid128_t UUID_ENDPOINT = TB_CHR(0x04);

static tiny_beacon_cfg_t s_cfg;
static char s_name[48], s_devid[48], s_fw[24];
static const char *s_tools[32];
static bool s_configured = false, s_running = false, s_synced = false;
static uint16_t s_session = 0;
static uint8_t s_tools_page = 0;
static bool s_hello_seen = false;
static esp_timer_handle_t s_rotate = NULL;

// ---- header (SPEC §2) -------------------------------------------------------
static uint8_t flags_byte(void) {
    uint8_t f = TB_FLAG_PROVISIONED | TB_FLAG_CATALOG | TB_FLAG_AGENT;
    if (s_cfg.social) f |= TB_FLAG_SOCIAL;
    if (s_cfg.battery) f |= TB_FLAG_BATTERY;
    return f;
}
static void header9(uint8_t out[9]) {
    int tools = s_cfg.tool_count > 255 ? 255 : s_cfg.tool_count;
    out[0] = TB_VERSION2; out[1] = TB_KIND_STICKY; out[2] = flags_byte();
    out[3] = (uint8_t)(s_cfg.caps & 0xFF); out[4] = (uint8_t)(s_cfg.caps >> 8);
    out[5] = (uint8_t)tools;
    out[6] = (uint8_t)(s_session & 0xFF); out[7] = (uint8_t)(s_session >> 8);
    out[8] = (uint8_t)s_cfg.rssi1m;
}

static void dev_hash(char out[17]) {
    unsigned char h[32];
    mbedtls_sha256((const unsigned char *)s_devid, strlen(s_devid), h, 0);
    for (int i = 0; i < 8; ++i) sprintf(out + i * 2, "%02x", h[i]);
    out[16] = 0;
}

// ---- catalog JSON (SPEC §3) -------------------------------------------------
static int identity_json(char *buf, size_t n) {
    char dh[17]; dev_hash(dh);
    uint8_t h[9]; header9(h);
    int w = snprintf(buf, n, "{\"v\":2,\"kind\":%d,\"dev\":\"%s\",\"platform\":\"esp32s3\",\"fw\":\"%s\",\"tools\":%d,\"caps\":%u,\"session\":%u",
                     TB_KIND_STICKY, dh, s_fw, s_cfg.tool_count, (unsigned)s_cfg.caps, (unsigned)s_session);
    if (s_cfg.social && s_name[0]) w += snprintf(buf + w, n - w, ",\"name\":\"%s\"", s_name);
    w += snprintf(buf + w, n - w, "}");
    return w;
}
static int tools_json(char *buf, size_t n) {
    if (s_cfg.social && !s_hello_seen) return snprintf(buf, n, "{\"p\":0,\"n\":0,\"items\":[],\"err\":\"hello_first\"}");
    int pages = (s_cfg.tool_count + TB_TOOLS_PER_PAGE - 1) / TB_TOOLS_PER_PAGE; if (pages < 1) pages = 1;
    int p = s_tools_page >= pages ? pages - 1 : s_tools_page;
    int w = snprintf(buf, n, "{\"p\":%d,\"n\":%d,\"items\":[", p, pages);
    for (int i = p * TB_TOOLS_PER_PAGE; i < s_cfg.tool_count && i < (p + 1) * TB_TOOLS_PER_PAGE; ++i)
        w += snprintf(buf + w, n - w, "%s{\"name\":\"%s\"}", i == p * TB_TOOLS_PER_PAGE ? "" : ",", s_tools[i]);
    w += snprintf(buf + w, n - w, "]}");
    return w;
}
static int endpoint_json(char *buf, size_t n) {
    return snprintf(buf, n, "{\"device\":\"tinyapp://device/%s\"}", s_devid);
}

static int chr_access(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg) {
    const ble_uuid_t *u = ctxt->chr->uuid;
    char buf[512];
    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
        int w;
        if (ble_uuid_cmp(u, &UUID_IDENTITY.u) == 0) w = identity_json(buf, sizeof buf);
        else if (ble_uuid_cmp(u, &UUID_TOOLS.u) == 0) w = tools_json(buf, sizeof buf);
        else if (ble_uuid_cmp(u, &UUID_ENDPOINT.u) == 0) w = endpoint_json(buf, sizeof buf);
        else return BLE_ATT_ERR_ATTR_NOT_FOUND;
        return os_mbuf_append(ctxt->om, buf, w) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    if (ctxt->op == BLE_GATT_ACCESS_OP_WRITE_CHR) {
        uint16_t len = OS_MBUF_PKTLEN(ctxt->om);
        if (ble_uuid_cmp(u, &UUID_HELLO.u) == 0) {
            char hello[160] = {0};
            os_mbuf_copydata(ctxt->om, 0, len < sizeof hello - 1 ? len : sizeof hello - 1, hello);
            s_hello_seen = true;   // per-connection in a fuller build; one central at a time on this board
            ESP_LOGI(TAG, "hello: %s", hello);
            return 0;
        }
        if (ble_uuid_cmp(u, &UUID_TOOLS.u) == 0) {
            uint8_t page = 0; if (len >= 1) os_mbuf_copydata(ctxt->om, 0, 1, &page);
            s_tools_page = page;
            return 0;
        }
    }
    return BLE_ATT_ERR_UNLIKELY;
}

static const struct ble_gatt_chr_def s_chrs[] = {
    { .uuid = &UUID_IDENTITY.u, .access_cb = chr_access, .flags = BLE_GATT_CHR_F_READ },
    { .uuid = &UUID_TOOLS.u,    .access_cb = chr_access, .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_WRITE },
    { .uuid = &UUID_HELLO.u,    .access_cb = chr_access, .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP },
    { .uuid = &UUID_ENDPOINT.u, .access_cb = chr_access, .flags = BLE_GATT_CHR_F_READ },
    { 0 },
};
static const struct ble_gatt_svc_def s_svcs[] = {
    { .type = BLE_GATT_SVC_TYPE_PRIMARY, .uuid = &UUID_CATALOG.u, .characteristics = s_chrs },
    { 0 },
};

extern "C" void tiny_beacon_gatt_register(void) {
    ble_svc_gap_init();
    ble_svc_gatt_init();
    int rc = ble_gatts_count_cfg(s_svcs);
    if (rc == 0) rc = ble_gatts_add_svcs(s_svcs);
    if (rc != 0) ESP_LOGE(TAG, "gatt register rc=%d", rc);
    ble_svc_gap_device_name_set("tiny");
}

// ---- advertising (SPEC §2.4) ------------------------------------------------
static int gap_event(struct ble_gap_event *ev, void *) {
    switch (ev->type) {
        case BLE_GAP_EVENT_CONNECT:
            s_hello_seen = false; s_tools_page = 0;
            ESP_LOGI(TAG, "catalog reader connected (%d)", ev->connect.status);
            return 0;
        case BLE_GAP_EVENT_DISCONNECT:
            ESP_LOGI(TAG, "catalog reader gone (%d)", ev->disconnect.reason);
            if (s_running) tiny_beacon_on_sync();   // NimBLE stops advertising on connect; resume
            return 0;
        case BLE_GAP_EVENT_ADV_COMPLETE:
            if (s_running) tiny_beacon_on_sync();
            return 0;
        default: return 0;
    }
}

static int start_adv(void) {
    uint8_t h[9]; header9(h);
    // ADV_IND: flags + UUID128 + mfg SHORT (ffff + first 6 header bytes)
    uint8_t mshort[8] = { 0xff, 0xff, h[0], h[1], h[2], h[3], h[4], h[5] };
    struct ble_hs_adv_fields adv = {};
    adv.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    adv.uuids128 = &UUID_CATALOG; adv.num_uuids128 = 1; adv.uuids128_is_complete = 1;
    adv.mfg_data = mshort; adv.mfg_data_len = sizeof mshort;
    int rc = ble_gap_adv_set_fields(&adv);
    if (rc != 0) { ESP_LOGE(TAG, "adv fields rc=%d", rc); return rc; }
    // SCAN_RSP: mfg FULL + name (name only when social — SPEC §4.3)
    uint8_t mfull[15] = { 0xff, 0xff, 0x54, 0x4e };
    memcpy(mfull + 4, h, 9);
    struct ble_hs_adv_fields rsp = {};
    rsp.mfg_data = mfull; rsp.mfg_data_len = sizeof mfull;
    if (s_cfg.social && s_name[0]) {
        rsp.name = (uint8_t *)s_name; rsp.name_len = strlen(s_name) > 14 ? 14 : strlen(s_name); rsp.name_is_complete = 1;
    }
    rc = ble_gap_adv_rsp_set_fields(&rsp);
    if (rc != 0) { ESP_LOGE(TAG, "rsp fields rc=%d", rc); return rc; }
    struct ble_gap_adv_params p = {};
    p.conn_mode = BLE_GAP_CONN_MODE_UND;   // connectable → catalog reads
    p.disc_mode = BLE_GAP_DISC_MODE_GEN;
    p.itvl_min = BLE_GAP_ADV_ITVL_MS(250); p.itvl_max = BLE_GAP_ADV_ITVL_MS(400);  // ~3 Hz for RSSI streams, e-ink board can afford it
    uint8_t own_addr_type;
    ble_hs_util_ensure_addr(0);
    ble_hs_id_infer_auto(0, &own_addr_type);
    rc = ble_gap_adv_start(own_addr_type, NULL, BLE_HS_FOREVER, &p, gap_event, NULL);
    if (rc != 0 && rc != BLE_HS_EALREADY) { ESP_LOGE(TAG, "adv start rc=%d", rc); return rc; }
    ESP_LOGI(TAG, "on air: kind sticky session 0x%04x tools %d %s", s_session, s_cfg.tool_count, s_cfg.social ? "social" : "quiet");
    return 0;
}

extern "C" void tiny_beacon_on_sync(void) {
    s_synced = true;
    if (s_configured && s_running) start_adv();
}

static void rotate_cb(void *) {
    s_session = (uint16_t)(esp_random() % 0xFFFF) + 1;
    if (s_running && s_synced) { ble_gap_adv_stop(); start_adv(); }
}

extern "C" esp_err_t tiny_beacon_start(const tiny_beacon_cfg_t *cfg) {
    if (!cfg) return ESP_ERR_INVALID_ARG;
    s_cfg = *cfg;
    strlcpy(s_name, cfg->name ? cfg->name : "", sizeof s_name);
    strlcpy(s_devid, cfg->device_id ? cfg->device_id : "", sizeof s_devid);
    strlcpy(s_fw, cfg->fw ? cfg->fw : "", sizeof s_fw);
    s_cfg.tool_count = cfg->tool_count > 32 ? 32 : cfg->tool_count;
    for (int i = 0; i < s_cfg.tool_count; ++i) s_tools[i] = cfg->tools[i];
    s_cfg.tools = s_tools;
    if (s_session == 0) s_session = (uint16_t)(esp_random() % 0xFFFF) + 1;
    s_configured = true; s_running = true;
    if (!s_rotate) {
        const esp_timer_create_args_t a = { .callback = rotate_cb, .arg = NULL, .dispatch_method = ESP_TIMER_TASK, .name = "tb_rotate", .skip_unhandled_events = true };
        esp_timer_create(&a, &s_rotate);
        esp_timer_start_periodic(s_rotate, TB_ROTATE_US);
    }
    if (s_synced) return start_adv() == 0 ? ESP_OK : ESP_FAIL;
    return ESP_OK;   // tiny_ble bring-up calls tiny_beacon_on_sync
}

extern "C" esp_err_t tiny_beacon_stop(void) {
    s_running = false;
    if (s_synced) ble_gap_adv_stop();
    return ESP_OK;
}
extern "C" bool tiny_beacon_running(void) { return s_running && s_synced; }
extern "C" uint16_t tiny_beacon_session(void) { return s_session; }
