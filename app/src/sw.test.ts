/**
 * Regression tests for the service worker caching fix (needle 371).
 *
 * Problem: the service worker registered in dev mode and used cache-first
 * for ALL non-API requests, including HTML navigation. After a restart,
 * the browser served stale cached index.html and JS bundles. Users had
 * to hard-refresh every time.
 *
 * Fix:
 * 1. main.tsx only registers the SW in production (import.meta.env.PROD)
 * 2. sw.js uses network-first for navigation requests so deploys and
 *    restarts always serve the latest HTML
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const mainTsx = readFileSync(resolve(__dirname, 'main.tsx'), 'utf-8')
const swJs = readFileSync(resolve(__dirname, '../public/sw.js'), 'utf-8')

describe('service worker registration (main.tsx)', () => {
  it('only registers the service worker in production mode', () => {
    // The registration must be gated behind import.meta.env.PROD
    expect(mainTsx).toContain('import.meta.env.PROD')

    // There must NOT be an unconditional registration that only checks
    // for serviceWorker support without a production guard
    const lines = mainTsx.split('\n')
    const regLine = lines.find(l => l.includes("serviceWorker.register"))
    expect(regLine).toBeDefined()

    // The PROD check must appear BEFORE the register call (same block)
    const prodIndex = mainTsx.indexOf('import.meta.env.PROD')
    const registerIndex = mainTsx.indexOf("serviceWorker.register")
    expect(prodIndex).toBeLessThan(registerIndex)
  })
})

describe('service worker fetch strategy (sw.js)', () => {
  it('uses network-first for navigation requests', () => {
    // The SW must check request.mode === "navigate" and route to
    // networkFirstThenCache, not cacheFirstThenNetwork
    expect(swJs).toContain("event.request.mode === 'navigate'")

    // After the navigate check, the handler must call networkFirstThenCache
    const navigateSection = swJs.substring(
      swJs.indexOf("event.request.mode === 'navigate'")
    )
    const nextRespondWith = navigateSection.split('\n').find(l =>
      l.includes('respondWith')
    )
    expect(nextRespondWith).toContain('networkFirstThenCache')
  })

  it('uses cache-first only for static assets, not navigation', () => {
    // cacheFirstThenNetwork must NOT be called for navigate requests
    // Find the navigate block and confirm it does NOT use cacheFirst
    const navigateIdx = swJs.indexOf("event.request.mode === 'navigate'")
    const afterNavigate = swJs.substring(navigateIdx, navigateIdx + 200)
    expect(afterNavigate).not.toContain('cacheFirstThenNetwork')
  })

  it('bumped the shell cache version to invalidate stale caches', () => {
    // The old cache was myos-shell-v1 which had the stale index.html.
    // The new version must be v2 or higher so activate() cleans up v1.
    expect(swJs).toMatch(/SHELL_CACHE\s*=\s*'myos-shell-v[2-9]/)
  })
})
