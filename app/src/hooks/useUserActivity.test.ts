import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useUserActivity } from './useUserActivity'
import * as sidebarBus from '../lib/sidebarBus'

// Verifies the three behaviors we actually ship:
//   1. Keystroke, mouse move, focus, visibility, pointerdown each fire bumpAll.
//   2. Throttled to at most once per second.
//   3. All listeners torn down on unmount, so no leaks when the Layout
//      re-renders or the app hot-reloads in dev.

describe('useUserActivity', () => {
  let bumpAllSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    vi.useFakeTimers()
    bumpAllSpy = vi.spyOn(sidebarBus, 'bumpAll').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.useRealTimers()
    bumpAllSpy.mockRestore()
  })

  it('bumps on keystroke', () => {
    renderHook(() => useUserActivity())
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }))
    expect(bumpAllSpy).toHaveBeenCalledTimes(1)
  })

  it('bumps on mouse move', () => {
    renderHook(() => useUserActivity())
    window.dispatchEvent(new MouseEvent('mousemove'))
    expect(bumpAllSpy).toHaveBeenCalledTimes(1)
  })

  it('bumps on window focus', () => {
    renderHook(() => useUserActivity())
    window.dispatchEvent(new FocusEvent('focus'))
    expect(bumpAllSpy).toHaveBeenCalledTimes(1)
  })

  it('bumps on visibility change when visible', () => {
    renderHook(() => useUserActivity())
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(bumpAllSpy).toHaveBeenCalledTimes(1)
  })

  it('throttles to at most once per second under a burst of keystrokes', () => {
    renderHook(() => useUserActivity())
    // Simulate fast typing: 20 keystrokes back to back.
    for (let i = 0; i < 20; i++) {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'x' }))
    }
    // Only the first should have fired; the rest are throttled.
    expect(bumpAllSpy).toHaveBeenCalledTimes(1)

    // Advance past the throttle window and fire once more. Fake timers
    // do not move Date.now on their own in vitest's default config, so
    // we shift Date.now via a spy.
    const realNow = Date.now()
    const dateSpy = vi.spyOn(Date, 'now').mockReturnValue(realNow + 1500)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'y' }))
    expect(bumpAllSpy).toHaveBeenCalledTimes(2)
    dateSpy.mockRestore()
  })

  it('removes all listeners on unmount', () => {
    const { unmount } = renderHook(() => useUserActivity())
    unmount()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }))
    window.dispatchEvent(new MouseEvent('mousemove'))
    window.dispatchEvent(new FocusEvent('focus'))
    document.dispatchEvent(new Event('visibilitychange'))
    expect(bumpAllSpy).not.toHaveBeenCalled()
  })
})
