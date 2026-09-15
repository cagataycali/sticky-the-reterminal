import { useEffect, useState } from 'react'
import { api } from '../lib'
import { describe } from '../hooks'
import type { Caps } from '../App'

export function ActivityCard({ bump, caps }: { bump: number; caps: Caps }) {
  const [items, setItems] = useState<any[]>([])
  const [inFlight, setInFlight] = useState<any[]>([])
  const [note, setNote] = useState('')
  useEffect(() => {
    let dead = false
    const run = () =>
      api('/api/activity?limit=40')
        .then((r) => {
          if (dead) return
          setItems(r.activity || [])
          setInFlight(r.in_flight || [])
          setNote(r.events_error ? `event ring unreachable: ${r.events_error}` : '')
        })
        .catch(() => {})
    run()
    const t = setInterval(run, 5000)
    return () => {
      dead = true
      clearInterval(t)
    }
  }, [bump])
  return (
    <section className="card wide">
      <h3 title={caps.mode === 'live' ? 'ui_tap is a finger on the glass; sim_tap is a click in the mirror.' : 'Mock: ui_tap entries come from clicking the mirror.'}>
        Activity{' '}
        <button
          className="ghost"
          title="download the current receipts as JSON — evidence you can hand to the firmware side"
          onClick={() => {
            const blob = new Blob(
              [JSON.stringify({ exported_at: new Date().toISOString(), source: 'sticky.cagatay.my dashboard', mode: caps.mode, in_flight: inFlight, activity: items }, null, 2)],
              { type: 'application/json' },
            )
            const a = document.createElement('a')
            a.href = URL.createObjectURL(blob)
            a.download = `sticky-session-${new Date().toISOString().replace(/[:.]/g, '-')}.json`
            a.click()
            URL.revokeObjectURL(a.href)
          }}
        >
          export
        </button>
      </h3>
      {note && <p className="warn tiny">{note}</p>}
      {inFlight.length > 0 && (
        <p className="receipt pending"><span className="receipt-dot" aria-hidden="true" /><span className="receipt-text">
          {inFlight.length} in flight — {summariseInFlight(inFlight)}
        </span></p>
      )}
      <div className="feed" tabIndex={0} aria-label="activity log">
        {items.length === 0 && <p className="dim">nothing yet</p>}
        {items.map((it, i) => (
          <div key={it.event_id ? `e${it.event_id}` : i} className="feeditem">
            <span className="ts">{new Date(it.ts * 1000).toLocaleTimeString([], { hour12: false })}</span>
            <span className={`tag ${it.kind}`}>{it.kind}</span>
            <span className="body" title={it.result != null && typeof it.result !== 'undefined' ? (typeof it.result === 'string' ? it.result : JSON.stringify(it.result)).slice(0, 600) : undefined}>{describe(it)}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

/** "status (105s) · status (105s) · screenshot (75s)" → "status ×2 · 105 s, screenshot ×3 · 75 s" */
function summariseInFlight(rows: any[]): string {
  const by = new Map<string, { n: number; age: number }>()
  for (const p of rows) {
    const k = String(p.prompt).slice(0, 40)
    const cur = by.get(k) || { n: 0, age: 0 }
    by.set(k, { n: cur.n + 1, age: Math.max(cur.age, Number(p.age_s) || 0) })
  }
  return [...by.entries()].map(([k, v]) => `${k}${v.n > 1 ? ` ×${v.n}` : ''} · ${v.age} s`).join(', ')
}
