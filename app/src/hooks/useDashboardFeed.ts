import { useEffect, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import { useDashboardStore } from '../stores/dashboardStore'
import { api } from '../lib/api'

const FALLBACK_POLL_MS = 5000
const FALLBACK_DELAY_MS = 3000

interface DashboardFrame {
  type: 'snapshot' | 'delta' | 'ping' | string
  active_agents?: number
  running_tasks?: number
}

export function useDashboardFeed() {
  const { lastMessage, isConnected } = useWebSocket('/api/ws/dashboard/data', true)
  const setAgentsCount = useDashboardStore((s) => s.setAgentsCount)
  const setTasksCount = useDashboardStore((s) => s.setTasksCount)
  const setWsConnected = useDashboardStore((s) => s.setWsConnected)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!lastMessage) return
    const frame = lastMessage as unknown as DashboardFrame
    if (frame.type === 'snapshot' || frame.type === 'delta') {
      if (typeof frame.active_agents === 'number') setAgentsCount(frame.active_agents)
      if (typeof frame.running_tasks === 'number') setTasksCount(frame.running_tasks)
    }
  }, [lastMessage, setAgentsCount, setTasksCount])

  useEffect(() => {
    setWsConnected(isConnected)

    if (isConnected) {
      if (fallbackTimerRef.current !== null) {
        clearTimeout(fallbackTimerRef.current)
        fallbackTimerRef.current = null
      }
      if (pollRef.current !== null) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
      return
    }

    fallbackTimerRef.current = setTimeout(() => {
      fallbackTimerRef.current = null
      if (pollRef.current !== null) return
      pollRef.current = setInterval(async () => {
        try {
          const data = await api.get<DashboardFrame>('/coordination/dashboard')
          if (typeof data.active_agents === 'number') setAgentsCount(data.active_agents)
          if (typeof data.running_tasks === 'number') setTasksCount(data.running_tasks)
        } catch {
          // network error during fallback — ignore, retry next interval
        }
      }, FALLBACK_POLL_MS)
    }, FALLBACK_DELAY_MS)

    return () => {
      if (fallbackTimerRef.current !== null) {
        clearTimeout(fallbackTimerRef.current)
        fallbackTimerRef.current = null
      }
    }
  }, [isConnected, setWsConnected, setAgentsCount, setTasksCount])

  useEffect(() => {
    return () => {
      if (pollRef.current !== null) clearInterval(pollRef.current)
      if (fallbackTimerRef.current !== null) clearTimeout(fallbackTimerRef.current)
    }
  }, [])
}
