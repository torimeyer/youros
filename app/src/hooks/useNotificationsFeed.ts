import { useEffect } from 'react'
import { useNotificationsStore } from '../stores/notificationsStore'

export function useNotificationsFeed() {
  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let pollTimer: ReturnType<typeof setInterval> | null = null

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const url = `${protocol}//${window.location.host}/ws/notifications`

      ws = new WebSocket(url)

      ws.onopen = () => {
        useNotificationsStore.setState({ wsConnected: true })
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'snapshot') {
            useNotificationsStore.setState({
              notifications: msg.notifications || [],
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
        // Fallback to polling
        startPolling()
        // Try to reconnect after 5s
        reconnectTimer = setTimeout(connect, 5000)
      }
    }

    const startPolling = () => {
      pollTimer = setInterval(async () => {
        try {
          const res = await fetch('/api/notifications')
          if (!res.ok) return
          const data = await res.json()
          useNotificationsStore.setState({
            notifications: Array.isArray(data) ? data : [],
          })
        } catch (e) {
          console.error('Notifications poll error:', e)
        }
      }, 5000)
    }

    connect()

    return () => {
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (pollTimer) clearInterval(pollTimer)
      if (ws) ws.close()
    }
  }, [])
}
