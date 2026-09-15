import { useEffect, useState } from 'react'
import { api } from '../lib'
import { Row } from '../hooks'

// ── dashboard ──────────────────────────────────────────────────────────
export function FramesCard() {
  // The sequence shelf + the ceremony trigger. Premiere is the ONE-call
  // receipted act (probe → window → play → testimony → frame-match →
  // home restore) — this card just fires it and shows the receipts.
  const [seqs, setSeqs] = useState<any[]>([])
  const [busy, setBusy] = useState('')
  const [report, setReport] = useState<any>(null)
  const [queue, setQueue] = useState<any>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    api('/api/frames/library').then((r) => setSeqs(r.sequences || [])).catch((e) => setErr(String(e?.message || e)))
  }, [])
  useEffect(() => {
    // queue watcher: poll while a premiere is queued/running, surface its report
    const t = setInterval(() => {
      api('/api/frames/premiere-queue').then((q) => {
        setQueue(q)
        if (q.state === 'done' && q.report) setReport(q.report)
      }).catch(() => {})
    }, 10000)
    return () => clearInterval(t)
  }, [])
  const queuePremiere = async (seq: string) => {
    setErr('')
    try {
      const r = await api(`/api/frames/${seq}/premiere-when-free`, { method: 'POST' })
      setQueue({ seq_id: seq, state: 'waiting', queued_at: 'now' })
      void r
    } catch (e: any) { setErr(String(e?.message || e).slice(0, 200)) }
  }
  const premiere = async (seq: string) => {
    setBusy(seq); setReport(null); setErr('')
    try {
      // ceremony runs ~30-90s on-device — long fetch is honest here
      const r = await api(`/api/frames/${seq}/premiere`, { method: 'POST' })
      setReport(r)
    } catch (e: any) {
      setErr(String(e?.message || e).slice(0, 200))
    } finally { setBusy('') }
  }
  return (
    <section className="card">
      <h3>Frames <span className="tiny">rendered sequences — “premiere” plays one on the glass (~1 min ceremony)</span></h3>
      {err && <p className="err">{err}</p>}
      {seqs.length === 0 && !err && <p className="dim">no rendered sequences yet — compose one via /api/frames or /anim</p>}
      {seqs.length > 0 && (
        <ul className="seqs">
          {seqs.map((s) => (
            <li key={s.seq_id} className="seq">
              <img src={s.preview} alt="" className="seq-thumb" />
              <span className="seq-meta"><code>{s.seq_id.slice(0, 6)}</code><span className="dim"> · {s.count} frame{s.count === 1 ? '' : 's'} · {s.interval_ms} ms{s.mirrored ? ' · mirrored' : ''}</span></span>
              <span className="seq-acts">
                <button className="ghost" disabled={!!busy} onClick={() => premiere(s.seq_id)} title="play this sequence on the glass now">
                  {busy === s.seq_id ? 'premiering… ~1 min' : 'premiere'}
                </button>
                {s.mirrored && (
                  <button className="ghost" title="fires the full ceremony automatically the moment no client holds a glass window (30min patience)" onClick={() => queuePremiere(s.seq_id)}>
                    when free
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      {queue && queue.state !== 'idle' && (
        <p className="dim tiny">queue: {queue.seq_id?.slice(0, 6)} · {queue.state}{queue.queued_at ? ` · since ${queue.queued_at}` : ''}</p>
      )}
      {report && (
        <div className="kv">
          <Row k="verdict" v={report.verdict} />
          <Row k="probe" v={report.stages?.probe ? `${report.stages.probe.frames}f transport_ok=${report.stages.probe.transport_ok}` : null} />
          <Row k="player" v={report.stages?.verdict || null} />
          <Row k="frame match" v={report.stages?.frame_match ? `frame ${report.stages.frame_match.best_frame} · ${report.stages.frame_match.diff_pct}% diff` : report.stages?.frame_match?.error} />
          <Row k="home restored" v={String(report.stages?.home_restored ?? '?')} />
        </div>
      )}
    </section>
  )
}
