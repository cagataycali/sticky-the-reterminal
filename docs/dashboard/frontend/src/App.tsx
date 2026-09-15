import { useCallback, useEffect, useState } from 'react'
import { api, AuthError, onSessionExpired, setToken, token } from './lib'
import { useDevice } from './store'
import { applyScheme, getScheme, type Scheme } from './scheme'
import { AuthGate } from './components/Auth'
import { Shortcuts } from './components/Shortcuts'
import { Dashboard } from './components/Dashboard'

type AuthView = 'loading' | 'setup' | 'login' | 'dashboard'
export type Caps = { mode: 'mock' | 'live'; taps: 'real' | 'simulated'; note?: string | null; advertised?: boolean; map: Record<string, { available: boolean; reason?: string | null; source?: string; new?: boolean }> }
export default function App() {
  const [view, setView] = useState<AuthView>('loading')
  const [err, setErr] = useState('')
  const [authWarning, setAuthWarning] = useState('')
  const [bootstrapRequired, setBootstrapRequired] = useState(false)

  const boot = useCallback(async () => {
    try {
      const st = await api('/auth/status')
      if (st.warning) setAuthWarning(st.warning)
      setBootstrapRequired(!!st.bootstrap_required)
      if (!st.enabled) return setView('dashboard')
      if (st.setup_required) return setView('setup')
      // have a stored token? probe a guarded route
      if (token()) {
        try {
          await api('/api/capabilities')
          return setView('dashboard')
        } catch (e) {
          if (e instanceof AuthError) setToken(null)
          else return setView('dashboard') // guarded route errored for non-auth reasons
        }
      }
      setView('login')
    } catch (e: any) {
      setErr(String(e?.message || e))
    }
  }, [])

  useEffect(() => {
    // mid-session 401 (expired/revoked token) → one loud banner, back to the gate
    onSessionExpired.fire = () => {
      setToken(null)
      setAuthWarning('session expired — sign in again')
      setView((v) => (v === 'dashboard' ? 'login' : v))
    }
    boot()
    return () => { onSessionExpired.fire = () => {} }
  }, [boot])

  if (view === 'loading')
    return (
      <Shell>
        <div className="center">{err ? <p className="err">{err}</p> : <p>connecting…</p>}</div>
      </Shell>
    )
  if (view === 'setup' || view === 'login')
    return (
      <Shell>
        <AuthGate mode={view} warning={authWarning} bootstrapRequired={bootstrapRequired} onDone={() => setView('dashboard')} />
      </Shell>
    )
  return (
    <Shell onLogout={async () => { await api('/auth/logout', { method: 'POST' }).catch(() => {}); setToken(null); setView('login') }}>
      <Dashboard />
    </Shell>
  )
}

function Shell({ children, onLogout }: { children: any; onLogout?: () => void }) {
  const device = useDevice()
  return (
    <div className="shell">
      <header>
        <h1>sticky</h1>
        <span className="sub">{device && device !== 'sticky' ? device : 'dashboard'}</span>
        <span className="grow" />
        <Shortcuts />
        <SchemeToggle />
        {onLogout && (
          <button className="ghost" onClick={onLogout}>
            log out
          </button>
        )}
      </header>
      <main id="main">{children}</main>
    </div>
  )
}

function SchemeToggle() {
  const [s, setS] = useState<Scheme>(getScheme())
  const next: Record<Scheme, Scheme> = { system: 'light', light: 'dark', dark: 'system' }
  const label: Record<Scheme, string> = { system: 'auto', light: 'light', dark: 'dark' }
  return (
    <button className="ghost scheme" title="colour scheme" aria-label={`colour scheme: ${label[s]}`}
      onClick={() => { const n = next[s]; applyScheme(n); setS(n) }}>
      ◐ {label[s]}
    </button>
  )
}
