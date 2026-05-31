import { Suspense, useEffect, type ReactNode } from 'react'
import Icon from '../Icon'
import { ErrorBoundary } from '../ErrorBoundary'

// Frame around the active game: back to grid, optional restart, optional
// status slot (score / timer). Wraps the game in an ErrorBoundary keyed by
// the caller so a crash in one game never takes down the page, and a Suspense
// boundary for the lazy()-loaded game component.
export default function GameShell({
  gameId,
  title,
  icon,
  onExit,
  onRestart,
  onHelp,
  status,
  children,
}: {
  gameId: string
  title: string
  icon?: string
  onExit: () => void
  onRestart?: () => void
  onHelp?: () => void
  status?: ReactNode
  children: ReactNode
}) {
  // Escape restarts the current game, but defers to any open overlay (a
  // how-to-play splash or a win/lose dialog) so it doesn't fire underneath one.
  useEffect(() => {
    if (!onRestart) return
    const handler = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (document.querySelector('[role="dialog"]')) return
      e.preventDefault()
      onRestart()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onRestart])

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <button
          type="button"
          data-testid="breakroom-back"
          onClick={onExit}
          className="flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          <Icon name="arrow_back" className="text-lg" /> Games
        </button>
        {icon && <Icon name={icon} className="text-xl accent-text" />}
        <span className="text-lg font-semibold text-slate-900 dark:text-white">{title}</span>
        <div className="ml-auto flex items-center gap-3">
          {status}
          {onHelp && (
            <button
              type="button"
              data-testid="breakroom-help"
              onClick={onHelp}
              aria-label="How to play"
              className="flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              <Icon name="help" className="text-lg" /> How to play
            </button>
          )}
          {onRestart && (
            <button
              type="button"
              data-testid="breakroom-restart"
              onClick={onRestart}
              className="flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              <Icon name="restart_alt" className="text-lg" /> Restart
            </button>
          )}
        </div>
      </div>
      <ErrorBoundary key={gameId}>
        <Suspense fallback={<div className="p-8 text-center text-slate-500">Loading...</div>}>
          {children}
        </Suspense>
      </ErrorBoundary>
    </div>
  )
}
