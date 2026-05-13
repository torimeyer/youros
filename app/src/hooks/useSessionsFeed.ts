import { useEffect, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import { useSessionsStore, type SessionRow, type LockRow, type EventRow } from '../stores/sessionsStore'

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
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pollAbortRef = useRef<AbortController | null>(null)
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
          const res = await fetch('/api/sessions/coordination', { signal: controller.signal })
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          const data = (await res.json()) as { sessions: SessionRow[]; locks: LockRow[]; events: EventRow[] }
          setSnapshot(data.sessions ?? [], data.locks ?? [], data.events ?? [])
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
  }, [isConnected, setConnected, setSnapshot])

  useEffect(() => {
    return () => {
      if (pollRef.current !== null) clearTimeout(pollRef.current)
      if (fallbackTimerRef.current !== null) clearTimeout(fallbackTimerRef.current)
      if (pollAbortRef.current !== null) pollAbortRef.current.abort()
    }
  }, [])
}
