import { useCallback, useEffect, useRef, useState } from 'react'
import { useRunningAgentsStore, type RunningAgent, type TerminatedAgent } from '../stores/runningAgents'
import { api } from '../lib/api'

const ACTIVE_STATUSES = new Set(['running', 'spawned', 'starting'])
const FALLBACK_DELAY_MS = 3000
const FALLBACK_POLL_MS = 2000
// A burst of agent events (spawn wave, sweep) collapses into one refetch.
const REFETCH_COALESCE_MS = 200

// →2946: the Running Agents panel subscribes to the consolidated backend
// event stream (every domain publishes there) instead of the old
// agents-only WebSocket. Agent events carry type "agent.*".
export const EVENTS_STREAM_PATH = '/api/events'

interface BusEvent {
  type: string
  payload?: {
    name?: string
    status?: string
    terminal?: boolean
    feedback?: string
  }
}

export function useRunningAgentsFeed() {
  const setSnapshot = useRunningAgentsStore((s) => s.setSnapshot)
  const setConnected = useRunningAgentsStore((s) => s.setConnected)
  const setTerminatedAgent = useRunningAgentsStore((s) => s.setTerminatedAgent)
  const [isConnected, setIsConnected] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refetchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const prevAgentsRef = useRef<RunningAgent[]>([])

  // The stream carries event notifications, not agent lists; the list
  // itself comes from the REST endpoint on every agent event.
  const refetchSnapshot = useCallback(async () => {
    try {
      const data = await api.get<{ agents?: RunningAgent[] } | RunningAgent[]>(
        '/agents?user_spawned_only=true',
      )
      const list: RunningAgent[] = Array.isArray(data)
        ? data
        : ((data as { agents?: RunningAgent[] }).agents ?? [])
      const running = list.filter((a) => ACTIVE_STATUSES.has(a.status))
      // Skip setSnapshot when the agent list is identical. list.filter()
      // always returns a new array reference, which would cause storeAgents
      // to change every tick even when nothing is running (→1730).
      const prev = prevAgentsRef.current
      const changed =
        prev.length !== running.length ||
        running.some((a, i) => a.name !== prev[i]?.name || a.status !== prev[i]?.status)
      if (!changed) return
      prevAgentsRef.current = running
      setSnapshot(running.length, running)
    } catch {
      // network error — the next event or poll tick retries
    }
  }, [setSnapshot])

  const scheduleRefetch = useCallback(() => {
    if (refetchTimerRef.current !== null) return
    refetchTimerRef.current = setTimeout(() => {
      refetchTimerRef.current = null
      void refetchSnapshot()
    }, REFETCH_COALESCE_MS)
  }, [refetchSnapshot])

  // Subscribe to the consolidated event stream (SSE).
  useEffect(() => {
    if (typeof EventSource === 'undefined') {
      // No SSE in this environment (jsdom, very old browsers): the REST
      // fallback below keeps the panel populated.
      return
    }
    const es = new EventSource(EVENTS_STREAM_PATH)
    es.onopen = () => {
      setIsConnected(true)
      // The stream sends no initial snapshot; fetch one so the panel is
      // fresh immediately on connect and reconnect.
      void refetchSnapshot()
    }
    es.onerror = () => {
      // EventSource reconnects on its own; flag the gap so the REST
      // fallback keeps the panel fresh meanwhile.
      setIsConnected(false)
    }
    es.onmessage = (event) => {
      let frame: BusEvent
      try {
        frame = JSON.parse(event.data) as BusEvent
      } catch {
        return // keepalives and malformed frames carry no agent state
      }
      if (!frame.type || !frame.type.startsWith('agent.')) return
      if (
        frame.type === 'agent.delta' &&
        frame.payload?.terminal === true &&
        frame.payload.feedback
      ) {
        setTerminatedAgent({
          name: frame.payload.name ?? '',
          status: frame.payload.status ?? '',
          feedback: frame.payload.feedback,
        } as TerminatedAgent)
      }
      scheduleRefetch()
    }
    return () => {
      es.close()
      if (refetchTimerRef.current !== null) {
        clearTimeout(refetchTimerRef.current)
        refetchTimerRef.current = null
      }
    }
  }, [refetchSnapshot, scheduleRefetch, setTerminatedAgent])

  // Mirror stream connected state; start/stop REST fallback on disconnect
  useEffect(() => {
    setConnected(isConnected)

    if (isConnected) {
      // Stream reconnected: cancel pending fallback timer and stop polling
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

    // Stream down: start fallback poll after FALLBACK_DELAY_MS
    fallbackTimerRef.current = setTimeout(() => {
      fallbackTimerRef.current = null
      if (pollRef.current !== null) return
      pollRef.current = setInterval(() => {
        void refetchSnapshot()
      }, FALLBACK_POLL_MS)
    }, FALLBACK_DELAY_MS)

    return () => {
      if (fallbackTimerRef.current !== null) {
        clearTimeout(fallbackTimerRef.current)
        fallbackTimerRef.current = null
      }
    }
  }, [isConnected, setConnected, refetchSnapshot])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current !== null) clearInterval(pollRef.current)
      if (fallbackTimerRef.current !== null) clearTimeout(fallbackTimerRef.current)
      if (refetchTimerRef.current !== null) clearTimeout(refetchTimerRef.current)
    }
  }, [])
}
