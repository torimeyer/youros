import { useEffect, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import { useSessionsStore, type SessionSnapshot } from '../stores/sessionsStore'
import { api } from '../lib/api'

const FALLBACK_DELAY_MS = 3000
const FALLBACK_POLL_MS = 10000

interface SessionsFrame {
  type: 'snapshot' | 'delta' | 'ping' | string
  count?: number
  sessions?: SessionSnapshot[]
}

export function useSessionsFeed() {
  const { lastMessage, isConnected } = useWebSocket('/api/ws/sessions/state', true)
  const setSnapshot = useSessionsStore((s) => s.setSnapshot)
  const setConnected = useSessionsStore((s) => s.setConnected)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Apply incoming WS frames to the store
  useEffect(() => {
    if (!lastMessage) return
    const frame = lastMessage as unknown as SessionsFrame
    if (
      (frame.type === 'snapshot' || frame.type === 'delta') &&
      typeof frame.count === 'number'
    ) {
      setSnapshot(frame.count, frame.sessions ?? [])
    }
  }, [lastMessage, setSnapshot])

  // Mirror WS connected state; start/stop REST fallback on disconnect
  useEffect(() => {
    setConnected(isConnected)

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

    // Socket down: start fallback poll after FALLBACK_DELAY_MS
    fallbackTimerRef.current = setTimeout(() => {
      fallbackTimerRef.current = null
      if (pollRef.current !== null) return
      pollRef.current = setInterval(async () => {
        try {
          const data = await api.get<{
            count?: number
            active_count?: number
            sessions?: SessionSnapshot[]
          }>('/sessions/active')
          setSnapshot(data.count ?? data.active_count ?? 0, data.sessions ?? [])
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
  }, [isConnected, setConnected, setSnapshot])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current !== null) clearInterval(pollRef.current)
      if (fallbackTimerRef.current !== null) clearTimeout(fallbackTimerRef.current)
    }
  }, [])
}
