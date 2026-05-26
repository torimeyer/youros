import { useEffect, useCallback } from 'react'
import { useMemoryToastStore } from '../stores/memoryToast'

async function callUndoRemove() {
  await fetch('/api/memory/user/undo-remove', { method: 'POST' })
}

function RememberedToast({ bullet, onDismiss }: { bullet: string; onDismiss: () => void }) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 3000)
    return () => clearTimeout(timer)
  }, [bullet, onDismiss])

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="memory-toast"
      className="fixed bottom-6 right-6 z-50 flex items-start gap-3 rounded-xl bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 px-4 py-3 shadow-xl max-w-sm animate-fade-in"
    >
      <span className="material-symbols-outlined text-violet-600 dark:text-violet-400 text-xl mt-0.5 shrink-0">
        memory
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium text-slate-900 dark:text-slate-100 leading-snug">
          Got it. I&apos;ll remember that.
        </p>
        <p className="text-xs text-slate-600 dark:text-slate-400 mt-0.5 truncate" title={bullet}>
          {bullet}
        </p>
      </div>
      <button
        onClick={onDismiss}
        aria-label="Dismiss"
        className="ml-1 shrink-0 text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors"
      >
        <span className="material-symbols-outlined text-base">close</span>
      </button>
    </div>
  )
}

function ForgotToast({ forgot, onDismiss }: { forgot: string; onDismiss: () => void }) {
  const handleUndo = useCallback(async () => {
    await callUndoRemove()
    onDismiss()
  }, [onDismiss])

  useEffect(() => {
    const timer = setTimeout(onDismiss, 3000)
    return () => clearTimeout(timer)
  }, [forgot, onDismiss])

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="memory-forgot-toast"
      className="fixed bottom-6 right-6 z-50 flex items-start gap-3 rounded-xl bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 px-4 py-3 shadow-xl max-w-sm animate-fade-in"
    >
      <span className="material-symbols-outlined text-amber-600 dark:text-amber-400 text-xl mt-0.5 shrink-0">
        ink_eraser
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-slate-900 dark:text-slate-100 leading-snug">Forgot.</p>
        <p className="text-xs text-slate-600 dark:text-slate-400 mt-0.5 truncate" title={forgot}>
          {forgot}
        </p>
      </div>
      <button
        onClick={handleUndo}
        data-testid="memory-undo-button"
        aria-label="Undo forget"
        className="shrink-0 text-xs font-medium text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:hover:text-violet-300 transition-colors px-2 py-1 rounded border border-violet-700 hover:border-violet-500"
      >
        Undo
      </button>
      <button
        onClick={onDismiss}
        aria-label="Dismiss"
        className="ml-1 shrink-0 text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors"
      >
        <span className="material-symbols-outlined text-base">close</span>
      </button>
    </div>
  )
}

function AmbiguousToast({
  candidates,
  onDismiss,
}: {
  candidates: string[]
  onDismiss: () => void
}) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 8000)
    return () => clearTimeout(timer)
  }, [candidates, onDismiss])

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="memory-ambiguous-toast"
      className="fixed bottom-6 right-6 z-50 flex flex-col gap-2 rounded-xl bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 px-4 py-3 shadow-xl max-w-sm animate-fade-in"
    >
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-slate-900 dark:text-slate-100">Which one to forget?</p>
        <button
          onClick={onDismiss}
          aria-label="Dismiss"
          className="text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors"
        >
          <span className="material-symbols-outlined text-base">close</span>
        </button>
      </div>
      <ul className="flex flex-col gap-1">
        {candidates.map((c) => (
          <li key={c}>
            <button
              className="w-full text-left text-xs text-slate-700 dark:text-slate-300 hover:text-white px-2 py-1 rounded hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors"
              onClick={onDismiss}
            >
              {c}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function MemoryToast() {
  const bullet = useMemoryToastStore((s) => s.bullet)
  const forgot = useMemoryToastStore((s) => s.forgot)
  const forgotCandidates = useMemoryToastStore((s) => s.forgotCandidates)
  const clear = useMemoryToastStore((s) => s.clear)
  const clearForgot = useMemoryToastStore((s) => s.clearForgot)

  if (forgotCandidates && forgotCandidates.length > 0) {
    return <AmbiguousToast candidates={forgotCandidates} onDismiss={clearForgot} />
  }
  if (forgot) {
    return <ForgotToast forgot={forgot} onDismiss={clearForgot} />
  }
  if (bullet) {
    return <RememberedToast bullet={bullet} onDismiss={clear} />
  }
  return null
}
