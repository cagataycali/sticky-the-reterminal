# Glass captures — real framebuffer, fw 0.27.0-u2 (5f178635), grammar v11

Every PNG here is a `screenshot` reply from Sticky #1 (`sticky`,
b20893b4-ccc5-4f40-8cda-443c9d9dbdaa) — 800×480, ≤4 gray levels, no pixel
edits (portrait files excepted, see C). Reproduce: `rotate 0` first (the
`screenshot` verb returns PANEL SPACE, so an auto-rotated portrait UI comes
out lying on its side — the first GALLERY pass shipped 17 sideways files),
then the verb, wait ~4 s for the e-ink refresh, then `screenshot`. Finish
with `rotate auto` + `page home`. Orientation column = verified by OCR of
the header line (tesseract) on the saved file, not assumed. `rotate 0` is
upright (not 180) on fw 0.27.0-u2.

## A. Pages

| image | verb | captured (UTC) | orientation | note |
|---|---|---|---|---|
| `home.png` | `page home` | 2026-09-07T04:30Z | landscape-verified | hero — also copied to `docs/img/home-live.png` and `docs/img/glass-home-panelspace.png` |
| `status.png` | `page status` | 2026-09-07T04:31Z | landscape-verified | |
| `settings.png` | `page settings` | 2026-09-07T04:33Z | landscape-verified | |
| `wifi.png` | `page wifi` | 2026-09-07T04:35Z | landscape-verified | roaming list + RSSI |
| `ble.png` | `page ble` | 2026-09-07T04:42Z | landscape-verified | |
| `sensors.png` | `page sensors` | 2026-09-07T04:32Z | landscape-verified | live SHT40/IMU/RTC/BQ27220 read |
| `messages.png` | `messages` | 2026-09-07T04:44Z | landscape-verified | DM inbox, card_id `dm-inbox` (no `page messages`; the verb itself paints it) |
| `universe.png` | `page universe` | 2026-09-07T04:46Z | landscape-verified | agent roster, touch id `u:open` |
| `gallery.png` | `page gallery` | 2026-09-07T04:48Z | landscape-verified | SD-card photo viewer, fullscreen — shows the `PHOTO 2` test asset with a dashed frame (whatever is on the card), touch id `g:open` |

## B. Cards (`render_ui <json>`)

