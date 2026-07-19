import { useEffect } from 'react'
import { useCalendarStore, type CalendarEvent } from '../stores/calendarStore'
import { subscribeSharedSocket } from '../lib/sharedSocket'
import { reportError } from '../lib/reportError'

const POLL_MS = 5000

export function useCalendarFeed() {
  useEffect(() => {
    let pollTimer: ReturnType<typeof setTimeout> | null = null
    let controller: AbortController | null = null
    let cancelled = false
    let backoff = 0
    let wsUp = false

    const stopPolling = () => {
      if (pollTimer) {
        clearTimeout(pollTimer)
        pollTimer = null
      }
      if (controller) {
        controller.abort()
        controller = null
      }
    }

    const tick = async () => {
      if (cancelled) return
      controller = new AbortController()
      try {
        const res = await fetch('/api/calendar/events', { signal: controller.signal })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = await res.json()
        useCalendarStore.setState({
          events: data.events ? data.events : [],
        })
        backoff = 0
      } catch (e: unknown) {
        if (e instanceof Error && e.name === 'AbortError') return
        reportError('Calendar poll error', e)
        backoff = backoff === 0 ? 1000 : Math.min(backoff * 2, 60_000)
      }
      if (!cancelled && !document.hidden) {
        pollTimer = setTimeout(tick, backoff || POLL_MS)
      }
    }

    const startPolling = () => {
      if (pollTimer || cancelled || document.hidden) return
      tick()
    }

    // Share the single calendar socket. The shared manager owns reconnect
    // timing, so a dropped socket can never leave two live connections for
    // this channel. The HTTP poll below only runs while the socket is down.
    const unsubscribe = subscribeSharedSocket('/api/ws/calendar/events', {
      onOpen: () => {
        wsUp = true
        useCalendarStore.setState({ wsConnected: true })
        stopPolling()
      },
      onMessage: (msg) => {
        const m = msg as { type?: string; events?: CalendarEvent[] }
        if (m.type === 'snapshot') {
          useCalendarStore.setState({
            events: m.events || [],
          })
        }
      },
      onClose: () => {
        wsUp = false
        useCalendarStore.setState({ wsConnected: false })
        startPolling()
      },
    })

    // →2982: no polling while the tab is hidden. When the socket is up it
    // keeps the data fresh anyway; when it is down, resume the poll as soon
    // as the user comes back to the tab.
    const onVisibilityChange = () => {
      if (document.hidden) {
        stopPolling()
      } else if (!wsUp) {
        startPolling()
      }
    }
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      cancelled = true
      document.removeEventListener('visibilitychange', onVisibilityChange)
      stopPolling()
      unsubscribe()
    }
  }, [])
}
