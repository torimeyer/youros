import { useEffect } from 'react'
import { bumpAll } from '../lib/sidebarBus'

// Throttle window in ms. Typing fires a keydown per character; without
// a floor we would call bumpAll() every few ms during a burst of typing
// and overwhelm the sidebar subscribers (and, through onAgentsChange and
// friends, the /api/agents and /api/tasks endpoints that page components
// refetch on bump). One second feels instant to humans and still caps
// the event rate at 1/s no matter how fast the user types.
const THROTTLE_MS = 1000

// Events that indicate the user is active in the app. Keystrokes and
// mouse movement are the obvious ones. focus fires when a window comes
// back to the foreground, which is exactly when the user wants to see
// fresh data. visibilitychange covers tab-switching (the browser does
// not fire focus when switching between tabs of the same window on some
// platforms). pointerdown catches clicks and taps on devices where
// mousemove is not emitted (touch screens). All handlers share one
// throttled bump, so cost is independent of how many events fire.
type ActivityEvent = 'keydown' | 'mousemove' | 'pointerdown' | 'focus'

// useUserActivity wires a single throttled bumpAll() to every signal
// that the user is interacting with the app. Mount it once at the top
// of the app (Layout). It is intentionally event-listener-only: no
// timers, no React state, no re-renders, so the cost when idle is zero.
export function useUserActivity(): void {
  useEffect(() => {
    // Last time we called bumpAll. Tracked in a closure-local variable
    // rather than a ref because we never need to expose it.
    let lastBump = 0

    const maybeBump = (): void => {
      const now = Date.now()
      if (now - lastBump < THROTTLE_MS) return
      lastBump = now
      bumpAll()
    }

    // Window-level events. Bound with passive: true so the browser
    // never waits on our handler (it does no preventDefault) and we
    // do not block scroll or input on slow hardware.
    const windowEvents: ActivityEvent[] = ['keydown', 'mousemove', 'pointerdown', 'focus']
    for (const name of windowEvents) {
      window.addEventListener(name, maybeBump, { passive: true })
    }

    // Document visibility change: fires when the user switches back to
    // this tab. Separate because it lives on document, not window.
    const onVisibility = (): void => {
      if (document.visibilityState === 'visible') maybeBump()
    }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      for (const name of windowEvents) {
        window.removeEventListener(name, maybeBump)
      }
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])
}
