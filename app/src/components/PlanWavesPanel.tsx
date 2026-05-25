import { useEffect, useState, useCallback } from 'react'
import Icon from './Icon'
import ConflictDialog from './ConflictDialog'
import { api } from '../lib/api'
import { buildSpec, type ConflictItem } from '../lib/spawn'

interface WaveNeedle {
  id: string
  title: string
  priority: string
  scope_hint: string
  type?: 'needle' | 'spec'
}

interface Wave {
  wave: number
  needles: WaveNeedle[]
  blocked_by_prior: boolean
}

interface WavesResponse {
  waves: Wave[]
  total_needles: number
}

interface Spec {
  path: string
  filename: string
  title: string
  status: string
  promoted_at?: string
  created_at?: string
}

interface SpecsResponse {
  docs: Spec[]
}

interface Task {
  id: string
  title: string
  status: string
  priority?: string
  scope_hint?: string
  assigned_agent?: string | null
}

interface TasksResponse {
  tasks: Task[]
}

interface Props {
  open: boolean
  onClose: () => void
}

type Subtab = 'specs' | 'tasks' | 'waves'

const PRIORITY_COLORS: Record<string, string> = {
  P0: 'bg-red-500/20 text-red-300 border border-red-500/30',
  P1: 'bg-orange-500/20 text-orange-300 border border-orange-500/30',
  P2: 'bg-blue-500/20 text-blue-300 border border-blue-500/30',
  P3: 'bg-slate-500/20 text-slate-400 border border-slate-600',
}

// Map backend spec status to user-facing label. Mirrors displayStatus in
// Specs.tsx but defined locally so PlanWavesPanel doesn't depend on a page
// module. Kept short: only the four states the UI cares about.
function displayStatus(s: string): 'Draft' | 'Ready' | 'Building' | 'Done' {
  if (s === 'complete') return 'Done'
  if (s === 'in-progress') return 'Building'
  if (s === 'ready' || s === 'spec') return 'Ready'
  return 'Draft'
}

const STATUS_BADGE: Record<string, string> = {
  Draft: 'bg-slate-500/20 text-slate-400',
  Ready: 'bg-blue-500/20 text-blue-400',
  Building: 'bg-yellow-500/20 text-yellow-400',
  Done: 'bg-green-500/20 text-green-400',
}

