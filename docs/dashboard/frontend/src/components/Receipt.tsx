import { useSelectedRow } from '../store'

/** One honest line under an action: pending (queued, the device answers on
 *  its next poll) · landed · refused. Parses the conventional prefixes the
 *  cards already produce (… ⏳ ✓ ⚠ ✗) so every card reads the same. */
export function Receipt({ msg, inline = false }: { msg: string; inline?: boolean }) {
  const row = useSelectedRow()
  if (!msg) return null
  const m = msg.trim()
  const state = /^(…|⏳)/.test(m) ? 'pending' : m.startsWith('✓') ? 'ok' : m.startsWith('⚠') ? 'warn' : m.startsWith('✗') ? 'err' : 'info'
  const text = m.replace(/^(…|⏳|✓|⚠|✗)\s*/, '') || (state === 'pending' ? 'sending…' : m)
  const Tag: any = inline ? 'span' : 'p'
  return (
    <Tag className={`receipt ${state}`} role={state === 'err' ? 'alert' : 'status'} aria-live="polite">
      <span className="receipt-dot" aria-hidden="true" />
      <span className="receipt-text">
        {state === 'pending' && !/queued|pending|sending/i.test(text) ? `${text} — waiting for ${row?.name || 'the device'}` : text}
      </span>
    </Tag>
  )
}
