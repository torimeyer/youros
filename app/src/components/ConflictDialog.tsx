import Icon from './Icon'
import type { ConflictItem } from '../lib/spawn'

interface Props {
  open: boolean
  conflicts: ConflictItem[]
  onWait: () => void
  onProceed: () => void
}

/**
 * ConflictDialog — shown when a Build attempt hits a spawn lock conflict
 * (HTTP 409 from POST /specs/{path}/build). Lists each agent that is
 * already holding one of the files this build wants to touch, and offers
 * Wait (dismiss, come back later) or Build anyway (proceed with the spawn
 * regardless; the kernel will accept the second spawn and rely on each
 * agent's own startup conflict re-check to keep things safe).
 */
export default function ConflictDialog({ open, conflicts, onWait, onProceed }: Props) {
  if (!open) return null

  return (
    <div
      data-testid="conflict-dialog"
      className="fixed inset-0 z-[60] flex items-start justify-center bg-black/60 pt-24"
      onClick={onWait}
    >
      <div
        className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-2xl shadow-2xl w-full max-w-lg mx-4 flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          data-testid="conflict-dialog-header"
          className="flex items-center gap-2 px-5 py-4 border-b border-slate-200 dark:border-slate-800"
        >
          <Icon name="warning" className="text-amber-600 dark:text-amber-400 text-lg" />
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
            {conflicts.length} {conflicts.length === 1 ? 'agent is' : 'agents are'} editing files this build needs
          </h2>
        </div>

        {/* Conflict list */}
        <div className="px-5 py-4 max-h-72 overflow-y-auto space-y-2">
          {conflicts.map((c, i) => (
            <div
              key={`${c.held_by_spawn}-${c.held_path}-${i}`}
              data-testid="conflict-row"
              className="flex items-start gap-3 px-3 py-2 rounded-lg bg-slate-50/40 dark:bg-slate-800/40 border border-slate-200 dark:border-slate-800"
            >
              <Icon name="lock" className="text-amber-600 dark:text-amber-400/80 text-sm shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-slate-800 dark:text-slate-200">
                  <span className="font-medium">{c.held_by_spawn}</span>
                </p>
                <p className="text-xs text-slate-500 font-mono truncate">{c.held_path}</p>
              </div>
            </div>
          ))}
        </div>

        {/* Footer / actions */}
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-slate-200 dark:border-slate-800 bg-white/60 dark:bg-slate-900/60">
          <button
            data-testid="conflict-dialog-wait"
            onClick={onWait}
            className="text-sm px-4 py-2 rounded-lg text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          >
            Wait
          </button>
          <button
            data-testid="conflict-dialog-proceed"
            onClick={onProceed}
            className="text-sm px-4 py-2 rounded-lg bg-amber-600 text-white hover:bg-amber-500 transition-colors font-medium"
          >
            Build anyway
          </button>
        </div>
      </div>
    </div>
  )
}
