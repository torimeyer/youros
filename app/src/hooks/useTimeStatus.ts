import { useState, useEffect } from 'react'

const POLL_MS = 30_000

export interface TimeEstimate {
  estimate_sec: number
}

/**
 * Polls /api/time/estimate/{opKind} every 30 seconds.
 * Returns { estimate_sec } when the backend has enough history to produce
 * an estimate, or null when it does not.
 */
export function useTimeStatus(opKind: string): TimeEstimate | null {
  const [estimate, setEstimate] = useState<TimeEstimate | null>(null)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const fetchEstimate = async () => {
      try {
        const res = await fetch(`/api/time/estimate/${encodeURIComponent(opKind)}`)
        if (!res.ok) return
        const data: { estimate_sec: number | null } = await res.json()
        if (!cancelled) {
          setEstimate(
            typeof data.estimate_sec === 'number' && data.estimate_sec > 0
              ? { estimate_sec: data.estimate_sec }
              : null,
          )
        }
      } catch {
        // Network error — keep previous value, retry on next tick
      }
      if (!cancelled) {
        timer = setTimeout(fetchEstimate, POLL_MS)
      }
    }

    fetchEstimate()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [opKind])

  return estimate
}
