import { useEffect, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import { useRunningAgentsStore, type RunningAgent, type TerminatedAgent } from '../stores/runningAgents'

const ACTIVE_STATUSES = new Set(['running', 'spawned', 'starting'])
const FALLBACK_DELAY_MS = 3000
const FALLBACK_POLL_MS = 2000

interface AgentStateFrame {
  type: 'snapshot' | 'delta' | 'sweep' | 'ping' | string
  running_count?: number
  agents?: RunningAgent[]
  changed?: { name: string; status: string; terminal?: boolean; feedback?: string }
}

export function useRunningAgentsFeed() {
  const { lastMessage, isConnected } = useWebSocket('/api/ws/agents/state', true)
  const setSnapshot = useRunningAgentsStore((s) => s.setSnapshot)
  const setConnected = useRunningAgentsStore((s) => s.setConnected)
  const setTerminatedAgent = useRunningAgentsStore((s) => s.setTerminatedAgent)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pollAbortRef = useRef<AbortController | null>(null)
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Apply incoming WS frames to the store
  useEffect(() => {
    if (!lastMessage) return
    const frame = lastMessage as unknown as AgentStateFrame
    if (
      (frame.type === 'snapshot' || frame.type === 'delta' || frame.type === 'sweep') &&
      typeof frame.running_count === 'number'
    ) {
      setSnapshot(frame.running_count, frame.agents ?? [])
    }
    if (
      frame.type === 'delta' &&
      frame.changed?.terminal === true &&
      frame.changed.feedback
    ) {
      setTerminatedAgent({
        name: frame.changed.name,
        status: frame.changed.status,
        feedback: frame.changed.feedback,
      } as TerminatedAgent)
    }
  }, [lastMessage, setSnapshot, setTerminatedAgent])

  // Mirror WS connected state; start/stop REST fallback on disconnect
  useEffect(() => {
    setConnected(isConnected)

    if (isConnected) {
      // Socket reconnected: cancel pending fallback timer and stop polling
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
          const res = await fetch('/api/agents?user_spawned_only=true', { signal: controller.signal })
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          const data = (await res.json()) as { agents?: RunningAgent[] } | RunningAgent[]
          const list: RunningAgent[] = Array.isArray(data)
            ? data
            : ((data as { agents?: RunningAgent[] }).agents ?? [])
          const running = list.filter((a) => ACTIVE_STATUSES.has(a.status))
          setSnapshot(running.length, running)
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

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current !== null) clearTimeout(pollRef.current)
      if (fallbackTimerRef.current !== null) clearTimeout(fallbackTimerRef.current)
      if (pollAbortRef.current !== null) pollAbortRef.current.abort()
    }
  }, [])
}
