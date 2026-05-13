import { useEffect, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import { useGrantsStore, type GrantRequest } from '../stores/grantsStore'

const FALLBACK_POLL_MS = 5000
const FALLBACK_DELAY_MS = 3000

interface GrantsFrame {
  type: 'snapshot' | 'delta' | 'ping' | string
  grants?: GrantRequest[]
}

export function useGrantsFeed() {
  const { lastMessage, isConnected } = useWebSocket('/api/ws/grants/state', true)
  const setGrants = useGrantsStore((s) => s.setGrants)
  const setConnected = useGrantsStore((s) => s.setConnected)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pollAbortRef = useRef<AbortController | null>(null)
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!lastMessage) return
    const frame = lastMessage as unknown as GrantsFrame
    if ((frame.type === 'snapshot' || frame.type === 'delta') && Array.isArray(frame.grants)) {
      setGrants(frame.grants)
    }
  }, [lastMessage, setGrants])

  useEffect(() => {
    setConnected(isConnected)

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

    fallbackTimerRef.current = setTimeout(() => {
      fallbackTimerRef.current = null
      if (pollRef.current !== null) return
      let backoff = 0

      const tick = async () => {
        const controller = new AbortController()
        pollAbortRef.current = controller
        try {
          const res = await fetch('/api/agents/grants?status=pending', { signal: controller.signal })
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          const data = (await res.json()) as { grants?: GrantRequest[] }
          setGrants(data.grants ?? [])
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
  }, [isConnected, setConnected, setGrants])

  useEffect(() => {
    return () => {
      if (pollRef.current !== null) clearTimeout(pollRef.current)
      if (fallbackTimerRef.current !== null) clearTimeout(fallbackTimerRef.current)
      if (pollAbortRef.current !== null) pollAbortRef.current.abort()
    }
  }, [])
}
