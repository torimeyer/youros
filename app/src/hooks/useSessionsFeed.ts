import { useEffect, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import { useSessionsStore, type SessionRow, type LockRow, type EventRow } from '../stores/sessionsStore'
import { api } from '../lib/api'

const FALLBACK_DELAY_MS = 3000
const FALLBACK_POLL_MS = 5000

interface SessionStateFrame {
  type: 'snapshot' | 'ping' | string
  sessions?: SessionRow[]
  locks?: LockRow[]
  events?: EventRow[]
}

export function useSessionsFeed() {
  const { lastMessage, isConnected } = useWebSocket('/api/ws/sessions/state', true)
  const setSnapshot = useSessionsStore((s) => s.setSnapshot)
  const setConnected = useSessionsStore((s) => s.setConnected)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!lastMessage) return
    const frame = lastMessage as unknown as SessionStateFrame
    if (frame.type === 'snapshot' && Array.isArray(frame.sessions)) {
      setSnapshot(frame.sessions, frame.locks ?? [], frame.events ?? [])
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
          const data = await api.get<{ sessions: SessionRow[]; locks: LockRow[]; events: EventRow[] }>(
            '/sessions/coordination',
          )
          setSnapshot(data.sessions ?? [], data.locks ?? [], data.events ?? [])
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
