// light / dark / system — persisted; <html data-scheme> drives the tokens
export type Scheme = 'light' | 'dark' | 'system'
const KEY = 'sticky_scheme'
export function getScheme(): Scheme { return (localStorage.getItem(KEY) as Scheme) || 'system' }
export function applyScheme(s: Scheme) {
  if (s === 'system') { delete document.documentElement.dataset.scheme; localStorage.removeItem(KEY) }
  else { document.documentElement.dataset.scheme = s; localStorage.setItem(KEY, s) }
}
applyScheme(getScheme())
