// Test cases — the deep-sleep/wake contract acceptance spec: A2 + A1 storage half.
static int fails = 0;
static void expect(bool ok, const char *what) {
    printf("%s %s\n", ok ? "PASS" : "FAIL", what);
    if (!ok) ++fails;
}
static bool valid_utf8(const char *s) {
    const unsigned char *p = (const unsigned char *)s;
    while (*p) {
        int n = *p < 0x80 ? 1 : (*p & 0xE0) == 0xC0 ? 2
              : (*p & 0xF0) == 0xE0 ? 3 : (*p & 0xF8) == 0xF0 ? 4 : 0;
        if (!n) return false;
        for (int i = 1; i < n; ++i)
            if ((p[i] & 0xC0) != 0x80) return false;
        p += n;
    }
    return true;
}
int main() {
    char dst[32];

    // A1 (storage half): byte-identical round trip for a fitting label.
    const char *tr = "K\xC4\xB1rg\xC4\xB1l \xC4\x9F\xC5\x9F\xC3\xBC test";
    copy_label_utf8(dst, sizeof dst, tr);
    expect(strcmp(dst, tr) == 0, "A1s: Kırgıl ğşü test survives byte-identical");

    // A2 core: 29 ASCII + 2-byte ğ = 31 bytes, fits cap-1 exactly.
    char in[64];
    memset(in, 'a', 29); in[29] = 0; strcat(in, "\xC4\x9F");
    copy_label_utf8(dst, sizeof dst, in);
    expect(strcmp(dst, in) == 0, "A2a: 29a+ğ (31B) fits whole");

    // A2 knife: 30 ASCII + ğ = 32 bytes; byte 31 would be the ğ's LEAD.
    memset(in, 'a', 30); in[30] = 0; strcat(in, "\xC4\x9F");
    copy_label_utf8(dst, sizeof dst, in);
    expect(strlen(dst) == 30 && dst[29] == 'a',
           "A2b: cut lands BEFORE the rune, not through it");
    expect(valid_utf8(dst), "A2b: result is valid UTF-8 (no lone lead byte)");

    // A2 deep: truncation point falls after byte 1 of a 3-byte rune.
    memset(in, 'a', 29); in[29] = 0; strcat(in, "\xE2\x82\xAC");  // €
    copy_label_utf8(dst, sizeof dst, in);
    expect(strlen(dst) == 29 && valid_utf8(dst),
           "A2c: 3-byte rune cut at byte 2/3 drops the whole rune");

    // A2 empty/null resilience.
    copy_label_utf8(dst, sizeof dst, NULL);
    expect(dst[0] == 0, "A2d: NULL -> empty, no crash");
    copy_label_utf8(dst, sizeof dst, "");
    expect(dst[0] == 0, "A2e: empty -> empty");

    // 4-byte rune (emoji) at the boundary.
    memset(in, 'a', 28); in[28] = 0; strcat(in, "\xF0\x9F\xA6\x86");  // 🦆
    copy_label_utf8(dst, sizeof dst, in);
    expect(strlen(dst) == 32 - 1 - 0 || (strlen(dst) == 28 && valid_utf8(dst)),
           "A2f: 4-byte rune fitting exactly at cap-1 handled");
    // 28+4=32 > 31 -> must drop the emoji entirely.
    expect(strlen(dst) == 28 && valid_utf8(dst),
           "A2f2: emoji straddling cap dropped whole");

    printf(fails ? "\n%d FAILED\n" : "\nALL PASS\n", fails);
    return fails ? 1 : 0;
}
