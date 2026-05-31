import { useEffect } from 'react'
import { reportError } from '../lib/reportError'

/**
 * Plays a short, pleasant two-note chime using the Web Audio API.
 *
 * No external audio file and no npm dependency: the tone is synthesised
 * live from an oscillator with a quick gain envelope so it fades in and
 * out smoothly (no click). Total length is ~260ms.
 *
 * Exported so the unit test can call it directly with a mocked
 * AudioContext, and so callers can trigger the sound without the hook.
 */
export function playTaskFinishedSound(
  AudioCtor: typeof AudioContext | undefined = typeof window !== 'undefined'
    ? window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    : undefined,
): void {
  if (!AudioCtor) return
  try {
    const ctx = new AudioCtor()

    // A rising perfect fifth: C6 -> G6. Cheerful, short, unobtrusive.
    const notes = [
      { freq: 1046.5, start: 0, dur: 0.16 },
      { freq: 1568.0, start: 0.1, dur: 0.16 },
    ]
    const peak = 0.18 // gentle volume

    for (const note of notes) {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = note.freq

      const t0 = ctx.currentTime + note.start
      const t1 = t0 + note.dur
      // Quick attack, smooth exponential release to avoid clicks.
      gain.gain.setValueAtTime(0.0001, t0)
      gain.gain.exponentialRampToValueAtTime(peak, t0 + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, t1)

      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start(t0)
      osc.stop(t1)
    }

    // Close the context shortly after the last note finishes so we do
    // not leak audio contexts on repeated task closes.
    const totalMs = (notes[notes.length - 1].start + notes[notes.length - 1].dur) * 1000 + 50
    window.setTimeout(() => {
      // Some implementations reject if already closed; swallow.
      ctx.close?.().catch(() => {})
    }, totalMs)
  } catch (e) {
    reportError('Task-finished sound failed', e)
  }
}

/**
 * Subscribes to the existing notifications WebSocket (the same
 * `/api/ws/notifications` channel that `useNotificationsFeed` uses) and
 * plays a short chime whenever the backend emits a `needle_closed`
 * event. The backend publishes this event from the task-close handler
 * (api/routers/tasks.py) onto the notifications event bus, which the WS
 * endpoint forwards verbatim as `{ "type": "needle_closed", ... }`.
 *
 * Mount once at app level. It opens its own lightweight read-only socket
 * and reconnects on drop, mirroring the notifications feed's behaviour.
 */
export function useTaskFinishedSound(): void {
  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let cancelled = false

    const connect = () => {
      if (cancelled) return
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const url = `${protocol}//${window.location.host}/api/ws/notifications`

      try {
        ws = new WebSocket(url)
      } catch (e) {
        reportError('Task-finished sound WS connect error', e)
        return
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg && msg.type === 'needle_closed') {
            playTaskFinishedSound()
          }
        } catch {
          // Ignore non-JSON / unrelated frames; the notifications feed
          // owns parse-error reporting for this channel.
        }
      }

      ws.onclose = () => {
        if (cancelled) return
        reconnectTimer = setTimeout(connect, 5000)
      }
    }

    connect()

    return () => {
      cancelled = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (ws) ws.close()
    }
  }, [])
}
