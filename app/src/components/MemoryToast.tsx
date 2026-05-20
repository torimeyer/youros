import { useEffect } from 'react'
import { useMemoryToastStore } from '../stores/memoryToast'

const BASE_CLASS =
  'fixed bottom-6 right-6 z-50 flex items-start gap-3 rounded-xl bg-slate-800 border border-slate-700 px-4 py-3 shadow-xl max-w-sm animate-fade-in'

export default function MemoryToast() {
  const mode = useMemoryToastStore((s) => s.mode)
  const bullet = useMemoryToastStore((s) => s.bullet)
  const section = useMemoryToastStore((s) => s.section)
  const candidates = useMemoryToastStore((s) => s.candidates)
  const clear = useMemoryToastStore((s) => s.clear)

  useEffect(() => {
    if (!mode) return
    const timer = setTimeout(clear, 3000)
    return () => clearTimeout(timer)
  }, [mode, clear])

  if (!mode) return null

  async function handleUndo() {
    if (!bullet) return
    try {
      await fetch('/api/memory/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ section, bullet }),
      })
    } catch {
      // best-effort
    }
    clear()
  }

  if (mode === 'added') {
    return (
      <div
        role="status"
        aria-live="polite"
        data-testid="memory-toast"
        className={BASE_CLASS}
      >
        <span className="material-symbols-outlined text-violet-400 text-xl mt-0.5 shrink-0">
          memory
        </span>
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-100 leading-snug">
            Got it. I&apos;ll remember that.
          </p>
          <p className="text-xs text-slate-400 mt-0.5 truncate" title={bullet!}>
            {bullet}
          </p>
        </div>
        <button
          onClick={clear}
          aria-label="Dismiss"
          className="ml-1 shrink-0 text-slate-500 hover:text-slate-300 transition-colors"
        >
          <span className="material-symbols-outlined text-base">close</span>
        </button>
      </div>
    )
  }

  if (mode === 'forgot') {
    return (
      <div
        role="status"
        aria-live="polite"
        data-testid="memory-toast-forgot"
        className={BASE_CLASS}
      >
        <span className="material-symbols-outlined text-slate-400 text-xl mt-0.5 shrink-0">
          memory
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-slate-100 leading-snug">Forgot.</p>
          <p className="text-xs text-slate-400 mt-0.5 truncate" title={bullet!}>
            {bullet}
          </p>
        </div>
        <button
          onClick={handleUndo}
          aria-label="Undo"
          data-testid="memory-undo-button"
          className="shrink-0 text-xs text-violet-400 hover:text-violet-300 font-medium transition-colors"
        >
          Undo
        </button>
        <button
          onClick={clear}
          aria-label="Dismiss"
          className="ml-1 shrink-0 text-slate-500 hover:text-slate-300 transition-colors"
        >
          <span className="material-symbols-outlined text-base">close</span>
        </button>
      </div>
    )
  }

  if (mode === 'ambiguous') {
    return (
      <div
        role="status"
        aria-live="polite"
        data-testid="memory-toast-ambiguous"
        className={BASE_CLASS + ' flex-col'}
      >
        <div className="flex items-center justify-between w-full">
          <p className="text-sm font-medium text-slate-100">Which one should I forget?</p>
          <button
            onClick={clear}
            aria-label="Dismiss"
            className="shrink-0 text-slate-500 hover:text-slate-300 transition-colors"
          >
            <span className="material-symbols-outlined text-base">close</span>
          </button>
        </div>
        <ul className="mt-2 space-y-1 w-full">
          {candidates.map((c) => (
            <li key={c}>
              <button
                onClick={() => {
                  useMemoryToastStore.getState().triggerForgot(c, 'Preferences')
                  fetch('/api/memory/remove', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: c }),
                  }).catch(() => {})
                }}
                className="w-full text-left text-xs text-slate-300 hover:text-white hover:bg-slate-700 rounded px-2 py-1 transition-colors truncate"
                title={c}
              >
                {c}
              </button>
            </li>
          ))}
        </ul>
      </div>
    )
  }

  // mode === 'failed'
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="memory-toast-failed"
      className={BASE_CLASS}
    >
      <span className="material-symbols-outlined text-slate-500 text-xl mt-0.5 shrink-0">
        memory
      </span>
      <p className="text-sm text-slate-400 flex-1">
        I don&apos;t have a preference about that.
      </p>
      <button
        onClick={clear}
        aria-label="Dismiss"
        className="ml-1 shrink-0 text-slate-500 hover:text-slate-300 transition-colors"
      >
        <span className="material-symbols-outlined text-base">close</span>
      </button>
    </div>
  )
}
