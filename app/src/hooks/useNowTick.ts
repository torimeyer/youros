// →2982 One shared 1-second wall clock for every component that shows a
// live elapsed readout. All subscribers ride ONE setInterval, and each
// tick lands as a single batched React update instead of N private timers
// each firing their own render every second.
import { useEffect, useState } from 'react'

type Listener = (now: number) => void

const listeners = new Set<Listener>()
let timer: ReturnType<typeof setInterval> | null = null

function subscribe(listener: Listener): () => void {
  listeners.add(listener)
  if (!timer) {
    timer = setInterval(() => {
      const now = Date.now()
      listeners.forEach((l) => l(now))
    }, 1000)
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0 && timer) {
      clearInterval(timer)
      timer = null
    }
  }
}

/**
 * Current time in milliseconds, refreshed once per second by a single
 * interval shared across every mounted subscriber. The interval starts
 * with the first subscriber and stops with the last one.
 */
export function useNowTick(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => subscribe(setNow), [])
  return now
}
