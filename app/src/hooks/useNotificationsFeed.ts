import { useEffect } from 'react'
import { useNotificationsStore, type Notification } from '../stores/notificationsStore'
import { subscribeSharedSocket } from '../lib/sharedSocket'
import { reportError } from '../lib/reportError'

const POLL_MS = 5000

export function useNotificationsFeed() {
  useEffect(() => {
    let pollTimer: ReturnType<typeof setTimeout> | null = null
    let controller: AbortController | null = null
    let cancelled = false
    let backoff = 0

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
        const res = await fetch('/api/notifications', { signal: controller.signal })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = await res.json()
        useNotificationsStore.setState({
          notifications: Array.isArray(data) ? data : [],
          // Signal that the initial data has arrived so TopBar can seed
          // seenNotifIdsRef from the real list (mirrors WS snapshot path).
          snapshotReceived: true,
        })
        backoff = 0
      } catch (e: unknown) {
        if (e instanceof Error && e.name === 'AbortError') return
        reportError('Notifications poll error', e)
        backoff = backoff === 0 ? 1000 : Math.min(backoff * 2, 60_000)
      }
      if (!cancelled) {
        pollTimer = setTimeout(tick, backoff || POLL_MS)
      }
    }

    const startPolling = () => {
      if (pollTimer || cancelled) return
      tick()
    }

    // Share the single notifications socket. Several places listen to this
    // channel (this feed and the task-finished chime); the shared manager
    // makes sure they all ride on ONE connection instead of opening their
    // own. The HTTP poll below only runs while that socket is down.
    const unsubscribe = subscribeSharedSocket('/api/ws/notifications', {
      onOpen: () => {
        useNotificationsStore.setState({ wsConnected: true })
        stopPolling()
      },
      onMessage: (msg) => {
        const m = msg as { type?: string; notifications?: Notification[] }
        if (m.type === 'snapshot') {
          useNotificationsStore.setState({
            notifications: m.notifications || [],
            // Signal that the initial snapshot has been delivered so
            // TopBar can seed seenNotifIdsRef from the real list, not
            // from an empty Set that was built before the WS connected.
            snapshotReceived: true,
          })
        }
      },
      onClose: () => {
        useNotificationsStore.setState({ wsConnected: false })
        startPolling()
      },
    })

    return () => {
      cancelled = true
      stopPolling()
      unsubscribe()
    }
  }, [])
}
