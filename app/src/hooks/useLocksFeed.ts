import { useEffect, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import { useLocksStore, type LockSnapshot } from '../stores/locksStore'
import { api } from '../lib/api'

const FALLBACK_POLL_MS = 10_000
const FALLBACK_DELAY_MS = 3_000

interface LocksFrame {
  type: 'snapshot' | 'delta' | 'ping' | string
  locks?: LockSnapshot[]
}

export function useLocksFeed() {
  const { lastMessage, isConnected } = useWebSocket('/api/ws/locks/state', true)
  const setLocks = useLocksStore((s) => s.setLocks)
  const setWsConnected = useLocksStore((s) => s.setWsConnected)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Apply incoming WS frames to the store
  useEffect(() => {
    if (!lastMessage) return
    const frame = lastMessage as unknown as LocksFrame
    if (
      (frame.type === 'snapshot' || frame.type === 'delta') &&
      Array.isArray(frame.locks)
    ) {
      setLocks(frame.locks)
    }
  }, [lastMessage, setLocks])

  // Mirror WS connected state; start/stop HTTP fallback on disconnect
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

    // Socket down: start fallback poll after FALLBACK_DELAY_MS
    fallbackTimerRef.current = setTimeout(() => {
      fallbackTimerRef.current = null
      if (pollRef.current !== null) return
      pollRef.current = setInterval(async () => {
        try {
          const data = await api.get<{ locks?: LockSnapshot[] }>('/agents/locks')
          setLocks(data.locks ?? [])
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
  }, [isConnected, setWsConnected, setLocks])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current !== null) clearInterval(pollRef.current)
      if (fallbackTimerRef.current !== null) clearTimeout(fallbackTimerRef.current)
    }
  }, [])
}
