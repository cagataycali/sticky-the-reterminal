import { Receipt } from './Receipt'
import { useState } from 'react'
import { invoke } from '../lib'
import type { Caps } from '../App'

export const PAGES = [
  { t: 'home', label: 'home' },
  { t: 'status', label: 'status' },
  { t: 'sensors', label: 'sensors' },
  { t: 'settings', label: 'settings' },
  { t: 'wifi', label: 'wifi' },
  { t: 'ble', label: 'ble' },
]
export const NAV = [
  { t: 'back', label: '← back' },
  { t: 'prev', label: '‹ prev' },
  { t: 'next', label: 'next ›' },
]

export function NavCard({ caps, onAction }: { caps: Caps; onAction: () => void }) {
  const [msg, setMsg] = useState('')
  const [rot, setRot] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const pageOk = caps.map['page']?.available !== false
  const rotOk = caps.map['rotate']?.available !== false
  const go = async (target: string) => {
    setBusy(true)
    setMsg('…')
    try {
      const r = await invoke('page', { target }, 45)
      setMsg(r.pending ? `… queued ${r.envelope_id}` : `✓ ${String(r.result).slice(0, 120)}`)
      onAction()
    } catch (e: any) {
      setMsg(`✗ ${String(e?.message || e)}`)
    } finally {
      setBusy(false)
    }
  }
  const rotate = async (deg: string | null) => {
    setBusy(true)
    setMsg('…')
    try {
      const r = await invoke('rotate', deg === null ? undefined : { deg }, 45)
      if (r.rotate) setRot(r.rotate)
      setMsg(r.pending ? `… queued ${r.envelope_id}` : `✓ ${String(r.result).slice(0, 140)}`)
      if (deg !== null) onAction()
    } catch (e: any) {
      setMsg(`✗ ${String(e?.message || e)}`)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="card">
      <h3>Pages</h3>
      <div className="actions navring">
        {PAGES.map((p) => (
          <button key={p.t} disabled={busy || !pageOk} onClick={() => go(p.t)}>{p.label}</button>
        ))}
        {NAV.map((p) => (
          <button key={p.t} className="ghost" disabled={busy || !pageOk} onClick={() => go(p.t)}>{p.label}</button>
        ))}
      </div>
      <h4 className="dim" title="Each button lands on the glass; the mirror refreshes after.">Rotation</h4>
      <div className="actions navring">
        {['0', '90', '180', '270'].map((d) => (
          <button key={d} className={rot && String(rot.rotation) === d && rot.mode === 'manual' ? '' : 'ghost'}
            disabled={busy || !rotOk} onClick={() => rotate(d)}>{d}°</button>
        ))}
        <button className={rot?.mode === 'auto' ? '' : 'ghost'} disabled={busy || !rotOk} onClick={() => rotate('auto')}
          title="hand the decision back to gravity (IMU)">auto</button>
        <button className="ghost" disabled={busy || !rotOk} onClick={() => rotate(null)} title="report current rotation without changing it">?</button>
      </div>
      {rot && (
        <p className="dim tiny meta-line" title={rot.imu_last ? `IMU says ${rot.imu_last}` : undefined}>
          {rot.rotation}° · {rot.mode}
        </p>
      )}
      <Receipt msg={msg} />
    </section>
  )
}

export const ACTIONS: {
  cmd: string; args?: any; label: string; confirm: string; group: 'speak' | 'device' | 'danger'
  prompt?: string; numPrompt?: string; numDefault?: number; numOptional?: boolean
}[] = [
  { cmd: 'ask', group: 'speak', label: 'Ask tiny', confirm: '', prompt: 'What should tiny answer on the panel?' },
  { cmd: 'say', group: 'speak', label: 'Say', confirm: '', prompt: 'Text to put on the panel:' },
  { cmd: 'voice', group: 'speak', args: 6, label: 'Voice ask · 6 s', confirm: 'Record 6 s on the device mic and answer it on the panel?' },
  { cmd: 'sensors', group: 'device', label: 'Read sensors', confirm: 'Read SHT40/IMU/RTC?' },
  { cmd: 'miccheck', group: 'device', label: 'Mic check', confirm: '', numPrompt: 'Seconds to record (1–10)? RMS/peak is reported, nothing uploads.', numDefault: 2 },
  { cmd: 'sleep', group: 'device', label: 'Sleep', confirm: '', numPrompt: 'Deep sleep — timer-wake seconds (empty = only the AI button wakes it):', numOptional: true },
  { cmd: 'wake', group: 'device', label: 'Wake', confirm: 'Wake the device?' },
  { cmd: 'ota', group: 'danger', args: { channel: 'sticky-dev' }, label: 'OTA update', confirm: 'Stage an OTA update from sticky-dev? The device trial-boots and self-reverts on failure.' },
  { cmd: 'reprovision', group: 'danger', label: 'Reprovision Wi-Fi', confirm: 'Wipe Wi-Fi credentials and reopen the tiny-XXXX portal? The device goes OFFLINE.' },
]

export function ActionsCard({ caps, onAction }: { caps: Caps; onAction: () => void }) {
  const [msg, setMsg] = useState('')
  const [mic, setMic] = useState<any>(null)
  const run = async (a: (typeof ACTIONS)[number]) => {
    let args = a.args
    if (a.prompt) {
      const v = window.prompt(a.prompt)
      if (!v) return
      args = { text: v }
    } else if (a.numPrompt) {
      const v = window.prompt(a.numPrompt, a.numDefault != null ? String(a.numDefault) : '')
      if (v === null) return
      const n = parseInt(v, 10)
      if (Number.isFinite(n)) args = { seconds: n }
      else if (!a.numOptional) return
      if (a.cmd === 'sleep' && !window.confirm(Number.isFinite(n)
        ? `Deep sleep — wakes after ${n} s or on the AI button?`
        : 'Deep sleep — ONLY the AI button wakes it. Sure?')) return
    } else if (!window.confirm(a.confirm)) return
    setMsg('…')
    if (a.cmd === 'miccheck') setMic(null)
    try {
      const r = await invoke(a.cmd, args, a.cmd === 'voice' || a.cmd === 'ask' ? 120 : 60)
      if (r.miccheck) setMic(r.miccheck)
      setMsg(r.pending ? `… queued ${r.envelope_id}` : `✓ ${String(r.result).slice(0, 160)}`)
      onAction()
    } catch (e: any) {
      setMsg(`✗ ${String(e?.message || e)}`)
    }
  }
  return (
    <section className="card">
      <h3 title={(caps.advertised ? 'Availability follows the device\'s own heartbeat; greyed verbs are refused, not pretended.' : 'Heartbeat unreadable — availability from the static table.') + (Object.entries(caps.map).some(([, c]) => c.new && c.available) ? ` Also advertised, no button yet: ${Object.entries(caps.map).filter(([, c]) => c.new && c.available).map(([k]) => k).join(', ')}.` : '')}>Actions</h3>
      {(['speak', 'device', 'danger'] as const).map((g) => (
        <div className="actrow" key={g}>
          <span className="actlabel">{g === 'speak' ? 'Panel' : g === 'device' ? 'Device' : 'Careful'}</span>
          <div className="actions">
            {ACTIONS.filter((a) => a.group === g).map((a) => {
              const cap = caps.map[a.cmd]
              const ok = cap?.available !== false
              return (
                <button
                  key={a.cmd}
                  disabled={!ok}
                  className={g === 'danger' ? 'ghost' : ''}
                  title={ok ? a.confirm || a.prompt : `unavailable: ${cap?.reason || 'not advertised by this device'}`}
                  onClick={() => ok && run(a)}
                >
                  {a.label}
                </button>
              )
            })}
          </div>
        </div>
      ))}
      <Receipt msg={msg} />
    </section>
  )
}
