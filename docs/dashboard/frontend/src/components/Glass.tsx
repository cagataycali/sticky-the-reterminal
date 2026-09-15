import { Receipt } from './Receipt'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Bezel } from './Bezel'
import { useSelectedRow } from '../store'
import { fmtAge, useNow } from '../hooks'
import { api, token } from '../lib'
import { deviceActionFor } from '../cards'
import type { Caps } from '../App'

export function GlassBanner() {
  // Another client (a verdict card, a premiere) may have ANNOUNCED a
  // window on the physical panel — paint buttons 409 while it's open, so say
  // WHY up front instead of letting them fail mysteriously. Polls 15s.
  const [glass, setGlass] = useState<any | null>(null)
  useEffect(() => {
    let alive = true
    const poll = () => api('/api/glass').then((r) => alive && setGlass(r)).catch(() => {})
    poll()
    const t = setInterval(poll, 15000)
    return () => { alive = false; clearInterval(t) }
  }, [])
  if (!glass?.busy) return null
  return (
    <p className="warn modebar" title={glass.note}>
      🚦 glass busy — {String(glass.window?.text || '').slice(0, 140)}
      <span className="dim tiny"> (paints refuse with 409 until this window closes)</span>
    </p>
  )
}

export function PocketRow() {
  // Pocket lockout, owner-facing: lock/unlock/glance are real advertised
  // verbs. lock/unlock/glance paint → they respect glass etiquette (409 shows
  // as the message). Unlock-from-web is deliberate: the chord (UP+DOWN 1s)
  // stays the on-device rail; this is the couch rail.
  const [msg, setMsg] = useState('')
  const send = async (verb: string) => {
    setMsg(`${verb}…`)
    try {
      const r = await api('/api/invoke', { method: 'POST', body: JSON.stringify({ command: verb }) })
      setMsg(`✓ ${verb}${r?.result ? ` — ${String(r.result).slice(0, 90)}` : ''}`)
    } catch (e: any) {
      setMsg(`✗ ${verb}: ${String(e?.message || e).slice(0, 120)}`)
    }
  }
  return (
    <p>
      <button className="ghost" title="pocket lockout: buttons+mic+touch refuse until unlocked" onClick={() => send('lock')}>lock</button>{' '}
      <button className="ghost" title="remote unlock (on-device chord: UP+DOWN 1s)" onClick={() => send('unlock')}>unlock</button>{' '}
      <button className="ghost" title="glance card: time/battery/unread — partial refresh" onClick={() => send('glance')}>glance</button>{' '}
      <Receipt msg={msg} inline />
    </p>
  )
}

