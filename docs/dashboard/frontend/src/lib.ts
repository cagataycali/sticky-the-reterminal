// api + WebAuthn helpers for the sticky dashboard
import { currentDevice } from './store'

let TOKEN: string | null = localStorage.getItem('sticky_token')

export function setToken(t: string | null) {
  TOKEN = t
  if (t) localStorage.setItem('sticky_token', t)
  else localStorage.removeItem('sticky_token')
}

export function token(): string | null {
  return TOKEN
}

/** Append the selected device (URL `?device=`) to every /api/ call that
 *  does not name one — the backend's per-device caches key on it (D1). */
export function withDevice(path: string): string {
  if (!path.startsWith('/api/')) return path
  const sel = currentDevice()
  if (!sel || /[?&]device=/.test(path)) return path
  return path + (path.includes('?') ? '&' : '?') + 'device=' + encodeURIComponent(sel)
}

export async function api(path: string, opts: RequestInit = {}): Promise<any> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...(opts.headers as any) }
  if (TOKEN) headers['Authorization'] = `Bearer ${TOKEN}`
  const r = await fetch(withDevice(path), { ...opts, headers })
  if (r.status === 401) throw new AuthError((await r.json().catch(() => ({})))?.detail || 'authentication required')
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body?.detail || body?.error || `HTTP ${r.status}`)
  return body
}

export class AuthError extends Error {}

// Fired when a request 401s AFTER a session was established — the app
// listens and drops to the login gate with a "session expired" banner
// instead of every card showing its own wall of 401s.
export const onSessionExpired = { fire: () => {} }

// ── base64url ↔ bytes (WebAuthn wire format) ──────────────────────────
const b64uToBuf = (s: string): ArrayBuffer => {
  const pad = '='.repeat((4 - (s.length % 4)) % 4)
  const bin = atob(s.replace(/-/g, '+').replace(/_/g, '/') + pad)
  const buf = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i)
  return buf.buffer
}
const bufToB64u = (b: ArrayBuffer): string =>
  btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')

function decodeCreationOptions(o: any): PublicKeyCredentialCreationOptions {
  return {
    ...o,
    challenge: b64uToBuf(o.challenge),
    user: { ...o.user, id: b64uToBuf(o.user.id) },
    excludeCredentials: (o.excludeCredentials || []).map((c: any) => ({ ...c, id: b64uToBuf(c.id) })),
  }
}
function decodeRequestOptions(o: any): PublicKeyCredentialRequestOptions {
  return {
    ...o,
    challenge: b64uToBuf(o.challenge),
    allowCredentials: (o.allowCredentials || []).map((c: any) => ({ ...c, id: b64uToBuf(c.id) })),
  }
}
function encodeAttestation(cred: PublicKeyCredential): any {
  const r = cred.response as AuthenticatorAttestationResponse
  return {
    id: cred.id,
    rawId: bufToB64u(cred.rawId),
    type: cred.type,
    clientExtensionResults: cred.getClientExtensionResults(),
    response: {
      clientDataJSON: bufToB64u(r.clientDataJSON),
      attestationObject: bufToB64u(r.attestationObject),
    },
  }
}
function encodeAssertion(cred: PublicKeyCredential): any {
  const r = cred.response as AuthenticatorAssertionResponse
  return {
    id: cred.id,
    rawId: bufToB64u(cred.rawId),
    type: cred.type,
    clientExtensionResults: cred.getClientExtensionResults(),
    response: {
      clientDataJSON: bufToB64u(r.clientDataJSON),
      authenticatorData: bufToB64u(r.authenticatorData),
      signature: bufToB64u(r.signature),
      userHandle: r.userHandle ? bufToB64u(r.userHandle) : null,
    },
  }
}

export async function registerPasskey(label: string, bootstrap = ''): Promise<void> {
  const begin = await api('/auth/register/begin', { method: 'POST', body: JSON.stringify({ label, bootstrap }) })
  const cred = (await navigator.credentials.create({
    publicKey: decodeCreationOptions(begin.options),
  })) as PublicKeyCredential
  const out = await api('/auth/register/finish', {
    method: 'POST',
    body: JSON.stringify({ challenge_id: begin.challenge_id, credential: encodeAttestation(cred) }),
  })
  setToken(out.token)
}

export async function loginPasskey(): Promise<void> {
  const begin = await api('/auth/login/begin', { method: 'POST', body: JSON.stringify({}) })
  const cred = (await navigator.credentials.get({
    publicKey: decodeRequestOptions(begin.options),
  })) as PublicKeyCredential
  const out = await api('/auth/login/finish', {
    method: 'POST',
    body: JSON.stringify({ challenge_id: begin.challenge_id, credential: encodeAssertion(cred) }),
  })
  setToken(out.token)
}

export async function invoke(command: string, args?: any, wait_s?: number): Promise<any> {
  return api('/api/invoke', { method: 'POST', body: JSON.stringify({ command, args, wait_s }) })
}
