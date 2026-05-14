import { useEffect } from 'react'
import { useNotificationsStore } from '../stores/notificationsStore'

const POLL_MS = 5000

export function useNotificationsFeed() {
  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
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
        console.error('Notifications poll error:', e)
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

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const url = `${protocol}//${window.location.host}/ws/notifications`

      ws = new WebSocket(url)

      ws.onopen = () => {
        useNotificationsStore.setState({ wsConnected: true })
        stopPolling()
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'snapshot') {
            useNotificationsStore.setState({
              notifications: msg.notifications || [],
              // Signal that the initial snapshot has been delivered so
              // TopBar can seed seenNotifIdsRef from the real list, not
              // from an empty Set that was built before the WS connected.
              snapshotReceived: true,
            })
          } else if (msg.type === 'ping') {
            // Keepalive received
          }
        } catch (e) {
          console.error('Notifications WS message parse error:', e)
        }
      }

      ws.onerror = () => {
        useNotificationsStore.setState({ wsConnected: false })
      }

      ws.onclose = () => {
        useNotificationsStore.setState({ wsConnected: false })
        startPolling()
        reconnectTimer = setTimeout(connect, 5000)
      }
    }

    connect()

    return () => {
      cancelled = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      stopPolling()
      if (ws) ws.close()
    }
  }, [])
}