export function MirrorCard({ bump, caps, onAction }: { bump: number; caps: Caps; onAction: () => void }) {
  const row = useSelectedRow()
  const now = useNow()
  const asleep = row ? !row.online : false
  const [src, setSrc] = useState('')
  const [prev, setPrev] = useState('')   // the last frame stays under the new one until it has loaded
  const [meta, setMeta] = useState<any>(null)
  const [buttons, setButtons] = useState<any[]>([])
  const [btnNote, setBtnNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [tapMsg, setTapMsg] = useState('')

  const shoot = useCallback(async () => {
    setBusy(true)
    setErr('')
    try {
      // real path: `screenshot` verb → device uploads its framebuffer to
      // plugin.tiny.technology/media/… → backend proxies it at /api/screen.png
      const r = await api('/api/screenshot', { method: 'POST' })
      setMeta(r)
      if (r.pending) {
        setErr('device has not answered the screenshot envelope yet — try again in a few seconds')
      } else {
        const u = r.proxy_url || '/api/screen.png'
        setSrc(token() ? `${u}${u.includes('?') ? '&' : '?'}token=${token()}` : u)
      }
      const b = await api('/api/buttons').catch(() => ({ buttons: [] }))
      setButtons(b.buttons || [])
      setBtnNote(b.note || b.source || '')
    } catch (e: any) {
      setErr(String(e?.message || e))
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    shoot()
  }, [shoot, bump])
  // keyboard `r` (see Shortcuts) asks for a frame the same way the button does
  useEffect(() => {
    const h = () => { shoot(); onAction() }
    window.addEventListener('dash:refresh', h)
    return () => window.removeEventListener('dash:refresh', h)
  }, [shoot, onAction])
  // remember the outgoing frame when a new URL arrives
  const lastSrc = useRef('')
  useEffect(() => {
    if (lastSrc.current && lastSrc.current !== src) setPrev(lastSrc.current)
    lastSrc.current = src
  }, [src])

  const tap = async (b: any) => {
    setTapMsg('')
    try {
      const r = await api('/api/tap', { method: 'POST', body: JSON.stringify({ button_id: b.id }) })
      setTapMsg(r.simulated ? `⚠ simulated tap on “${b.label || b.id}” — the panel did not feel it` : `✓ ui_tap ${b.id}`)
    } catch (e: any) {
      setTapMsg(`✗ ${String(e?.message || e)}`)
    }
    onAction()
  }

  // ── scroll (fw ≥0.14.3): `scroll <px>` = absolute offset, JSON receipt
  // {offset, content_h, view_h, scrollable} — we mirror the glass's own numbers
  const [scr, setScr] = useState<any>(null)
  const [scrMsg, setScrMsg] = useState('')
  const doScroll = async (dir: 'top' | 'up' | 'down') => {
    setScrMsg('…')
    const view = scr?.view_h ?? 356
    const max = scr ? Math.max(0, (scr.content_h ?? 0) - view) : 99999
    const cur = scr?.offset ?? 0
    const target =
      dir === 'top' ? 0 : dir === 'up' ? Math.max(0, cur - Math.round(view * 0.8)) : Math.min(max, cur + Math.round(view * 0.8))
    try {
      const r = await api('/api/invoke', {
        method: 'POST',
        // fw quirk (0.14.x): `scroll 0` is a REPORT (atoi sentinel) — offset 0
        // is only reachable via `scroll top`, so send the word for any 0 target
        body: JSON.stringify({ command: 'scroll', args: target === 0 ? 'top' : target, wait_s: 30 }),
      })
      if (r.pending) {
        setScrMsg('⏳ scroll queued — the receipt will land in Activity when the device answers')
      } else if (r.scroll) {
        setScr(r.scroll)
        setScrMsg(
          r.scroll.scrollable
            ? `${r.scroll.offset}/${Math.max(0, r.scroll.content_h - r.scroll.view_h)} px of ${r.scroll.content_h}`
            : 'card fits the panel — nothing to scroll'
        )
        shoot() // refresh the frame so the mirror shows what the glass shows
      } else {
        setScrMsg(String(r.result || '').slice(0, 80))
      }
    } catch (e: any) {
      setScrMsg(`✗ ${String(e?.message || e)}`)
    }
    onAction()
  }

  return (
    <section className="card glass-card">
      <h2>
        <span>{row?.name || 'glass'}</span>
        {row && <span className={'dot ' + (row.online ? 'on' : 'off')} aria-hidden="true" />}
        {row && <span className="tiny">{row.online ? 'online' : row.age_s != null ? `asleep · last seen ${fmtAge(row.age_s)} ago` : 'never seen'}</span>}
        <span className="grow" />
        {meta?.ts && (
          <span className="tiny" title={`${new Date(meta.ts * 1000).toLocaleString()} · panel ${meta.rotation ?? 0}°${meta.rotation == null ? ' (assumed)' : ''} · rotation source ${meta.rotation_source || 'n/a'}`}>
            frame {fmtAge(Math.max(0, (now / 1000) - meta.ts))} ago{meta.rotation ? ` · ${meta.rotation}°` : ''}
          </span>
        )}
        <button className="ghost" disabled={busy} onClick={() => { shoot(); onAction() }} title="ask the glass for a fresh frame (one device round-trip)">
          {busy ? 'shooting…' : 'Refresh'}
        </button>
        <span className="navring">
          <button className="ghost" title="scroll to top (scroll 0)" onClick={() => doScroll('top')}>⤒</button>
          <button className="ghost" title="scroll up ~80% of a view" onClick={() => doScroll('up')}>▲</button>
          <button className="ghost" title="scroll down ~80% of a view" onClick={() => doScroll('down')}>▼</button>
        </span>
        {scrMsg && <span className="tiny">{scrMsg}</span>}
      </h2>
      {err && <p className="err">{err}</p>}
      {(() => {
        const rot = Number(meta?.rotation ?? 0) || 0
        const quarter = rot % 180 !== 0
        // portrait: the 800×480 panel space is rotated inside the standing
        // bezel's screen box (aspect 480:800) — width 166.667 % / height 60 %
        // makes it fill exactly; taps use the img's LOCAL offsets, so the
        // mapping is rotation-proof.
        const style = quarter
          ? { position: 'absolute' as const, left: '-33.333%', top: '20%', width: '166.667%', height: '60%', transform: `rotate(${rot}deg)` }
          : rot ? { transform: `rotate(${rot}deg)` } : undefined
        return (
      <div className="glass-wrap" title={buttons.length === 0 && btnNote ? btnNote : undefined}>
      <Bezel rotation={rot} asleep={asleep}>
      {src ? (
        <div
          className={'mirror' + (busy ? ' busy' : '')}
          style={style}
        >
          {prev && prev !== src && <img className="frame prev" src={prev} alt="" aria-hidden="true" />}
          <img
            key={src}
            className="frame"
            src={src}
            alt="sticky screen"
            onLoad={() => { window.setTimeout(() => setPrev(''), 320) }}
            style={{ cursor: 'crosshair' }}
            title="click = a real tap at that panel coordinate"
            onClick={async (e) => {
              // offsetX/Y are in the element's LOCAL (pre-CSS-transform) space,
              // so this mapping is rotation-proof: the container may be rotated
              // (slice 10) but the img's own box is always 800×480 panel space.
              const el = e.currentTarget
              const x = Math.max(0, Math.min(799, Math.round((e.nativeEvent.offsetX / el.clientWidth) * 800)))
              const y = Math.max(0, Math.min(479, Math.round((e.nativeEvent.offsetY / el.clientHeight) * 480)))
              setTapMsg(`tapping ${x},${y}…`)
              try {
                const r = await api('/api/invoke', { method: 'POST', body: JSON.stringify({ command: 'tap', args: { x, y } }) })
                const routed = r?.routed
                setTapMsg(routed === false
                  ? `⚠ tap ${x},${y} — device took it but still routing (not lost)`
                  : `✓ real tap ${x},${y}${r?.result ? ` — ${String(r.result).slice(0, 80)}` : ''}`)
              } catch (err: any) {
                setTapMsg(`✗ tap ${x},${y}: ${String(err?.message || err).slice(0, 120)}`)
              }
            }}
          />
          {buttons.map((b) => {
            const [x0, y0, x1, y1] = b.bbox
            const fx = deviceActionFor(b.id, b.label)
            return (
              <button
                key={b.id}
                className={(caps.taps === 'real' ? 'tapzone' : 'tapzone sim') + (fx ? ' fx' : '')}
                title={
                  (caps.taps === 'real' ? `tap ${b.id}` : `simulated tap: ${b.label || b.id}`) +
                  (fx ? ` — ⚡ a real finger here ALSO ${fx.action} (fw sniffs "${fx.token}")` : '')
                }
                style={{
                  left: `${(x0 / 800) * 100}%`,
                  top: `${(y0 / 480) * 100}%`,
                  width: `${((x1 - x0) / 800) * 100}%`,
                  height: `${((y1 - y0) / 480) * 100}%`,
                }}
                onClick={() => tap(b)}
              >
                {fx ? <span className="fxmark">⚡</span> : null}
              </button>
            )
          })}
        </div>
      ) : (
        <div className="empty">{asleep ? `asleep · last seen ${row?.age_s != null ? fmtAge(row.age_s) : '?'} ago — no frame cached yet` : err ? 'the glass did not answer — see the note below' : busy ? 'asking the glass for its frame…' : 'no frame yet — press refresh'}</div>
      )}
      </Bezel>
      </div>
        )
      })()}
      {buttons.length > 0 && (
        <p className="dim tiny" title={caps.taps === 'real' ? 'a click sends a real ui_tap' : 'logged as sim_tap — only a finger on the glass makes the device act'}>
          {caps.taps === 'real'
            ? `${buttons.length} tappable ${buttons.length === 1 ? 'button' : 'buttons'} — clicks reach the glass`
            : `${buttons.length} ${buttons.length === 1 ? 'button' : 'buttons'} from the last card — clicks here are simulated`}
        </p>
      )}
      <Receipt msg={tapMsg} />
    </section>
  )
}