export default function PlanWavesPanel({ open, onClose }: Props) {
  const [subtab, setSubtab] = useState<Subtab>('waves')

  // Waves state.
  const [wavesLoading, setWavesLoading] = useState(false)
  const [wavesData, setWavesData] = useState<WavesResponse | null>(null)
  const [wavesError, setWavesError] = useState<string | null>(null)

  // Specs state.
  const [specs, setSpecs] = useState<Spec[] | null>(null)
  const [specsLoading, setSpecsLoading] = useState(false)
  const [specsError, setSpecsError] = useState<string | null>(null)
  const [buildingPath, setBuildingPath] = useState<string | null>(null)
  const [conflicts, setConflicts] = useState<ConflictItem[] | null>(null)
  const [pendingConflictPath, setPendingConflictPath] = useState<string | null>(null)

  // Tasks state.
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [tasksLoading, setTasksLoading] = useState(false)
  const [tasksError, setTasksError] = useState<string | null>(null)

  // Fetch waves on open (default subtab). Always includes specs since the
  // standalone "Tasks and design specs" toggle has been replaced by the
  // parent subtabs.
  useEffect(() => {
    if (!open) return
    setWavesLoading(true)
    setWavesError(null)
    setWavesData(null)
    api.get<WavesResponse>('/tasks/waves?include_specs=true')
      .then((r) => setWavesData({ waves: r?.waves ?? [], total_needles: r?.total_needles ?? 0 }))
      .catch((e: unknown) => setWavesError(e instanceof Error ? e.message : 'Could not load wave plan'))
      .finally(() => setWavesLoading(false))
  }, [open])

  // Lazy-load specs the first time the Specs subtab is shown.
  useEffect(() => {
    if (!open || subtab !== 'specs' || specs !== null) return
    setSpecsLoading(true)
    setSpecsError(null)
    api.get<SpecsResponse>('/specs')
      .then((r) => setSpecs(r.docs || []))
      .catch((e: unknown) => setSpecsError(e instanceof Error ? e.message : 'Could not load specs'))
      .finally(() => setSpecsLoading(false))
  }, [open, subtab, specs])

  // Lazy-load tasks the first time the Tasks subtab is shown.
  useEffect(() => {
    if (!open || subtab !== 'tasks' || tasks !== null) return
    setTasksLoading(true)
    setTasksError(null)
    api.get<TasksResponse>('/tasks')
      .then((r) => setTasks(r.tasks || []))
      .catch((e: unknown) => setTasksError(e instanceof Error ? e.message : 'Could not load tasks'))
      .finally(() => setTasksLoading(false))
  }, [open, subtab, tasks])

  const handleBuild = useCallback(async (path: string) => {
    setBuildingPath(path)
    try {
      const result = await buildSpec(path)
      if (result.status === 'conflict') {
        setConflicts(result.conflicts)
        setPendingConflictPath(path)
      } else {
        // Refresh the specs list so the just-built spec flips to Building.
        api.get<SpecsResponse>('/specs').then((r) => setSpecs(r.docs || [])).catch(() => {})
      }
    } finally {
      setBuildingPath(null)
    }
  }, [])

  const handleConflictWait = useCallback(() => {
    setConflicts(null)
    setPendingConflictPath(null)
  }, [])

  // Build-anyway: dismiss the dialog and re-spawn. The kernel's per-agent
  // startup conflict re-check is the safety net for the race.
  const handleConflictProceed = useCallback(async () => {
    const path = pendingConflictPath
    setConflicts(null)
    setPendingConflictPath(null)
    if (!path) return
    setBuildingPath(path)
    try {
      await buildSpec(path)
      api.get<SpecsResponse>('/specs').then((r) => setSpecs(r.docs || [])).catch(() => {})
    } finally {
      setBuildingPath(null)
    }
  }, [pendingConflictPath])

  if (!open) return null

  const totalWaveItems = wavesData ? wavesData.waves.reduce((sum, w) => sum + w.needles.length, 0) : 0

  // Build a quick lookup so Tasks rows can show "from spec: X" when a task
  // came from a spec build. Heuristic: spec.task_ids existed in the
  // /specs payload originally, but we don't fetch it here; instead we use
  // scope_hint prefix "spec:" or title containing "→<spec-slug>" as the
  // signal. Keep this lightweight; the test that matters is "rows render".
  const tasksWithSpecBadge = (t: Task): string | null => {
    if (t.scope_hint?.startsWith('spec:')) {
      return t.scope_hint.replace(/^spec:/, '').trim()
    }
    return null
  }

  const tabBtnClass = (active: boolean) =>
    `text-xs px-3 py-1.5 rounded-lg transition-colors font-medium ${
      active ? 'bg-purple-600 text-white' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
    }`

  return (
    <div
      data-testid="plan-waves-panel"
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-16"
      onClick={onClose}
    >
      <div
        className="bg-slate-900 border border-slate-700 rounded-2xl shadow-2xl w-full max-w-2xl mx-4 max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <Icon name="account_tree" className="text-purple-400 text-lg" />
            <h2 className="text-base font-semibold text-slate-100">Plan</h2>
            {subtab === 'waves' && wavesData && (
              <span className="text-xs text-slate-500 ml-1">
                {totalWaveItems} {totalWaveItems === 1 ? 'item' : 'items'} across {wavesData.waves.length} {wavesData.waves.length === 1 ? 'wave' : 'waves'}
              </span>
            )}
          </div>
          <button
            data-testid="plan-waves-close"
            onClick={onClose}
            className="text-slate-500 hover:text-slate-300 transition-colors"
          >
            <Icon name="close" className="text-base" />
          </button>
        </div>

        {/* Subtabs */}
        <div className="flex items-center gap-1 px-5 py-3 border-b border-slate-800/60 bg-slate-900/60">
          <button
            data-testid="plan-waves-subtab-specs"
            onClick={() => setSubtab('specs')}
            className={tabBtnClass(subtab === 'specs')}
          >
            Specs
          </button>
          <button
            data-testid="plan-waves-subtab-tasks"
            onClick={() => setSubtab('tasks')}
            className={tabBtnClass(subtab === 'tasks')}
          >
            Needles
          </button>
          <button
            data-testid="plan-waves-subtab-waves"
            onClick={() => setSubtab('waves')}
            className={tabBtnClass(subtab === 'waves')}
          >
            Waves
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 px-5 py-4 space-y-3">
          {/* SPECS SUBTAB */}
          {subtab === 'specs' && (
            <>
              {specsLoading && (
                <div className="text-sm text-slate-400 py-8 text-center">Loading specs...</div>
              )}
              {specsError && (
                <div className="text-sm text-red-400 bg-red-500/10 rounded-lg px-4 py-3">{specsError}</div>
              )}
              {specs && specs.length === 0 && (
                <div className="text-sm text-slate-400 py-8 text-center">No specs yet.</div>
              )}
              {specs && specs.map((s) => {
                const label = displayStatus(s.status)
                const isReady = label === 'Ready'
                const isBuildingNow = buildingPath === s.path
                return (
                  <div
                    key={s.path}
                    data-testid={`spec-row-${s.path}`}
                    className="rounded-xl border border-slate-800 bg-slate-800/40 px-4 py-3 flex items-start gap-3"
                  >
                    <span className="shrink-0 text-base">📋</span>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-slate-200 leading-snug">{s.title}</p>
                      <p className="text-xs text-slate-500 mt-0.5 font-mono truncate">{s.path}</p>
                    </div>
                    <span className={`shrink-0 text-xs px-2 py-0.5 rounded ${STATUS_BADGE[label]}`}>
                      {label}
                    </span>
                    {isReady && (
                      <button
                        data-testid={`spec-build-button-${s.path}`}
                        onClick={() => handleBuild(s.path)}
                        disabled={isBuildingNow}
                        className="shrink-0 text-xs px-3 py-1.5 rounded-lg bg-blue-600 text-white hover:bg-blue-500 transition-colors font-medium disabled:opacity-50"
                      >
                        {isBuildingNow ? 'Starting...' : 'Build it'}
                      </button>
                    )}
                  </div>
                )
              })}
            </>
          )}

          {/* TASKS SUBTAB */}
          {subtab === 'tasks' && (
            <>
              {tasksLoading && (
                <div className="text-sm text-slate-400 py-8 text-center">Loading needles...</div>
              )}
              {tasksError && (
                <div className="text-sm text-red-400 bg-red-500/10 rounded-lg px-4 py-3">{tasksError}</div>
              )}
              {tasks && tasks.length === 0 && (
                <div className="text-sm text-slate-400 py-8 text-center">No open needles.</div>
              )}
              {tasks && tasks.map((t) => {
                const specBadge = tasksWithSpecBadge(t)
                return (
                  <div
                    key={t.id}
                    data-testid={`task-row-${t.id}`}
                    className="rounded-xl border border-slate-800 bg-slate-800/40 px-4 py-3 flex items-start gap-3"
                  >
                    {t.priority && (
                      <span className={`shrink-0 text-xs px-1.5 py-0.5 rounded font-mono ${PRIORITY_COLORS[t.priority] ?? PRIORITY_COLORS.P3}`}>
                        {t.priority}
                      </span>
                    )}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-slate-200 leading-snug line-clamp-2">{(t.title ?? '').split('⊕')[0].trim()}</p>
                      {specBadge && (
                        <p className="text-xs text-purple-400/80 mt-0.5">from spec: {specBadge}</p>
                      )}
                    </div>
                  </div>
                )
              })}
            </>
          )}

          {/* WAVES SUBTAB */}
          {subtab === 'waves' && (
            <>
              {wavesLoading && (
                <div data-testid="plan-waves-loading" className="text-sm text-slate-400 py-8 text-center">
                  Working out the waves...
                </div>
              )}
              {wavesError && (
                <div data-testid="plan-waves-error" className="text-sm text-red-400 bg-red-500/10 rounded-lg px-4 py-3">
                  {wavesError}
                </div>
              )}
              {wavesData && wavesData.waves.length === 0 && (
                <div className="text-sm text-slate-400 py-8 text-center">No open needles to plan.</div>
              )}
              {wavesData && wavesData.waves.map((wave) => (
                <div
                  key={wave.wave}
                  data-testid={`wave-${wave.wave}`}
                  className="rounded-xl border border-slate-800 bg-slate-800/40"
                >
                  <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-800">
                    <span className="flex items-center justify-center w-6 h-6 rounded-full bg-purple-500/20 text-purple-300 text-xs font-bold">
                      {wave.wave}
                    </span>
                    <span className="text-sm font-medium text-slate-200">
                      Wave {wave.wave}
                    </span>
                    <span className="text-xs text-slate-500">
                      {wave.needles.length} {wave.needles.length === 1 ? 'item' : 'items'}
                    </span>
                    {wave.blocked_by_prior && (
                      <span
                        data-testid={`wave-${wave.wave}-blocked`}
                        className="ml-auto text-xs text-amber-400/80 flex items-center gap-1"
                      >
                        <Icon name="lock" className="text-xs" />
                        Starts after wave {wave.wave - 1} finishes
                      </span>
                    )}
                  </div>
                  <div className="divide-y divide-slate-800/60">
                    {wave.needles.map((needle) => (
                      <div
                        key={needle.id}
                        data-testid={`wave-needle-${needle.id}`}
                        className="flex items-start gap-3 px-4 py-3"
                      >
                        {needle.type === 'spec' ? (
                          <span className="shrink-0 text-base" title="Design spec">📋</span>
                        ) : (
                          <span className={`shrink-0 text-xs px-1.5 py-0.5 rounded font-mono ${PRIORITY_COLORS[needle.priority] ?? PRIORITY_COLORS.P3}`}>
                            {needle.priority}
                          </span>
                        )}
                        <div className="flex-1 min-w-0">
                          <p className="text-sm text-slate-200 leading-snug line-clamp-2">{needle.title.split('⊕')[0].trim()}</p>
                          {needle.scope_hint && needle.scope_hint !== 'general' && (
                            <p className="text-xs text-slate-500 mt-0.5 line-clamp-2">{needle.scope_hint.replace(/⊕/g, ' ').trim()}</p>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>

        {/* Footer hint — only in waves subtab */}
        {subtab === 'waves' && wavesData && wavesData.waves.length > 0 && (
          <div className="px-5 py-3 border-t border-slate-800 text-xs text-slate-600">
            Items in the same wave can run at the same time. Later waves wait for the previous one to finish.
          </div>
        )}
      </div>

      {/* Conflict dialog overlays the panel when a Build hits a 409. */}
      <ConflictDialog
        open={conflicts !== null}
        conflicts={conflicts ?? []}
        onWait={handleConflictWait}
        onProceed={handleConflictProceed}
      />
    </div>
  )
}
