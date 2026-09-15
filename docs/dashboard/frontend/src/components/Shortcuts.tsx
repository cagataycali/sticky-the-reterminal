import { useEffect, useState } from 'react'
import { setDevice, useDevice, useFleet } from '../store'

/** Keyboard for the whole dashboard. Never fires while typing. `?` opens
 *  the sheet, which is the documentation. */
const KEYS: [string, string][] = [
  ['r', 'ask the glass for a fresh frame'],
  ['[  ]', 'previous / next device'],
  ['1 – 9', 'jump to the n-th device in the rail'],
  ['c', 'focus the card composer'],
  ['t', 'toggle the colour scheme'],
  ['esc', 'close this sheet / leave a field'],
  ['?', 'this sheet'],
]

export function Shortcuts() {
  const [open, setOpen] = useState(false)
  const fleet = useFleet()
  const sel = useDevice()

  useEffect(() => {
    const typing = (t: EventTarget | null) => {
      const el = t as HTMLElement | null
      return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)
    }
    const h = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === 'Escape') {
        if (open) { setOpen(false); e.preventDefault(); return }
        if (typing(e.target)) (e.target as HTMLElement).blur()
        return
      }
      if (typing(e.target)) return
      const rows = fleet || []
      const i = rows.findIndex((d) => (sel ? d.id === sel || d.name === sel : d.default))
      switch (e.key) {
        case '?': setOpen((o) => !o); break
        case 'r': window.dispatchEvent(new Event('dash:refresh')); break
        case ']': if (rows.length) setDevice(rows[(Math.max(i, 0) + 1) % rows.length].name); break
        case '[': if (rows.length) setDevice(rows[(Math.max(i, 0) - 1 + rows.length) % rows.length].name); break
        case 'c': focus('.composer textarea'); break
        case 't': (document.querySelector('header .scheme') as HTMLButtonElement | null)?.click(); break
        default: {
          const n = Number(e.key)
          if (n >= 1 && n <= 9 && rows[n - 1]) setDevice(rows[n - 1].name)
          else return
        }
      }
      e.preventDefault()
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, fleet, sel])

  return (
    <>
      <button className="ghost kbd-hint" onClick={() => setOpen(true)} title="keyboard shortcuts (?)" aria-haspopup="dialog">?</button>
      {open && (
        <div className="sheet-backdrop" onClick={() => setOpen(false)}>
          <div className="sheet" role="dialog" aria-modal="true" aria-label="keyboard shortcuts" onClick={(e) => e.stopPropagation()}>
            <h3>Keyboard</h3>
            <dl className="keys">
              {KEYS.map(([k, v]) => (
                <div key={k}><dt><kbd>{k}</kbd></dt><dd>{v}</dd></div>
              ))}
            </dl>
            <p className="tiny">In the composer, <kbd>⌘↵</kbd> sends. In the rail, arrows move between devices.</p>
          </div>
        </div>
      )}
    </>
  )
}

function focus(selector: string) {
  const el = document.querySelector(selector) as HTMLElement | null
  if (!el) return
  el.focus()
  el.scrollIntoView({ block: 'center', behavior: 'smooth' })
}
