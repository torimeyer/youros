import { useEffect, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import { useGrantsStore, type GrantRequest } from '../stores/grantsStore'
import { api } from '../lib/api'

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
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
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
          const data = await api.get<{ grants?: GrantRequest[] }>('/agents/grants?status=pending')
          setGrants(data.grants ?? [])
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
  }, [isConnected, setConnected, setGrants])

  useEffect(() => {
    return () => {
      if (pollRef.current !== null) clearInterval(pollRef.current)
      if (fallbackTimerRef.current !== null) clearTimeout(fallbackTimerRef.current)
    }
  }, [])
}
