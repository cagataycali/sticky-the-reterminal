export const CARD_PRESETS: Record<string, any> = {
  text: { type: 'text', title: 'Note', body: 'Back at 19:30 — pizza in fridge' },
  list: { type: 'list', title: 'Groceries', items: ['milk', 'eggs', 'bread'] },
  kv: { type: 'kv', title: 'House', rows: { inside: '22.4°C', humidity: '48%', battery: '84%' } },
  chart: { type: 'chart', title: 'Battery — today', data: [100, 98, 97, 95, 92, 90, 88, 87], labels: ['9a', '12p', '3p', '6p'], style: 'line', unit: '%', y_min: 0, y_max: 100 },
  buttons: {
    type: 'text',
    title: 'Dinner?',
    body: 'Pizza is in the fridge — heat 12 min at 180°C.',
    buttons: [{ id: 'ok', label: 'Got it' }, { id: 'call', label: 'Call me' }],
    footer: 'sent from sticky.cagatay.my',
  },
  composite: {
    type: 'composite',
    title: 'Evening',
    parts: [
      { type: 'text', body: 'Two things left today.' },
      { type: 'list', items: ['bins out', 'reply to Zeynep'] },
      { type: 'buttons', buttons: [{ id: 'done', label: 'Done' }, { id: 'later', label: 'Later' }] },
    ],
  },
  qr: {
    type: 'qr',
    card_id: 'guest-wifi',
    text: 'WIFI:S:Verizon_Guest;T:WPA;P:example-pass;;',
    caption: 'guest wifi — point a phone camera here',
  },
  menu: {
    type: 'menu',
    title: 'Pick one',
    card_id: 'demo-menu',
    items: [
      { label: 'Show sensors', id: 'sensors', note: 'SHT40 + IMU' },
      { label: 'Sleep 60 s', id: 'sleep60' },
      'plain rows are fine too',
    ],
  },
  keyboard: {
    type: 'keyboard',
    card_id: 'kb-demo',
    title: 'Reply to tiny',
    value: 'hello from the dash',
    shift: false,
  },
  scrollable: {
    type: 'list',
    card_id: 'scroll-demo',
    title: 'Scroll demo — 24 rows, ~880 px of content',
    items: Array.from({ length: 24 }, (_, i) => `row ${i + 1} of 24 — overflow makes the glass scrollable`),
    footer: 'drive me with the mirror’s ⤒▲▼',
  },
  alert: {
    type: 'text',
    card_id: 'alert-demo',
    priority: 'alert',
    title: 'Alert',
    body: 'Front-door sensor battery at 5% — replace today.',
    buttons: [{ id: 'ack', label: 'Ack' }],
    footer: 'priority:"alert" → “! ” title prefix on glass (fw ≥0.14.4)',
  },
}

