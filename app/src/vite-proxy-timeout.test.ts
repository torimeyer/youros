import { describe, it, expect } from 'vitest'
import viteConfig from '../vite.config'

// →2626: the "AI suggest" button in the What-needs-clarity panel returned a
// red "Upstream unavailable" error. Root cause: the dev proxy's blanket
// proxyTimeout of 5000ms (→1930) is SHORTER than a normal AI-suggest
// response. Measured 2026-07-09 in isolation against the venv: 6.39s, 5.28s,
// 5.26s per call on an idle machine. FastAPI sends zero bytes until the
// handler returns, so the proxy's inactivity timer fired at 5s on every
// single healthy request and wrote the 502.
//
// This test mirrors vite's own route matcher (doesProxyContextMatchUrl):
// proxy keys are checked in insertion order; a key starting with "^" is a
// RegExp, anything else is a prefix match. First match wins.

function matchProxyEntry(url: string): { proxyTimeout?: number } | undefined {
  const proxy =
    (viteConfig as { server?: { proxy?: Record<string, unknown> } }).server?.proxy ?? {}
  for (const context of Object.keys(proxy)) {
    const matches = context.startsWith('^')
      ? new RegExp(context).test(url)
      : url.startsWith(context)
    if (matches) return proxy[context] as { proxyTimeout?: number }
  }
  return undefined
}

describe('dev proxy timeouts (→2626)', () => {
  it('AI suggest routes get a window long enough for a normal AI response', () => {
    const urls = [
      // spec clarity suggest, encoded and raw absolute spec paths
      '/api/specs/%2FUsers%2Ftori%2F.youros%2Fspecs%2Ffoo.md/clarity/suggest',
      '/api/specs//Users/tori/.youros/specs/foo.md/clarity/suggest',
      // task clarify suggest (same panel in task mode)
      '/api/tasks/task-123/clarify/suggest',
      // spec wizard suggest (same latency profile, same 502 failure mode)
      '/api/specs/wizard/suggest',
    ]
    for (const url of urls) {
      const entry = matchProxyEntry(url)
      expect(entry, `no proxy entry matched ${url}`).toBeDefined()
      expect(
        entry!.proxyTimeout,
        `proxyTimeout for ${url} must comfortably exceed a normal 5-7s AI response`
      ).toBeGreaterThanOrEqual(60_000)
    }
  })

  it('ordinary /api routes keep the fast 5s timeout so a wedged backend still fails fast', () => {
    const entry = matchProxyEntry('/api/agents')
    expect(entry).toBeDefined()
    expect(entry!.proxyTimeout).toBe(5000)
  })
})
