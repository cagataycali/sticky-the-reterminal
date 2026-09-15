import { useEffect, useRef, useState } from 'react'

export function usePoll<T>(fn: () => Promise<T>, bump: number, intervalMs = 0) {
  const [data, setData] = useState<T | null>(null)
  const [err, setErr] = useState('')
  const ref = useRef(fn)
  ref.current = fn
  useEffect(() => {
    let dead = false
    const run = () =>
      ref
        .current()
        .then((r) => {
          if (dead) return
          setErr('')
          setData(r)
        })
        .catch((e) => !dead && setErr(String(e?.message || e)))
    run()
    const t = intervalMs ? setInterval(run, intervalMs) : undefined
    return () => {
      dead = true
      if (t) clearInterval(t)
    }
  }, [bump, intervalMs])
  return { data, err }
}

export function fmtAge(s: number) {
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

export function parseMaybeJson(v: any): any {
  if (typeof v === 'object') return v
  if (typeof v === 'string' && v.trim().startsWith('{')) {
    try { return JSON.parse(v) } catch { /* not json */ }
  }
  return null
}

export function describe(it: any): string {
  if (it.kind === 'ui_tap')
    return `${it.source === 'device' ? 'finger on the glass' : 'tap'} — “${it.label || it.button_id}”${it.card_id ? ` on ${it.card_id}` : ''}`
  if (it.kind === 'sim_tap') return `simulated tap “${it.label || it.button_id}” — the device did not feel it`
  if (it.kind === 'unavailable') return `${it.prompt} refused — ${it.error}`
  if (it.kind === 'ota_guard') return `OTA guard: ${it.result} (direction-blind — receipts are the only proof)`
  if (it.kind === 'ota_verdict') {
    const v = String(it.verdict || '')
    return `OTA ${v === 'upgraded' ? 'upgraded' : v === 'DOWNGRADED' ? 'DOWNGRADED' : 'verdict'}: ${it.result}${v === 'DOWNGRADED' ? ' — stale pointer, silent rollback' : ''}`
  }
  if (it.kind === 'expired') return `${String(it.prompt || '').slice(0, 60)} — expired unanswered`
  const late = it.late ? 'late reply · ' : ''
  const p = String(it.prompt || '')
  const verb = p.split(/\s+/)[0] || p
  if (p.startsWith('scroll')) {
    const s = parseMaybeJson(it.result)
    if (s?.offset != null) {
      const max = Math.max(0, (s.content_h || 0) - (s.view_h || 0))
      const bad = s.result && s.result !== 'ESP_OK' ? ` · ${s.result}` : ''
      return `${late}${p.slice(0, 24)} → offset ${s.offset}/${max} of ${s.content_h} px${s.card_id ? ` (${s.card_id})` : ''}${bad}`
    }
  }
  if (p.startsWith('render_ui')) {
    const m = /card_id=([\w-]+)/.exec(String(it.result || ''))
    if (m) return `${late}card “${m[1]}” committed to the glass`
  }
  if (it.kind === 'pending') return `${p.slice(0, 60)} — queued, the reply lands here`
  if (it.prompt) return `${late}${p.slice(0, 70)}${it.result ? ` → ${summariseReply(verb, it.result)}` : ''}`
  return String(it.error ?? it.result ?? '')
}

/** One human sentence for a device reply — the raw text lives in the row's tooltip. */
export function summariseReply(verb: string, result: any): string {
  const raw = typeof result === 'string' ? result : JSON.stringify(result)
  const j = parseMaybeJson(result)
  if (j && typeof j === 'object' && !Array.isArray(j)) {
    if (verb === 'status') {
      const bits = [j.fw && `fw ${j.fw}`, j.battery_pct != null && `${j.battery_pct} %${j.charging ? ' charging' : ''}`, j.rssi_dbm != null && `${j.rssi_dbm} dBm`, j.heap_free != null && `heap ${Math.round(j.heap_free / 1024)} KiB`, j.sleeping ? 'asleep' : null]
      return bits.filter(Boolean).join(' · ') || 'status reply'
    }
    if (verb === 'sensors') return `${Object.keys(j).length} fields`
    if (j.result && Object.keys(j).length <= 3) return String(j.result) + (j.card_id ? ` (${j.card_id})` : '')
    const keys = Object.keys(j)
    return `${keys.length} fields: ${keys.slice(0, 4).join(', ')}${keys.length > 4 ? ', …' : ''}`
  }
  const m = /screenshot:\s*(https?:\S+)/.exec(raw)
  if (m) return 'frame landed'
  if (raw.startsWith('{')) {
    // a JSON reply the relay cut short — read what survived instead of showing the fragment
    const fw = /"fw"\s*:\s*"([^"]+)"/.exec(raw)?.[1]
    const bat = /"battery_pct"\s*:\s*(\d+)/.exec(raw)?.[1]
    const keys = [...raw.matchAll(/"([a-z_]+)"\s*:/g)].map((k) => k[1])
    const bits = [fw && `fw ${fw}`, bat && `${bat} %`].filter(Boolean)
    return `${bits.length ? bits.join(' · ') + ' · ' : ''}${keys.length} fields (reply cut short)`
  }
  if (raw.length > 90) return `${raw.slice(0, 87).replace(/\s+$/, '')}…`
  return raw
}

// ── bits ───────────────────────────────────────────────────────────────
export function Row({ k, v }: { k: string; v: any }) {
  return (
    <div className="row">
      <span className="k">{k}</span>
      <span className="v">{v === null || v === undefined || v === '' ? '—' : String(v)}</span>
    </div>
  )
}

export function fmtUptime(s?: number) {
  if (!s && s !== 0) return '—'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h ? `${h}h ${m}m` : `${m}m ${Math.floor(s % 60)}s`
}

/** Re-renders every `everyMs` so "42 s ago" labels stay honest. */
export function useNow(everyMs = 10000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), everyMs); return () => clearInterval(t) }, [everyMs])
  return now
}
