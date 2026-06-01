import { useEffect } from 'react'
import { reportError } from '../lib/reportError'
import { subscribeSharedSocket } from '../lib/sharedSocket'

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
 * Listens to the existing notifications channel (the same
 * `/api/ws/notifications` channel that `useNotificationsFeed` uses) and
 * plays a short chime whenever the backend emits a `needle_closed`
 * event. The backend publishes this event from the task-close handler
 * (api/routers/tasks.py) onto the notifications event bus, which the WS
 * endpoint forwards verbatim as `{ "type": "needle_closed", ... }`.
 *
 * Mount once at app level. It shares the single notifications socket via
 * the shared-socket manager rather than opening its own, so the
 * notifications channel never holds more than one connection.
 */
export function useTaskFinishedSound(): void {
  useEffect(() => {
    const unsubscribe = subscribeSharedSocket('/api/ws/notifications', {
      onMessage: (msg) => {
        const m = msg as { type?: string }
        if (m && m.type === 'needle_closed') {
          playTaskFinishedSound()
        }
      },
    })

    return () => {
      unsubscribe()
    }
  }, [])
}
