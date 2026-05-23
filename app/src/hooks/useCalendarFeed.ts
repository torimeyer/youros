import { useEffect } from 'react'
import { useCalendarStore } from '../stores/calendarStore'
import { reportError } from '../lib/reportError'

const POLL_MS = 5000

export function useCalendarFeed() {
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
      const url = `${protocol}//${window.location.host}/ws/calendar/events`

      ws = new WebSocket(url)

      ws.onopen = () => {
        useCalendarStore.setState({ wsConnected: true })
        stopPolling()
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
          reportError('Calendar WS message parse error', e)
        }
      }

      ws.onerror = () => {
        useCalendarStore.setState({ wsConnected: false })
      }

      ws.onclose = () => {
        useCalendarStore.setState({ wsConnected: false })
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
