import { useEffect, useState } from 'react'
import { api } from '../lib'
import { setDevice, setFleet, useDevice, type DeviceRow } from '../store'
import { fmtAge } from '../hooks'

/** The fleet's Stickies — one row each; click (or ↑/↓ + Enter) selects and
 *  rewrites `?device=`. Polls /api/devices every 30 s; never a device
 *  round-trip (the backend answers from its fleet cache). */
export function DeviceRail() {
  const sel = useDevice()
  const [rows, setRows] = useState<DeviceRow[] | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    let alive = true
    const load = () => api('/api/devices').then((r) => {
      if (!alive) return
      setRows(r.devices || []); setFleet(r.devices || []); setErr('')
    }).catch((e) => alive && setErr(String(e?.message || e)))
    load()
    const t = setInterval(load, 30000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  const isSel = (d: DeviceRow) => sel ? (d.id === sel || d.name === sel) : !!d.default
  const onKey = (e: React.KeyboardEvent, i: number) => {
    if (!rows) return
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') { e.preventDefault(); setDevice(rows[(i + 1) % rows.length].name) }
    if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') { e.preventDefault(); setDevice(rows[(i - 1 + rows.length) % rows.length].name) }
  }
  useEffect(() => {
    // move focus with the selection so arrow keys keep working
    const el = document.querySelector<HTMLElement>('.rail [aria-current="true"]')
    if (el && document.activeElement?.closest('.rail')) el.focus()
  }, [sel])

  return (
    <nav className="rail" aria-label="Stickies">
      <div className="rail-label">devices</div>
      {err && <p className="err small">{err}</p>}
      {rows === null && !err && <p className="dim small">finding your Stickies…</p>}
      {rows && rows.length === 0 && <p className="dim small">no Sticky on this account yet</p>}
      {rows?.map((d, i) => (
        <button key={d.id} className={'rail-item' + (isSel(d) ? ' on' : '')} aria-current={isSel(d) ? 'true' : undefined}
          onClick={() => setDevice(d.name)} onKeyDown={(e) => onKey(e, i)} tabIndex={isSel(d) || (!sel && i === 0) ? 0 : -1}
          title={d.id} aria-label={`${d.name}, ${d.online ? "online" : "asleep"}`}>
          <span className={'dot ' + (d.online ? 'on' : 'off')} aria-hidden="true" />
          <span className="rail-name">{d.name}</span>
          <span className="rail-meta">
            {d.online ? 'online' : d.age_s != null ? `asleep · ${fmtAge(d.age_s)} ago` : 'never seen'}
            {d.battery_pct != null ? ` · ${d.battery_pct}%${d.charging ? ' charging' : ''}` : ''}
            {d.fw ? ` · ${d.fw}` : ''}
          </span>
        </button>
      ))}
    </nav>
  )
}
