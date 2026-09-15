import type { ReactNode } from 'react'

/** The docs site's device bezel (106 × 65.5 mm body, four corner magnets,
 *  three top-edge buttons) around whatever is on the glass. `rotation`
 *  90/270 stands it up. `asleep` dims the frame — the device is offline and
 *  this is its LAST frame, said out loud by the caller. */
export function Bezel({ children, rotation = 0, asleep = false, className = '' }:
  { children: ReactNode; rotation?: number; asleep?: boolean; className?: string }) {
  const portrait = Math.abs(rotation % 180) === 90
  return (
    <div className={`bezel${portrait ? ' bezel--portrait' : ''}${asleep ? ' asleep' : ''} ${className}`.trim()} aria-hidden={false}>
      <span className="btn-ai" aria-hidden="true" /><span className="btn-up" aria-hidden="true" /><span className="btn-down" aria-hidden="true" />
      <span className="magnet tl" aria-hidden="true" /><span className="magnet tr" aria-hidden="true" /><span className="magnet bl" aria-hidden="true" /><span className="magnet br" aria-hidden="true" />
      <div className="screen">{children}</div>
    </div>
  )
}
