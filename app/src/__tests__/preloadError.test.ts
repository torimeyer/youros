import { describe, it, expect, vi, beforeEach } from 'vitest'
import { installVitePreloadRecovery } from '../lib/vitePreloadRecovery'

// Vite fires 'vite:preloadError' when a lazy dynamic-import chunk fails to
// fetch (stale hash after a dev-server restart). The recovery handler should:
//   1. Trigger a single full-page reload so the tab gets fresh chunk hashes.
//   2. NOT reload a second time if the event fires again (loop guard).
//   3. NOT reload if sessionStorage already marks this session as reloaded.

describe('vite:preloadError recovery', () => {
  beforeEach(() => {
    // Each test starts with a clean sessionStorage so guards are independent.
    sessionStorage.clear()
  })

  it('calls reload once when vite:preloadError fires', () => {
    const reloadSpy = vi.fn()
    installVitePreloadRecovery(reloadSpy)

    window.dispatchEvent(new Event('vite:preloadError'))

    expect(reloadSpy).toHaveBeenCalledTimes(1)
  })

  it('does not call reload a second time if the event fires again (loop guard)', () => {
    const reloadSpy = vi.fn()
    installVitePreloadRecovery(reloadSpy)

    window.dispatchEvent(new Event('vite:preloadError'))
    window.dispatchEvent(new Event('vite:preloadError'))

    // The listener is one-shot; a second dispatch must not cause a second reload.
    expect(reloadSpy).toHaveBeenCalledTimes(1)
  })

  it('does not call reload if the session was already reloaded (cross-reload loop guard)', () => {
    // Simulate: page reloaded once already, flag persists in sessionStorage.
    sessionStorage.setItem('_vitePreloadReloaded', '1')
    const reloadSpy = vi.fn()
    installVitePreloadRecovery(reloadSpy)

    window.dispatchEvent(new Event('vite:preloadError'))

    expect(reloadSpy).not.toHaveBeenCalled()
  })
})
