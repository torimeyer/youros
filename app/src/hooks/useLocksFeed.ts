import { useEffect, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import { useLocksStore, type LockSnapshot } from '../stores/locksStore'

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
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pollAbortRef = useRef<AbortController | null>(null)
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
        clearTimeout(pollRef.current)
        pollRef.current = null
      }
      if (pollAbortRef.current !== null) {
        pollAbortRef.current.abort()
        pollAbortRef.current = null
      }
      return
    }

    // Socket down: start fallback poll after FALLBACK_DELAY_MS
    fallbackTimerRef.current = setTimeout(() => {
      fallbackTimerRef.current = null
      if (pollRef.current !== null) return
      let backoff = 0

      const tick = async () => {
        const controller = new AbortController()
        pollAbortRef.current = controller
        try {
          const res = await fetch('/api/agents/locks', { signal: controller.signal })
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          const data = (await res.json()) as { locks?: LockSnapshot[] }
          setLocks(data.locks ?? [])
          backoff = 0
        } catch (e: unknown) {
          if (controller.signal.aborted) return
          backoff = backoff === 0 ? 1000 : Math.min(backoff * 2, 30_000)
        }
        if (!controller.signal.aborted) {
          pollRef.current = setTimeout(tick, backoff || FALLBACK_POLL_MS)
        }
      }

      tick()
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
      if (pollRef.current !== null) clearTimeout(pollRef.current)
      if (fallbackTimerRef.current !== null) clearTimeout(fallbackTimerRef.current)
      if (pollAbortRef.current !== null) pollAbortRef.current.abort()
    }
  }, [])
}
