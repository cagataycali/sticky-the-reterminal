import { api } from '../lib'
import { Row, fmtAge, fmtUptime, usePoll } from '../hooks'
import { LINT_GRAMMAR, setFwGrammar } from '../cards'
import { PocketRow } from './Glass'

export function StatusCard({ bump }: { bump: number }) {
  // Auto-refresh WITHOUT costing the pocket battery: /api/status/latest is a
  // server-side cache (age_s says how old), polled here every 15 s. The server
  // sends a real relay `status` at most every 120 s AND only while someone is
  // actually polling — page load never blocks on a device round-trip.
  const { data, err } = usePoll<any>(() => api('/api/status/latest'), bump, 15000)
  const s = data?.status
  const age: number | null = data?.age_s ?? null
  const stale = age != null && age > 300
  // feed the lint's drift sentinel — the composer's rules were written against
  // LINT_GRAMMAR; the device is the only authority on what it actually speaks
  if (s?.grammar_version != null) setFwGrammar(s.grammar_version)
  const drift = s?.grammar_version != null && s.grammar_version !== LINT_GRAMMAR
  return (
    <section className={`card${stale ? ' stale' : ''}`}>
      <h3>
        Status{' '}
        <span className={stale ? 'warn tiny' : 'dim tiny'}>
          {age == null ? '' : stale ? `stale · ${fmtAge(age)} old` : `${fmtAge(age)} ago`}
          {age != null && data?.refreshing && ' · refreshing'}
        </span>
      </h3>
      {err && <p className="err">{err}</p>}
      {s && (
        <p className="topline">
          {[
            s.locked != null ? (s.locked ? 'locked' : 'unlocked') : null,
            s.battery_pct != null ? `${s.battery_pct} %${s.charging === true ? ' charging' : ''}` : null,
            s.rssi_dbm != null ? `Wi-Fi ${s.rssi_dbm} dBm` : null,
            s.fw ? `fw ${s.fw}` : null,
          ].filter(Boolean).join(' · ')}
        </p>
      )}
      {drift && (
        <p className="warn tiny">Card grammar drift: firmware v{s.grammar_version}, dashboard v{LINT_GRAMMAR} — composer lint and tap zones may be stale.</p>
      )}
      {s ? (
        <>
          <div className="kv">
            <Row k="firmware" v={s.fw ? `${s.fw}${s.fw_commit ? ` (${s.fw_commit})` : ''}${s.grammar_version != null ? ` · grammar v${s.grammar_version}` : ''}` : null} />
            <Row k="battery" v={s.battery_pct == null ? null : `${s.battery_pct}%${s.charging === true ? ' · charging' : s.charging === false ? ' · on battery' : ''}`} />
            <Row k="wifi" v={s.rssi_dbm != null ? `${s.rssi_dbm} dBm` : s.wifi} />
            <Row k="heap free" v={s.heap_free != null ? `${Math.round(s.heap_free / 1024)} KiB${s.internal_free != null ? ` · internal ${Math.round(s.internal_free / 1024)} KiB` : ''}` : null} />
            {/* P0 instruments (fw 0.23.2): min_free = the lowest internal heap EVER
                seen this boot — the crash forensics number. <48 KiB is the danger
                band to hunt for; paint it loud. */}
            <Row k="heap low-water" v={s.min_free != null ? `${Math.round(s.min_free / 1024)} KiB${s.min_free < 49152 ? ' — danger band, <48 KiB' : ''}` : null} />
            <Row k="last boot" v={s.last_boot ? `${s.last_boot}${s.last_boot === 'panic' || String(s.last_boot).includes('wdt') ? ' — check' : ''}` : null} />
            <Row k="uptime" v={s.uptime_s != null ? fmtUptime(s.uptime_s) : null} />
            <Row k="display" v={s.display} />
            <Row k="state" v={s.sleeping == null ? null : s.sleeping ? 'sleeping' : 'awake'} />
          </div>
        </>
      ) : (
        !err && !data?.pending && <p className="receipt pending"><span className="receipt-dot" aria-hidden="true" /><span className="receipt-text">asking the device for its first receipt</span></p>
      )}
    </section>
  )
}

export function DeviceCard({ bump }: { bump: number }) {
  const { data, err } = usePoll<any>(() => api('/api/device'), bump, 60000)
  const d = data?.device
  return (
    <section className="card">
      <h3>Device</h3>
      {err && <p className="err">{err}</p>}
      {d ? (
        <div className="kv">
          <Row k="name" v={d.name} />
          <Row k="id" v={String(d.id || '').slice(0, 8) + '…'} />
          <Row k="kind" v={d.kind} />
          <Row k="online" v={d.online === false ? '✗ offline' : '✓ online'} />
          <Row k="last seen" v={d.last_seen ? new Date(Number(d.last_seen) * (String(d.last_seen).length > 11 ? 1 : 1000)).toLocaleTimeString() : null} />
        </div>
      ) : (
        !err && <p className="dim">…</p>
      )}
      <PocketRow />
    </section>
  )
}
