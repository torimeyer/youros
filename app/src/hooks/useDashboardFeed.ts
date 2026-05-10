import { useEffect } from 'react'
import { useDashboardStore } from '../stores/dashboardStore'

export function useDashboardFeed() {
  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let pollTimer: ReturnType<typeof setInterval> | null = null

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const url = `${protocol}//${window.location.host}/ws/dashboard/data`

      ws = new WebSocket(url)

      ws.onopen = () => {
        useDashboardStore.setState({ wsConnected: true })
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'snapshot') {
            useDashboardStore.setState({
              agentsCount: msg.agents_count || 0,
              tasksCount: msg.tasks_count || 0,
              lastSyncAt: msg.last_sync_at,
            })
          } else if (msg.type === 'ping') {
            // Keepalive received
          }
        } catch (e) {
          console.error('Dashboard WS message parse error:', e)
        }
      }

      ws.onerror = () => {
        useDashboardStore.setState({ wsConnected: false })
      }

      ws.onclose = () => {
        useDashboardStore.setState({ wsConnected: false })
        // Fallback to polling
        startPolling()
        // Try to reconnect after 5s
        reconnectTimer = setTimeout(connect, 5000)
      }
    }

    const startPolling = () => {
      pollTimer = setInterval(async () => {
        try {
          const res = await fetch('/api/dashboard')
          if (!res.ok) return
          const data = await res.json()
          useDashboardStore.setState({
            agentsCount: data.counts?.open || 0,
            tasksCount: data.counts?.open || 0,
          })
        } catch (e) {
          console.error('Dashboard poll error:', e)
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
