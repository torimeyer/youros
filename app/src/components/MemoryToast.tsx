import { useEffect } from 'react'
import { useMemoryToastStore } from '../stores/memoryToast'

/**
 * MemoryToast
 *
 * Shows a 3-second toast whenever a `memory_added` websocket event arrives.
 * The event is written into `useMemoryToastStore` by ChatPanel; this component
 * just renders and auto-dismisses.
 */
export default function MemoryToast() {
  const bullet = useMemoryToastStore((s) => s.bullet)
  const clear = useMemoryToastStore((s) => s.clear)

  // Auto-dismiss after 3 seconds.
  useEffect(() => {
    if (!bullet) return
    const timer = setTimeout(clear, 3000)
    return () => clearTimeout(timer)
  }, [bullet, clear])

  if (!bullet) return null

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="memory-toast"
      className="fixed bottom-6 right-6 z-50 flex items-start gap-3 rounded-xl bg-slate-800 border border-slate-700 px-4 py-3 shadow-xl max-w-sm animate-fade-in"
    >
      {/* Brain icon */}
      <span className="material-symbols-outlined text-violet-400 text-xl mt-0.5 shrink-0">
        memory
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium text-slate-100 leading-snug">
          Got it. I&apos;ll remember that.
        </p>
        <p
          className="text-xs text-slate-400 mt-0.5 truncate"
          title={bullet}
        >
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
