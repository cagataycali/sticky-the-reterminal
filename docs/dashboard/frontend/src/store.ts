// Device selection — the URL is the source of truth (`?device=<id|name>`),
// so a link to a Sticky is shareable and reload-safe. Components subscribe
// with useDevice(); every api() call appends the selection (see lib.ts).
import { useEffect, useState } from 'react'

export type DeviceRow = {
  id: string; name: string; platform?: string; kind?: string
  online: boolean; relay_online?: boolean | null
  last_seen?: number | null; age_s?: number | null
  fw?: string | null; battery_pct?: number | null; charging?: boolean | null
  rotation?: number | null; status_age_s?: number | null; shot_age_s?: number | null
  default?: boolean
}

const listeners = new Set<() => void>()

export function currentDevice(): string | null {
  const v = new URLSearchParams(window.location.search).get('device')
  return v && v.trim() ? v.trim() : null
}

export function setDevice(sel: string | null, replace = false) {
  const u = new URL(window.location.href)
  if (sel) u.searchParams.set('device', sel)
  else u.searchParams.delete('device')
  if (u.href === window.location.href) return
  if (replace) history.replaceState(null, '', u)
  else history.pushState(null, '', u)
  listeners.forEach((l) => l())
}

/** The selected device (id or name) — null means "the server's default". */
export function useDevice(): string | null {
  const [sel, setSel] = useState<string | null>(currentDevice)
  useEffect(() => {
    const l = () => setSel(currentDevice())
    listeners.add(l)
    window.addEventListener('popstate', l)
    return () => { listeners.delete(l); window.removeEventListener('popstate', l) }
  }, [])
  return sel
}

// ── shared fleet (DeviceRail loads it; the glass and header read it) ───
let fleet: DeviceRow[] | null = null
const fleetListeners = new Set<() => void>()
export function setFleet(rows: DeviceRow[]) { fleet = rows; fleetListeners.forEach((l) => l()) }
export function useFleet(): DeviceRow[] | null {
  const [f, setF] = useState<DeviceRow[] | null>(fleet)
  useEffect(() => { const l = () => setF(fleet); fleetListeners.add(l); return () => { fleetListeners.delete(l) } }, [])
  return f
}
/** The selected device's fleet row (null until the rail has loaded). */
export function useSelectedRow(): DeviceRow | null {
  const sel = useDevice(); const f = useFleet()
  if (!f) return null
  return f.find((d) => (sel ? d.id === sel || d.name === sel : d.default)) || null
}
