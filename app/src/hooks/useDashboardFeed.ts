import { useEffect } from 'react'
import { useDashboardStore } from '../stores/dashboardStore'
import { reportError } from '../lib/reportError'

const POLL_MS = 5000

export function useDashboardFeed() {
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
        const res = await fetch('/api/dashboard', { signal: controller.signal })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = await res.json()
        useDashboardStore.setState({
          agentsCount: data.counts?.open || 0,
          tasksCount: data.counts?.open || 0,
        })
        backoff = 0
      } catch (e: unknown) {
        if (e instanceof Error && e.name === 'AbortError') return
        reportError('Dashboard poll error', e)
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
      const url = `${protocol}//${window.location.host}/api/ws/dashboard/data`

      ws = new WebSocket(url)

      ws.onopen = () => {
        useDashboardStore.setState({ wsConnected: true })
        stopPolling()
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
          reportError('Dashboard WS message parse error', e)
        }
      }

      ws.onerror = () => {
        useDashboardStore.setState({ wsConnected: false })
      }

      ws.onclose = () => {
        useDashboardStore.setState({ wsConnected: false })
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
