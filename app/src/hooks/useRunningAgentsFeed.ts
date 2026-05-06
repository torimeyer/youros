import { useEffect, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import { useRunningAgentsStore, type RunningAgent } from '../stores/runningAgents'
import { api } from '../lib/api'

const ACTIVE_STATUSES = new Set(['running', 'spawned', 'starting'])
const FALLBACK_DELAY_MS = 3000
const FALLBACK_POLL_MS = 2000

interface AgentStateFrame {
  type: 'snapshot' | 'delta' | 'sweep' | 'ping' | string
  running_count?: number
  agents?: RunningAgent[]
}

export function useRunningAgentsFeed() {
  const { lastMessage, isConnected } = useWebSocket('/ws/agents/state', true)
  const setSnapshot = useRunningAgentsStore((s) => s.setSnapshot)
  const setConnected = useRunningAgentsStore((s) => s.setConnected)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
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
  }, [lastMessage, setSnapshot])

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
          const data = await api.get<{ agents?: RunningAgent[] } | RunningAgent[]>(
            '/agents?user_spawned_only=true',
          )
          const list: RunningAgent[] = Array.isArray(data)
            ? data
            : ((data as { agents?: RunningAgent[] }).agents ?? [])
          const running = list.filter((a) => ACTIVE_STATUSES.has(a.status))
          setSnapshot(running.length, running)
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
