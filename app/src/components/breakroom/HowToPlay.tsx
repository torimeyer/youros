import { useEffect, useRef, useState } from 'react'

// A friendly "how to play" splash shown over a game before it starts.
// Renders as a [role="dialog"] so GameShell's ESC-to-restart and the game
// loops' isOverlayOpen() pause both treat it as an open overlay.
export default function HowToPlay({
  title,
  steps,
  onPlay,
  onNeverShow,
}: {
  title: string
  steps: string[]
  /** Start (or resume) the game. */
  onPlay: () => void
  /** Called when the user ticks "Don't show this again" and presses Play. */
  onNeverShow: () => void
}) {
  const [never, setNever] = useState(false)
  const playRef = useRef<HTMLButtonElement>(null)
  const neverRef = useRef(never)
  neverRef.current = never

  useEffect(() => {
    setTimeout(() => playRef.current?.focus(), 0)
  }, [])

  // Enter or Escape both dismiss the splash and start the game.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        if (neverRef.current) onNeverShow()
        onPlay()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onPlay, onNeverShow])

  const play = () => {
    if (never) onNeverShow()
    onPlay()
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="howto-title"
      data-testid="howto-backdrop"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-6 shadow-2xl dark:border-slate-700 dark:bg-slate-900">
        <h2 id="howto-title" className="mb-1 text-lg font-semibold text-slate-900 dark:text-white">
          How to play {title}
        </h2>
        <ol className="my-4 flex flex-col gap-2 text-sm text-slate-700 dark:text-slate-300">
          {steps.slice(0, 5).map((step, i) => (
            <li key={i} className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-pink-500 text-xs font-bold text-white">
                {i + 1}
              </span>
              <span>{step}</span>
            </li>
          ))}
        </ol>
        <div className="flex items-center justify-between gap-2">
          <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
            <input
              type="checkbox"
              data-testid="howto-never"
              checked={never}
              onChange={(e) => setNever(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300"
            />
            Don't show this again
          </label>
          <button
            ref={playRef}
            type="button"
            data-testid="howto-play"
            onClick={play}
            className="rounded-lg bg-pink-500 px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-pink-600"
          >
            Play
          </button>
        </div>
      </div>
    </div>
  )
}