// ── card-spec v1 lint: non-blocking warnings, mirrors what the firmware
// actually parses (grammar v1). Never blocks send — the glass's receipt
// is the final judge; this just catches typos before a device round-trip.
// LINT_GRAMMAR = the verb-grammar generation these rules were written against
// (fw's grammar_version gates verbs + button-id namespaces, NOT card types —
// card types are unchanged since v1). v2 (fw 0.14.11) = the messages dialect:
// the `messages` verb family + the `m:` button-id namespace (m:inbox /
// m:t:<login> / m:<n>), exact-dispatched ahead of token sniffing like k:/w:.
// v11 (fw 0.27.0-u2, re-verified 2026-09-07 against firmware/main/tiny/*.cpp): card
// types text list kv composite qr menu keyboard chart chat image gallery
// agent_home (+ nested buttons); shell-owned button-id prefixes k: w: m: u:.
// fwGrammar = what the device last REPORTED; on drift, every lint says so first.
export const LINT_GRAMMAR = 11
export let fwGrammar: number | null = null
export function setFwGrammar(v: number | null) { fwGrammar = v }
export const CARD_TYPES = ['text', 'list', 'kv', 'composite', 'qr', 'menu', 'keyboard', 'chart', 'chat', 'image', 'gallery', 'agent_home']
export const CARD_KEYS = new Set(['type', 'card_id', 'title', 'body', 'items', 'rows', 'parts', 'buttons', 'footer', 'text', 'caption', 'value', 'shift', 'priority', 'data', 'labels', 'style', 'unit', 'y_min', 'y_max'])
// Faithful mirror of fw tiny_touch.cpp action_for()/token_match() @0.14.6:
// token_match is a case-insensitive SUBSTRING scan (no word boundaries — id
// "sorted" trips "sor" → microphone). Sniff target = id when non-empty, else
// label. First match wins, in firmware order.
export const SNIFF_TOKENS: [string, string][] = [
  ['ask', '🎤 opens the microphone (voice ask)'], ['sor', '🎤 opens the microphone (voice ask)'],
  ['status', 'opens the Status page'], ['durum', 'opens the Status page'],
  ['sensor', 'opens the Sensors page'], ['sens', 'opens the Sensors page'],
  ['home', 'goes Home'], ['ana ekran', 'goes Home'],
  ['settings', 'opens Settings'], ['ayarlar', 'opens Settings'],
  ['back', 'navigates Back'], ['geri', 'navigates Back'],
  ['rescan ble', 'rescans Bluetooth'], ['bluetooth', 'rescans Bluetooth'], ['ble', 'rescans Bluetooth'],
  ['rescan', 'rescans Wi-Fi'],
  ['wi-fi', 'opens the Wi-Fi page'], ['wifi', 'opens the Wi-Fi page'],
]
export function deviceActionFor(id?: string, label?: string): { action: string; token: string } | null {
  if (id && /^[kwmu]:/.test(id)) return null // shell-owned prefixes (k: w: m: u:), exact-dispatch
  if (id === 'noop') return null
  const sniff = (id && id.length ? id : label || '').toLowerCase()
  if (!sniff) return null
  for (const [tok, action] of SNIFF_TOKENS) if (sniff.includes(tok)) return { action, token: tok }
  return null
}

// Approximate the firmware's ascii_fold output length (bytes). ASCII is 1:1;
// the fold's multi-char expansions are the only growth: ✓→"[ok]", ✗→"[x]",
// ½¼¾→"1/2", …→"...", ß/æ/œ→2. Everything else folds to one byte (or '?').
// draw_wrapped folds into a static 1024-byte buffer PER FIELD (body, each
// list item, each composite part body) and cuts SILENTLY past it — fw bug
// filed (docs/cards.md); until the 2bpp font lands this lint is the warning.
export const FOLD_BUDGET = 1024
export function foldedLen(s: string): number {
  let n = 0
  for (const ch of s) {
    if (ch.codePointAt(0)! < 0x80) n += 1
    else if (ch === '\u2713') n += 4
    else if (ch === '\u2717' || ch === '\u00bd' || ch === '\u00bc' || ch === '\u00be' || ch === '\u2026') n += 3
    else if (ch === '\u00df' || ch === '\u00e6' || ch === '\u00c6' || ch === '\u0153' || ch === '\u0152') n += 2
    else n += 1
  }
  return n
}