| image | spec | captured (UTC) | orientation |
|---|---|---|---|
| `card-text.png` | `{"type":"text","card_id":"doc-text","title":"Dinner","body":"Back at 19:30 - pizza in the fridge, oven at 220C for 8 minutes. Leave the door unlocked for the courier, the parcel is the servo order.","footer":"from cagatay, 18:04","buttons":["Got it","Later"]}` | 2026-09-07T04:53Z | landscape-verified |
| `card-list.png` | `{"type":"list","card_id":"doc-list","title":"Groceries","items":["oat milk","eggs x12","sourdough","basil","parmesan","M3x20 screws (for the arm)","AA batteries"],"footer":"7 items - tap the AI button to add by voice"}` | 2026-09-07T04:54Z | landscape-verified |
| `card-kv.png` | `{"type":"kv","card_id":"doc-kv","title":"Weather - Newark","rows":[["now","17.5 C, clear sky"],["feels like","18.5 C"],["humidity","91%"],["wind","7.7 km/h"],["sunrise","06:27"],["tonight","low 15 C"]],"footer":"open-meteo, 00:30"}` (live Open-Meteo numbers) | 2026-09-07T04:55Z | landscape-verified |
| `card-menu.png` | `{"type":"menu","card_id":"doc-menu","title":"Who's home?","items":[{"id":"m:t:cagatay","label":"cagatay","note":"living room - 2 min ago"},{"id":"m:t:mac","label":"cagatay-mac","note":"desk - online"},{"id":"m:t:arm","label":"strands-arm","note":"folded - torque off"},{"id":"m:t:printer","label":"3D printer","note":"idle - bed 25 C"},{"id":"m:t:fleet","label":"stickies","note":"3 online"}]}` | 2026-09-07T04:56Z | landscape-verified |
| `card-composite.png` | `{"type":"composite","card_id":"doc-composite","title":"Morning","parts":[{"type":"kv","title":"Today","rows":[["weather","17 C, clear"],["first meeting","10:00 standup"],["printer","follower plate 62%"]]},{"type":"list","title":"Do","items":["calibrate wrist sign","reply to Seeed re: vendor license","order M3 heat inserts"]}],"footer":"tap the AI button to talk"}` | 2026-09-07T05:00Z | landscape-verified |
| `card-qr.png` | `{"type":"qr","card_id":"doc-qr","title":"Join my Wi-Fi","text":"WIFI:S:tiny-1a2b;T:WPA;P:tinysetup;;","caption":"scan to join tiny-1a2b"}` | 2026-09-07T05:02Z | landscape-verified |
| `card-chart.png` | `{"type":"chart","card_id":"doc-chart","title":"Office temp - 24h","data":[19.2,19.0,18.8,18.6,18.5,18.4,18.6,19.1,20.0,21.2,22.4,23.3,24.0,24.4,24.6,24.3,23.7,22.9,22.0,21.2,20.6,20.1,19.7,19.4],"style":"line","y_min":15,"y_max":30,"footer":"C, one point per hour - nicla sense"}` | 2026-09-07T05:04Z | landscape-verified |
| `card-chat.png` | `{"type":"chat","card_id":"doc-chat","title":"tiny","messages":[{"role":"user","body":"is the printer done?"},{"role":"assistant","body":"Not yet - the follower plate is at 62%, about 4h 40m left. Bed 60 C, nozzle 215 C, no errors."},{"role":"user","body":"ping me when it finishes"},{"role":"assistant","body":"Will do."}]}` | 2026-09-07T05:06Z | landscape-verified |
| `card-image.png` | `{"type":"image","card_id":"doc-image","url":"https://sticky.cagatay.my/api/frames/c26291311f4cc287/0.raw"}` — duck frame 0 via the render rail. **Verified: 0 of 384,000 px differ** from the server's `0.png` preview | 2026-09-07T05:10Z | landscape-verified (0 px differ from rail 0.png) |
| `say.png` | `say Printer finished the follower plate. 12h 28m, no errors. Bed is cooling - give it ten minutes before you pop the parts.` | 2026-09-07T05:12Z | landscape-verified |
| `anim-duck.gif` · `anim-duck-strip.png` | `play https://sticky.cagatay.my/api/frames/c26291311f4cc287/manifest 0.5 8` → `stream done: 8/8 frames`. Frames are the rail's own previews (`<n>.png`), which the image-card diff above proves are what the glass paints. `screenshot` after a stream redraws the *card* under it, so a stream frame cannot be screenshotted — by design (`screenshot` is refused *during* play) | 2026-09-07T04:31Z | landscape — rail previews, never a glass screenshot, so unaffected by the sideways bug (gif frame 0 = rail 0.png, 0 px differ) |

| `ask.png` | `ask In one short sentence, what is a reTerminal E1001?` → reply streamed to the glass (`[streamed to glass]`), then `screenshot`. Four gray levels: `you:` line + answer | 2026-09-07T05:18Z | landscape-verified |
| `portrait-home.png` | `rotate 90` · `page home` · `screenshot`. The framebuffer is always 800×480 panel-native; the file is that frame rotated 90° CCW so it reads as the device stands on the desk (480×800). Header top, status top-right, "Message tiny…" footer at the bottom | 2026-09-07T05:15Z | portrait-verified (rotate 90) |
| `portrait-kv.png` | `rotate 90` · kv card "Nicla Sense" (temp/humidity/pressure/IAQ/motion/battery) · `screenshot`, same 90° CCW viewer rotation | 2026-09-07T05:16Z | portrait-verified (rotate 90) |

## Status

Captures re-shot upright 2026-09-07; two portrait captures added the same day.
