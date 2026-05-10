import { useEffect } from 'react'
import { useCalendarStore } from '../stores/calendarStore'

export function useCalendarFeed() {
  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let pollTimer: ReturnType<typeof setInterval> | null = null

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const url = `${protocol}//${window.location.host}/ws/calendar/events`

      ws = new WebSocket(url)

      ws.onopen = () => {
        useCalendarStore.setState({ wsConnected: true })
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'snapshot') {
            useCalendarStore.setState({
              events: msg.events || [],
            })
          } else if (msg.type === 'ping') {
            // Keepalive received
          }
        } catch (e) {
          console.error('Calendar WS message parse error:', e)
        }
      }

      ws.onerror = () => {
        useCalendarStore.setState({ wsConnected: false })
      }

      ws.onclose = () => {
        useCalendarStore.setState({ wsConnected: false })
        // Fallback to polling
        startPolling()
        // Try to reconnect after 5s
        reconnectTimer = setTimeout(connect, 5000)
      }
    }

    const startPolling = () => {
      pollTimer = setInterval(async () => {
        try {
          const res = await fetch('/api/calendar/events')
          if (!res.ok) return
          const data = await res.json()
          useCalendarStore.setState({
            events: data.events ? data.events : [],
          })
        } catch (e) {
          console.error('Calendar poll error:', e)
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
