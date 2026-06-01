import { useEffect } from 'react'
import { useDashboardStore } from '../stores/dashboardStore'
import { subscribeSharedSocket } from '../lib/sharedSocket'
import { reportError } from '../lib/reportError'

const POLL_MS = 5000

export function useDashboardFeed() {
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

    // Share the single dashboard socket. This feed is mounted both at the
    // app shell and on the dashboard page; the shared manager makes both
    // mounts ride on ONE connection instead of opening a second.
    const unsubscribe = subscribeSharedSocket('/api/ws/dashboard/data', {
      onOpen: () => {
        useDashboardStore.setState({ wsConnected: true })
        stopPolling()
      },
      onMessage: (msg) => {
        const m = msg as {
          type?: string
          agents_count?: number
          tasks_count?: number
          last_sync_at?: string
        }
        if (m.type === 'snapshot') {
          useDashboardStore.setState({
            agentsCount: m.agents_count || 0,
            tasksCount: m.tasks_count || 0,
            lastSyncAt: m.last_sync_at,
          })
        }
      },
      onClose: () => {
        useDashboardStore.setState({ wsConnected: false })
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
