import { useState } from 'react'
import { loginPasskey, registerPasskey } from '../lib'
import { Bezel } from './Bezel'

/** The front door: paper, the device with a real home-page capture, one
 *  button. Copy says what a passkey is for in one line and never shouts. */
export function AuthGate({ mode, warning, bootstrapRequired, onDone }: { mode: 'setup' | 'login'; warning: string; bootstrapRequired: boolean; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [bootstrap, setBootstrap] = useState('')
  const canPasskey = typeof window !== 'undefined' && 'PublicKeyCredential' in window
  const go = async () => {
    setBusy(true)
    setErr('')
    try {
      if (mode === 'setup') await registerPasskey('admin passkey', bootstrap)
      else await loginPasskey()
      onDone()
    } catch (e: any) {
      const m = String(e?.message || e)
      // the browser's own words are unhelpful ("The operation either timed out…")
      setErr(/NotAllowed|timed out|abort/i.test(m) ? 'The passkey prompt was dismissed — try again.' : m)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="gate2">
      <div className="gate2-glass" aria-hidden="true">
        <Bezel>
          <img src="/img/home.png" alt="" width={800} height={480} />
        </Bezel>
      </div>
      <form className="gate2-form" onSubmit={(e) => { e.preventDefault(); if (!busy) go() }}>
        <h2>{mode === 'setup' ? 'Seal this dashboard' : 'Your Stickies'}</h2>
        <p className="lede">
          {mode === 'setup'
            ? 'No passkey is enrolled yet. Create one now; from then on only that passkey opens this page.'
            : 'This page drives the e-ink devices on your account. A passkey (Touch ID, Face ID or a security key) is the only key.'}
        </p>
        {warning && <p className="warn">{warning}</p>}
        {!canPasskey && <p className="warn">This browser has no passkey support — open the page in Safari, Chrome or Edge.</p>}
        {mode === 'setup' && bootstrapRequired && (
          <label className="field">
            <span>Bootstrap token</span>
            <input type="password" autoComplete="one-time-code" placeholder="tiny sent it to you" value={bootstrap} onChange={(e) => setBootstrap(e.target.value)} />
          </label>
        )}
        <button type="submit" className="primary big" disabled={busy || !canPasskey || (mode === 'setup' && bootstrapRequired && !bootstrap)}>
          {busy ? 'Waiting for the passkey…' : mode === 'setup' ? 'Create a passkey' : 'Continue with passkey'}
        </button>
        {err && <p className="err" role="alert">{err}</p>}
        <p className="tiny foot">Nothing here is public. Sessions last 30 days; log out ends one early.</p>
      </form>
    </div>
  )
}
