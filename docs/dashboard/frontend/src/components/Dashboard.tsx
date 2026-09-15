import { useEffect, useState } from 'react'
import { api } from '../lib'
import { useDevice } from '../store'
import { DeviceRail } from './DeviceRail'
import type { Caps } from '../App'
import { ActionsCard, NavCard } from './Actions'
import { ActivityCard } from './Activity'
import { ComposerCard } from './Composer'
import { DeviceCard, StatusCard } from './Status'
import { FramesCard } from './Frames'
import { GlassBanner, MirrorCard } from './Glass'
import { MessagesCard } from './Messages'
import { SensorsCard } from './Sensors'

export function Dashboard() {
  // the URL's ?device= is the source of truth; keying the body on it
  // re-mounts every card so no state from Sticky #1 bleeds into #2
  const device = useDevice()
  return (
    <div className="layout">
      <DeviceRail />
      <DashboardBody key={device || '_default'} />
    </div>
  )
}

function DashboardBody() {
  const [bump, setBump] = useState(0)
  const [caps, setCaps] = useState<Caps | null>(null)
  const [capErr, setCapErr] = useState('')
  const refresh = () => setBump((b) => b + 1)

  useEffect(() => {
    api('/api/capabilities')
      .then((r) => {
        const map: Caps['map'] = {}
        for (const c of r.commands || []) map[c.command] = { available: c.available, reason: c.reason, source: c.source, new: !!c.new }
        setCaps({ mode: r.mode, taps: r.taps, note: r.note, advertised: !!r.advertised, map })
      })
      .catch((e) => setCapErr(String(e?.message || e)))
  }, [])

  if (capErr) return <div className="main center"><p className="err">{capErr}</p></div>
  if (!caps) return <div className="main center"><p className="dim">reading device capabilities…</p></div>

  return (
    <div className="main">
      {caps.mode !== 'live' && (
        <p className="warn modebar">◌ MOCK — this is a software Sticky, not the real device (start the backend with STICKY_MOCK=0 STICKY_DEVICE=sticky).</p>
      )}
      <GlassBanner />
      <section className="stage" aria-label="Glass">
        <MirrorCard bump={bump} caps={caps} onAction={refresh} />
        <div className="grid under">
          <StatusCard bump={bump} />
          <SensorsCard />
          <DeviceCard bump={bump} />
        </div>
      </section>
      <aside className="side" aria-label="Actions">
        <ActionsCard caps={caps} onAction={refresh} />
        <NavCard caps={caps} onAction={refresh} />
        <ComposerCard onSent={refresh} />
      </aside>
      <section className="grid below" aria-label="More">
        <MessagesCard mode={caps.mode} />
        <FramesCard />
        <ActivityCard bump={bump} caps={caps} />
      </section>
    </div>
  )
}
