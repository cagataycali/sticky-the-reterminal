import { useState } from 'react'
import { api } from '../lib'
import { Row } from '../hooks'

export function SensorsCard() {
  // On-demand, never polled: a sensors read is a full device round-trip, and
  // battery is the product on a pocket-carried 750 mAh pack.
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const read = async () => {
    setBusy(true); setErr('')
    try { setData(await api('/api/sensors')) } catch (e: any) { setErr(String(e?.message || e)) }
    setBusy(false)
  }
  const sn = data?.sensors
  const bat = sn?.battery
  const fmt = (v: any, unit = '', digits = 1) =>
    v == null ? null : `${typeof v === 'number' ? v.toFixed(digits) : v}${unit}`
  return (
    <section className="card">
      <h3>Sensors <button className="ghost" disabled={busy} onClick={read}>{busy ? '…' : sn ? 'read again' : 'read now'}</button></h3>
      {err && <p className="err">{err}</p>}
      {data?.pending && <p className="warn">envelope queued — tap read again in a few seconds</p>}
      {data?.truncated && <p className="warn">Reply arrived truncated (fw cuts the sensors JSON at ~767 B, bug filed) — fields below were salvaged from the fragment; missing rows are casualties of the cut, not sensor failures</p>}
      {sn && (
        <>
          <div className="kv">
            <Row k="temperature" v={fmt(sn.temperature_c, ' °C')} />
            <Row k="humidity" v={fmt(sn.humidity_pct, ' %', 0)} />
            <Row k="orientation" v={sn.orientation} />
            <Row k="accel" v={sn.accel_g ? `x ${fmt(sn.accel_g.x, '', 2)} · y ${fmt(sn.accel_g.y, '', 2)} · z ${fmt(sn.accel_g.z, '', 2)} g` : null} />
            <Row k="gyro" v={sn.gyro_dps == null ? '— (powered down, not broken)' : `x ${fmt(sn.gyro_dps.x, '', 1)} · y ${fmt(sn.gyro_dps.y, '', 1)} · z ${fmt(sn.gyro_dps.z, '', 1)} dps`} />
            <Row k="rtc" v={sn.rtc ? `${sn.rtc}${sn.clock_source ? ` (${sn.clock_source})` : ''}` : null} />
            <Row k="battery" v={bat ? `${bat.percent != null ? `${bat.percent}%` : '—'}${bat.voltage_mv != null ? ` · ${(bat.voltage_mv / 1000).toFixed(2)} V` : ''}${bat.charging === true ? ' ⚡' : ''}` : null} />
            {bat?.mah_trusted === true && <Row k="capacity" v={`${bat.remaining_mah ?? '—'} / ${bat.full_charge_mah ?? '—'} mAh`} />}
          </div>
          {bat && bat.mah_trusted === false && (
            <p className="dim tiny">gauge mAh hidden: BQ27220 reports design {bat.design_mah ?? '?'} mAh on a {bat.pack_mah ?? '?'} mAh pack (mah_trusted:false) — percent and voltage are the believable fields</p>
          )}
          {Array.isArray(sn.errors) && sn.errors.length > 0 && <p className="err">sensor errors: {sn.errors.join(' · ')}</p>}
          {sn.summary && <p className="dim tiny">{sn.summary}</p>}
        </>
      )}
      {!sn && !err && !data?.pending && <p className="dim tiny">SHT40 · IMU · RTC · fuel gauge — on demand, one device round-trip per read</p>}
    </section>
  )
}
