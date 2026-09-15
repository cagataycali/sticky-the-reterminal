import { Receipt } from './Receipt'
import { useEffect, useMemo, useState } from 'react'
import { invoke } from '../lib'
import { CARD_PRESETS, lintCard } from '../cards'

/** Compose a card → render_ui. The lint below the editor is advice, not a
 *  gate: only unparseable JSON blocks the send (the device would refuse it
 *  anyway). ⌘/Ctrl+Enter sends. Switching a preset while the text is dirty
 *  keeps your edit until you press "reset". */
export function ComposerCard({ onSent }: { onSent: () => void }) {
  const [preset, setPreset] = useState('text')
  const [text, setText] = useState(JSON.stringify(CARD_PRESETS.text, null, 2))
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const pristine = JSON.stringify(CARD_PRESETS[preset], null, 2)
  const dirty = text !== pristine

  const { lint, parseErr, type } = useMemo(() => {
    try {
      const spec = JSON.parse(text)
      return { lint: lintCard(spec), parseErr: '', type: spec?.type as string | undefined }
    } catch (e: any) {
      return { lint: [] as string[], parseErr: humanJsonError(String(e?.message || e), text), type: undefined }
    }
  }, [text])
  const drift = lint.filter((l) => l.startsWith('⚠ grammar drift'))
  const warnings = lint.filter((l) => !l.startsWith('⚠ grammar drift'))

  const pick = (p: string) => {
    if (dirty && p !== preset && !confirm(`Replace your edited spec with the “${p}” preset?`)) return
    setPreset(p)
    setText(JSON.stringify(CARD_PRESETS[p], null, 2))
    setMsg('')
  }
  const send = async () => {
    if (parseErr || busy) return
    setBusy(true)
    setMsg('')
    try {
      const spec = JSON.parse(text)
      const r = await invoke('render_ui', spec, 60)
      if (r.pending) setMsg(`… queued (${r.envelope_id}) — the device picks it up within 5 s`)
      else if (r.committed_card_id) {
        const want = spec.card_id
        setMsg(want && want !== r.committed_card_id
          ? `⚠ glass committed card_id=${r.committed_card_id} but the spec said ${want}`
          : `✓ on glass — committed card_id=${r.committed_card_id} (mirror refreshing)`)
      } else setMsg(`✓ ${r.result} ${r.card_id ?? ''}`)
      if (Array.isArray(r.render_warning)) setMsg((m) => `${m} — ⚠ ${r.render_warning.join('; ')}`)
      onSent()
    } catch (e: any) {
      setMsg(`✗ ${String(e?.message || e)}`)
    } finally {
      setBusy(false)
    }
  }

  // ⌘/Ctrl+Enter from inside the editor
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && (e.target as HTMLElement)?.closest?.('.composer')) { e.preventDefault(); send() }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  })

  return (
    <section className="card wide composer">
      <h3>
        Compose a card
        <span className="tiny" title="the dashboard sends your JSON to the glass as a render_ui envelope; the glass answers with the card_id it committed">render_ui</span>
        <a className="h-meta" href="/cards/" target="_blank" rel="noreferrer" title="every card type and field, with examples">schema</a>
      </h3>
      <div className="presets" role="tablist" aria-label="card presets">
        {Object.keys(CARD_PRESETS).map((p) => (
          <button key={p} role="tab" aria-selected={p === preset} className={p === preset ? 'chip on' : 'chip'} onClick={() => pick(p)}>
            {p}
          </button>
        ))}
      </div>
      <textarea
        aria-label="card spec (JSON)"
        aria-invalid={!!parseErr}
        className={parseErr ? 'invalid' : ''}
        value={text}
        onChange={(e) => setText(e.target.value)}
        spellCheck={false}
        rows={10}
      />
      <div className="lint" aria-live="polite">
        {parseErr && <p className="lint-line err">{parseErr}</p>}
        {!parseErr && warnings.length === 0 && (
          <p className="lint-line ok">{type ? `${type} card, nothing to warn about` : 'valid JSON'}{dirty ? ' · edited' : ''}</p>
        )}
        {warnings.map((w, i) => <p key={i} className="lint-line warn">{w}</p>)}
        {drift.length > 0 && <p className="lint-line dim" title={drift[0]}>lint rules may lag this firmware — the glass's receipt is the judge</p>}
      </div>
      <div className="rowline">
        <button disabled={busy || !!parseErr} onClick={send} title={parseErr ? 'fix the JSON first' : 'send to the selected device (⌘↵)'}>
          {busy ? 'sending…' : 'Send to device'}
        </button>
        <kbd className="tiny" aria-hidden="true">⌘↵</kbd>
        {dirty && <button className="ghost" onClick={() => { setText(pristine); setMsg('') }} title="back to the preset">reset</button>}
        <Receipt msg={msg} inline />
      </div>
    </section>
  )
}

/** V8 already says it well ("Expected double-quoted property name in JSON at
 *  position 33 (line 1 column 34)") — keep its sentence, lead with the line,
 *  add the one hint a human needs. */
function humanJsonError(raw: string, text: string): string {
  const lc = /\(line (\d+) column (\d+)\)/.exec(raw)
  const pos = /position (\d+)/.exec(raw)
  const line = lc ? Number(lc[1]) : pos ? text.slice(0, Number(pos[1])).split('\n').length : 0
  const sentence = raw.replace(/\s*in JSON at position.*$/, '').replace(/^JSON\.parse:\s*/, '')
  const before = pos ? text.slice(0, Number(pos[1])).trimEnd() : ''
  let hint = ''
  if (/Expected double-quoted property name/.test(sentence) && before.endsWith(',')) hint = ' — a trailing comma before the closing brace'
  else if (/Expected property name/.test(sentence)) hint = ' — keys need double quotes'
  else if (/Expected ',' or '}'/.test(sentence) && /end of|$^/.test(sentence) === false && text.trimEnd().length === (pos ? Number(pos[1]) : -1)) hint = ' — the object is still open'
  else if (/Expected ',' or '}'/.test(sentence)) hint = ' — a comma is missing before the next key, or a brace is still open'
  else if (/Unterminated string/.test(sentence)) hint = ' — a quote is still open'
  else if (/Unexpected end/.test(sentence)) hint = ' — a bracket or quote is still open'
  return `${line ? `line ${line}: ` : ''}${sentence.charAt(0).toLowerCase() + sentence.slice(1)}${hint}`
}
