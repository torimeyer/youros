import { useEffect, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import { useSpecCountsStore } from '../stores/specCounts'
import { api } from '../lib/api'

const FALLBACK_DELAY_MS = 3000
const FALLBACK_POLL_MS = 2000

interface SpecStateFrame {
  type: 'snapshot' | 'ping' | string
  unfinished?: number
  total?: number
}

export function useSpecCountsFeed() {
  const { lastMessage, isConnected } = useWebSocket('/api/ws/specs/state', true)
  const setSnapshot = useSpecCountsStore((s) => s.setSnapshot)
  const setConnected = useSpecCountsStore((s) => s.setConnected)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!lastMessage) return
    const frame = lastMessage as unknown as SpecStateFrame
    if (frame.type === 'snapshot' && typeof frame.unfinished === 'number') {
      setSnapshot(frame.unfinished, frame.total ?? 0)
    }
  }, [lastMessage, setSnapshot])

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

    fallbackTimerRef.current = setTimeout(() => {
      fallbackTimerRef.current = null
      if (pollRef.current !== null) return
      pollRef.current = setInterval(async () => {
        try {
          const data = await api.get<{ unfinished: number; total: number }>('/specs/counts')
          setSnapshot(data.unfinished ?? 0, data.total ?? 0)
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

  useEffect(() => {
    return () => {
      if (pollRef.current !== null) clearInterval(pollRef.current)
      if (fallbackTimerRef.current !== null) clearTimeout(fallbackTimerRef.current)
    }
  }, [])
}
