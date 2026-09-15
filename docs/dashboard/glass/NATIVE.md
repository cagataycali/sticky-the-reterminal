# NATIVE.md — what each component would cost inside the firmware

The server-rendered path ships 96 000 bytes per card. A native card ships its
*data* and lets the ESP32-S3 draw — these are the one-line estimates recorded
with each component (`native_hint`), for a future native firmware path. Sizes are
payload guesses, not measurements.

| component | native form |
|---|---|
| `weather_bar` | type:'weather' {temp,unit,code,hi,lo,precip,hours:[12×{t,temp,p}],days:[5×{dow,code,hi,lo}]} ≈ 600 B |
| `calendar_day` | type:'agenda' {date, now, all_day:[..], events:[{s,e,title,loc}] ≤12} ≈ 800 B; partial refresh of the now-line every minute |
| `calendar_month` | type:'month' {y,m,today,dots:[42 x u8 count|0x80 allday], next:[{d,t,title}] ≤8} ≈ 300 B; one full refresh per day |
| `notifications` | type:'inbox' {title, n_new, rows:[{src:u8, from, text≤80, age_s, unread}] ≤10} ≈ 1 KB; partial refresh per row on new item |
| `now` | type:'now' {clock, date, temp, cond:u8, hi, lo, next:{t,title,loc,in_min}, sunrise, sunset, n_events, n_new} ≈ 200 B; the clock is the ONE field that wants a per-minute partial refresh — a native card wins here |
| `fleet` | type:'fleet' {n_on, n, groups:[{label, rows:[{name, plat, age_s|-1, on:u8, batt?, fw?}]}]} ≈ 600 B; refresh every 5 min |
| `sun` | type:'sun' {rise_min, set_min, now_min, day_len_s, delta_s, tm_rise, tm_set} ≈ 40 B — the arc is pure geometry the firmware can draw; refresh every 10 min |
| `week` | type:'week' {days:[{dom, dow, code:u8, hi:i8, lo:i8, pop:u8, n_ev:u8, n_all:u8, t1}]} ≈ 350 B; refresh hourly |
| `github` | type:'github' {login, year, today, week, streak, grid: 140 × u2 (35 B packed), events:[{ago, repo, what}]} ≈ 500 B; refresh every 15 min |
| `moon` | type:'moon' {f:u16 (fraction×65535), next:[{k, in_d}]} ≈ 24 B — the firmware can draw the disc from f alone; refresh hourly |
| `air` | type:'air' {aqi:u16, eaqi:u8, uv:u8×24 (×10), pm25, pm10, no2, o3, so2, co: u16} ≈ 44 B; refresh hourly |
| `habits` | type:'habits' {names[], bits: n×30 packed (4 B/habit), today:u8} ≈ 120 B; refresh on edit |
| `poster` | type:'poster' {kicker, text, sub, by} ≤ 300 B — the firmware already has a text card; this adds auto-fit + wrap rules |
| `countdown` | type:'countdown' {hero:{days:u16, label, date}, list:[{label, days:u16}]} ≈ 160 B; refresh daily at 00:00 |
| `clock` | type:'clock' {face:u8, zones:[{city, off:i8}]} ≈ 60 B — the firmware has the RTC; draw locally each minute |
| `photo` | the whole point is the 96 000-byte frame — there is no smaller native form; ship the raw |
| `arm` | type:'arm' {q:[6×i16 ×10], v:u8, torque:u8, pose, head:{rssi,tof}} ≈ 40 B — the silhouette is 4 line segments the firmware can draw |
| `focus` | type:'focus' {ds,de,now, ev:[{s,e,title}], fb:[{s,e,state:u8,label}], free:[{s,e}]} ≈ 400 B; refresh each minute for the now-line (partial) |
| `goals` | type:'goals' {n, items:[{name, v:u16, t:u16, unit, hist:7×u16}]} ≈ 120 B; rings are arcs the firmware can draw |
| `compose` | type:'composite' already exists in firmware — this maps tile boxes onto its regions; each tile's native form ≤ 120 B |
| `todo` | type:'todo' {title, top, groups:[{name, items:[{t,done,tag}]}]} ≈ 600 B; text-only — the firmware list card is one step away |
| `overnight` | type:'overnight' {tomorrow, first:{t,title,where}, line, due:[…], moon_f, footer} ≈ 300 B; the firmware's `sleep` verb keeps it at 0 draw |
| `agenda` | type:'agenda' {days:[{dow,dom,busy:'16 hex nibbles',n,first,last,free}]} ≈ 7×40 B; the firmware chart card could draw the strips |
| `reading` | type:'reading' {title, author, page, pages, pace, finish, per_day:[14], note} ≈ 220 B |
| `sudoku` | type:'sudoku' {grid:'81 chars', difficulty, code} = 100 B — the firmware could draw it from a string |
| `clocks` | type:'clocks' {rows:[{label,time,offset,awake}], count:[24], now} ≈ 300 B |
| `markets` | type:'markets' {rows:[{label,last,change_pct,spark:[24],pos52}]} ≈ 400 B |
| `year` | type:'year' {ordinal, bits:'46 B past/event mask'} — the ESP32 could draw 365 dots itself |
| `chess` | type:'chess' {fen:'…', last:'h2g3', goal:'Mate in 2'} ≈ 90 B — the board is 64 squares and 12 glyphs |
| `playing` | type:'playing' {track, artist, progress, state} + `image <art url>` ≈ 120 B; the sleeve is the only heavy part |
| `tide` | type:'tide' {pts:'48×u8', now, hi:[…], lo:[…]} ≈ 70 B — a polyline the ESP32 can fill itself |
| `almanac` | type:'list' with year prefixes ≈ 600 B of text — no geometry, the ESP32 can set this itself |
| `departures` | type:'departures' {stop, rows:[{line, dest, mins:[…]}]} ≈ 160 B — roundels are a circle + one glyph |
| `budget` | type:'budget' {spent, pace, budget, cum:[u16×31]} ≈ 70 B — a stepped polyline + one diagonal |
| `printer` | type:'printer' {pct, layer, layers, eta, temps:[3×u16], spools:[4×u8]} ≈ 40 B — bars and arcs the ESP32 draws itself |