export function lintCard(spec: any, nested = false): string[] {
  const w: string[] = []
  if (!nested && fwGrammar != null && fwGrammar !== LINT_GRAMMAR)
    w.push(`⚠ grammar drift: device reports grammar v${fwGrammar} but these lint rules mirror v${LINT_GRAMMAR} — warnings below may be stale and new fw card features invisible; the glass's receipt is the judge`)
  if (typeof spec !== 'object' || spec == null || Array.isArray(spec)) return [...w, 'spec must be a JSON object']
  const t = spec.type
  if (!t) w.push('missing "type"')
  else if (!CARD_TYPES.includes(t) && !(nested && t === 'buttons')) w.push(`unknown type "${t}" (firmware knows: ${CARD_TYPES.join(', ')})`)
  if (t === 'text' && !spec.body && !spec.title) w.push('text card with neither body nor title renders blank')
  if (typeof spec.body === 'string' && foldedLen(spec.body) > FOLD_BUDGET)
    w.push(`body is ~${foldedLen(spec.body)} bytes after fold — the glass cuts it SILENTLY at ${FOLD_BUDGET} (fw bug, filed); split into composite parts or several cards`)
  if (Array.isArray(spec.items))
    spec.items.forEach((it: any, i: number) => {
      if (typeof it === 'string' && foldedLen(it) > FOLD_BUDGET)
        w.push(`items[${i}] is ~${foldedLen(it)} bytes after fold — silently cut at ${FOLD_BUDGET} on the glass`)
    })
  if (t === 'list' && !Array.isArray(spec.items)) w.push('list needs items: []')
  if (t === 'kv' && (typeof spec.rows !== 'object' || Array.isArray(spec.rows))) w.push('kv needs rows: {k: v}')
  if (t === 'qr' && !spec.text) w.push('qr needs text (the encoded payload)')
  if (t === 'menu' && !Array.isArray(spec.items)) w.push('menu needs items: []')
  if (t === 'chart') {
    const d = spec.data
    if (!Array.isArray(d) || d.length < 2 || d.some((v: any) => typeof v !== 'number'))
      w.push('chart needs data: [2+ numbers] — the glass renders an honest void otherwise')
    else if (d.length > 160) w.push(`data has ${d.length} points — firmware caps at 160 (~4px/point)`)
    if (spec.style != null && !['line', 'bar'].includes(spec.style)) w.push(`style "${spec.style}" — firmware knows "line" and "bar"`)
    if (spec.y_min == null && spec.y_max == null && Array.isArray(d) && d.length >= 2) {
      const span = Math.max(...d) - Math.min(...d)
      if (span !== 0 && span < Math.abs(Math.max(...d)) * 0.05)
        w.push('auto-scale will stretch a narrow range across the full height (87→86 renders as a cliff) — pin y_min/y_max for slow-moving values like battery%')
    }
  }
  if (t === 'composite') {
    if (!Array.isArray(spec.parts)) w.push('composite needs parts: []')
    else spec.parts.forEach((p: any, i: number) => lintCard(p, true).forEach((x) => w.push(`parts[${i}]: ${x}`)))
  }
  if (spec.buttons != null) {
    if (!Array.isArray(spec.buttons)) w.push('buttons must be an array')
    else {
      if (spec.buttons.length > 4) w.push(`${spec.buttons.length} buttons — firmware draws at most 4`)
      spec.buttons.forEach((b: any, i: number) => {
        if (typeof b !== 'string' && !b?.id) w.push(`buttons[${i}] has no id — its tap can't be attributed (plain strings are ok)`)
        const id = typeof b === 'string' ? undefined : b?.id
        const label = typeof b === 'string' ? b : b?.label
        if (id && /^m:/.test(id)) w.push(`buttons[${i}] id "${id}" → messages app exact-dispatch on-device (grammar v2): m:inbox opens the inbox, m:t:<login> opens that thread — label text is not sniffed`)
        const hit = deviceActionFor(id, label)
        if (hit) w.push(`buttons[${i}] ⚡ tap ALSO ${hit.action} on-device — fw sniffs "${hit.token}" as a substring of the ${id ? 'id' : 'label'} "${id || label}"; use id "noop" (or a neutral id) for a report-only button`)
      })
    }
  }
  if (spec.priority != null && !['alert', 'normal'].includes(spec.priority)) w.push(`priority "${spec.priority}" — grammar v1 knows "alert" and "normal"`)
  if (spec.card_id != null && typeof spec.card_id !== 'string') w.push('card_id should be a string')
  if (!nested) for (const k of Object.keys(spec)) if (!CARD_KEYS.has(k)) w.push(`unknown key "${k}" — firmware ignores it`)
  return w
}
