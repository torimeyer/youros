import { useState, useEffect, useCallback } from 'react'
import { api } from '../lib/api'

export interface ParkedTask {
  id: string
  tab_id: string
  label: string
  kind: 'schedule' | 'monitor'
  wakeup_at: number | null
  created_at: number
}

interface ParkedTaskPillProps {
  tasks: ParkedTask[]
  onCancelled: (id: string) => void
}

function Countdown({ wakeupAt }: { wakeupAt: number | null }) {
  const [secs, setSecs] = useState<number | null>(() =>
    wakeupAt ? Math.max(0, Math.ceil(wakeupAt - Date.now() / 1000)) : null
  )

  useEffect(() => {
    if (wakeupAt === null) return
    const id = setInterval(() => {
      const remaining = Math.max(0, Math.ceil(wakeupAt - Date.now() / 1000))
      setSecs(remaining)
    }, 1000)
    return () => clearInterval(id)
  }, [wakeupAt])

  if (secs === null) return <span className="text-slate-600 dark:text-slate-400">Monitoring&hellip;</span>

  const m = Math.floor(secs / 60)
  const s = secs % 60
  const label = m > 0 ? `${m}m ${s}s` : `${s}s`
  return <span className="text-slate-700 dark:text-slate-300 tabular-nums">{label}</span>
}

function SinglePill({
  task,
  onCancelled,
}: {
  task: ParkedTask
  onCancelled: (id: string) => void
}) {
  const handleCancel = useCallback(async () => {
    try {
      await api.delete(`/parked_tasks/${task.id}`)
    } catch {
      // optimistic: remove locally regardless
    }
    onCancelled(task.id)
  }, [task.id, onCancelled])

  return (
    <div
      data-testid={`parked-pill-${task.id}`}
      className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-xs"
    >
      <span className="text-slate-600 dark:text-slate-400 truncate max-w-[120px]" title={task.label}>
        {task.label}
      </span>
      <span className="text-slate-600">·</span>
      <Countdown wakeupAt={task.wakeup_at} />
      <button
        data-testid={`cancel-pill-${task.id}`}
        onClick={handleCancel}
        className="ml-0.5 text-slate-500 hover:text-red-600 dark:hover:text-red-400 transition-colors leading-none"
        title="Cancel"
        aria-label={`Cancel: ${task.label}`}
      >
        ×
      </button>
    </div>
  )
}

const MAX_VISIBLE = 3

export default function ParkedTaskPill({ tasks, onCancelled }: ParkedTaskPillProps) {
  const [popoverOpen, setPopoverOpen] = useState(false)

  if (tasks.length === 0) return null

  const visible = tasks.slice(0, MAX_VISIBLE)
  const overflow = tasks.length - MAX_VISIBLE

  return (
    <div
      data-testid="parked-pills-row"
      className="flex items-center gap-1.5 px-4 py-1.5 border-b border-slate-200/60 dark:border-slate-800/60 flex-wrap"
    >
      {visible.map(t => (
        <SinglePill key={t.id} task={t} onCancelled={onCancelled} />
      ))}
      {overflow > 0 && (
        <div className="relative">
          <button
            data-testid="parked-overflow-button"
            onClick={() => setPopoverOpen(o => !o)}
            className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-xs text-slate-600 dark:text-slate-400 hover:text-white transition-colors"
          >
            +{overflow} more
          </button>
          {popoverOpen && (
            <div
              data-testid="parked-overflow-popover"
              className="absolute top-full left-0 mt-1 z-50 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg shadow-xl p-2 flex flex-col gap-1.5 min-w-[200px]"
            >
              {tasks.slice(MAX_VISIBLE).map(t => (
                <SinglePill key={t.id} task={t} onCancelled={(id) => { onCancelled(id); setPopoverOpen(false) }} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
