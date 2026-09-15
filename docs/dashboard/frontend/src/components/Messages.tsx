import { Receipt } from './Receipt'
import { useEffect, useState } from 'react'
import { api } from '../lib'

// ── DM rail (gate 7): the same tiny.technology inbox the glass shows.
// Inbox list is poll-safe (60 s). Opening a thread MARKS IT READ upstream —
// so it only happens on an explicit click, and the UI says so. Replies are
// sent as the human (viaTiny=false) unless the "as tiny" box is ticked.
export function MessagesCard({ mode }: { mode: 'mock' | 'live' }) {
  const [threads, setThreads] = useState<any[]>([])
  const [err, setErr] = useState('')
  const [open, setOpen] = useState<any>(null) // {peer, messages}
  const [draft, setDraft] = useState('')
  const [asTiny, setAsTiny] = useState(false)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (mode !== 'live') return
    let dead = false
    const load = () => api('/api/messages').then((r) => !dead && (setErr(''), setThreads(r.threads || []))).catch((e) => !dead && setErr(String(e?.message || e)))
    load()
    const t = setInterval(load, 60000)
    return () => { dead = true; clearInterval(t) }
  }, [mode])

  if (mode !== 'live')
    return (
      <section className="card">
        <h3>Messages</h3>
        <p className="dim tiny">mock mode has no tiny.technology inbox</p>
      </section>
    )

  const openThread = async (t: any) => {
    setMsg('')
    try {
      const r = await api(`/api/messages/${encodeURIComponent(t.login || t.userId)}`)
      setOpen(r)
    } catch (e: any) {
      setMsg(`✗ ${String(e?.message || e)}`)
    }
  }

  const send = async () => {
    if (!draft.trim() || !open?.peer) return
    setBusy(true)
    setMsg('')
    try {
      const r = await api('/api/messages', {
        method: 'POST',
        body: JSON.stringify({ to: open.peer.login || open.peer.userId, message: draft, via_tiny: asTiny }),
      })
      setMsg(`✓ delivered ${Object.entries(r.delivered || {}).filter(([, v]) => v).map(([k]) => k).join('+') || 'stored'}`)
      setDraft('')
      openThread({ login: open.peer.login, userId: open.peer.userId }) // refresh thread
    } catch (e: any) {
      setMsg(`✗ ${String(e?.message || e)}`)
    } finally {
      setBusy(false)
    }
  }

  const [glassMsg, setGlassMsg] = useState('')
  const toGlass = async (what?: string) => {
    setGlassMsg('…')
    try {
      const r = await api('/api/invoke', {
        method: 'POST',
        body: JSON.stringify({ command: 'messages', args: what ? `thread ${what}` : undefined, wait_s: 30 }),
      })
      setGlassMsg(r.pending ? '⏳ queued — receipt lands in Activity' : `✓ on the glass${r.result ? ` — ${String(r.result).slice(0, 60)}` : ''}`)
    } catch (e: any) {
      setGlassMsg(`✗ ${String(e?.message || e)}`)
    }
  }

  const unread = threads.reduce((n, t) => n + (t.unread || 0), 0)
  return (
    <section className="card messages">
      <h3>
        {open ? (
          <>
            <button className="ghost back" onClick={() => setOpen(null)} title="back to the inbox">←</button>
            <Avatar t={open.peer} />
            <span className="peer">@{open.peer?.login || open.peer?.userId}{showName(open.peer) && <span className="dim"> · {open.peer.name}</span>}</span>
          </>
        ) : (
          <>Messages{unread > 0 && <b className="badge unread"> {unread}</b>}<span className="tiny">your tiny.technology inbox — the same one the glass shows</span></>
        )}
        <span className="grow" />
        <button
          className="ghost"
          title={open
            ? `render this conversation on the Sticky's e-ink (fw verb: messages thread ${open.peer?.login})`
            : "render this inbox on the Sticky's e-ink (fw verb: messages — the device fetches with its own token)"}
          onClick={() => toGlass(open ? (open.peer?.login || open.peer?.userId) : undefined)}
        >
          {open ? 'thread to glass' : 'to glass'}
        </button>
      </h3>
      {err && <p className="err tiny">{err}</p>}
      <Receipt msg={glassMsg} />
      {!open && (
        <>
          {threads.length === 0 && !err && <p className="dim tiny">inbox empty</p>}
          <ul className="threads">
            {threads.slice(0, 8).map((t) => (
              <li key={t.userId}>
                <button className={'thread' + (t.unread > 0 ? ' unread' : '')} onClick={() => openThread(t)} title="opening marks the thread read — same as opening it on your phone">
                  <Avatar t={t} />
                  <span className="thread-main">
                    <span className="thread-top">
                      <span className="who">@{t.login || t.userId}{showName(t) && <span className="dim"> · {t.name}</span>}</span>
                      <span className="when dim">{t.lastAt ? shortAgo(t.lastAt) : ''}</span>
                    </span>
                    <span className="preview">{t.unread > 0 && <b className="dot" aria-label={`${t.unread} unread`} />}{String(t.lastBody || (t.lastAttachments?.length ? '[attachment]' : '')).slice(0, 96)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {open && (
        <>
          <div className="thread-msgs" role="log" aria-label={`conversation with @${open.peer?.login}`}>
            {(open.messages || []).slice(-12).map((m: any, i: number, arr: any[]) => {
              const prev = arr[i - 1]
              const newSpeaker = !prev || prev.direction !== m.direction || (prev.viaTiny ?? false) !== (m.viaTiny ?? false)
              return (
                <div key={m.id} className={'dm ' + (m.direction === 'in' ? 'in' : 'out') + (newSpeaker ? ' first' : '')}>
                  {newSpeaker && <span className="dm-who dim tiny">{m.direction === 'in' ? `@${open.peer.login}` : m.viaTiny ? 'tiny (as agent)' : 'you'}</span>}
                  <span className="dm-body">{m.body}</span>
                  <time className="dm-when dim" dateTime={m.created ? new Date(m.created * 1000).toISOString() : undefined}>{m.created ? clock(m.created) : ''}</time>
                </div>
              )
            })}
          </div>
          <div className="rowline compose">
            <input value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && send()} placeholder={`reply to @${open.peer.login}…`} maxLength={2000} aria-label={`reply to @${open.peer.login}`} />
            <button disabled={busy || !draft.trim()} onClick={send}>{busy ? '…' : 'Send'}</button>
          </div>
          <label className="as-tiny dim tiny" title="viaTiny marks the agent as the author — it never fakes a human">
            <input type="checkbox" checked={asTiny} onChange={(e) => setAsTiny(e.target.checked)} /> send as tiny
          </label>
        </>
      )}
      <Receipt msg={msg} />
    </section>
  )
}

function Avatar({ t }: { t: any }) {
  const login = String(t?.login || t?.userId || '?')
  return t?.avatar
    ? <img className="avatar" src={t.avatar} alt="" />
    : <span className="avatar initial" aria-hidden="true">{login.replace(/^@/, '').slice(0, 1).toUpperCase()}</span>
}

/** unix seconds → "2m" / "3h" / "Tue" / "Sep 2" */
function shortAgo(ts: number): string {
  const s = Date.now() / 1000 - ts
  if (s < 60) return 'now'
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  const d = new Date(ts * 1000)
  if (s < 6 * 86400) return d.toLocaleDateString(undefined, { weekday: 'short' })
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}
/** the display name earns its place only when it says more than the login */
function showName(t: any): boolean {
  const n = String(t?.name || '').trim(); const l = String(t?.login || '').trim()
  return !!n && n.toLowerCase() !== l.toLowerCase()
}
