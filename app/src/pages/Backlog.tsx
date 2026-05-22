import { useState, useEffect, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { buildSpec } from '../lib/spawn'
import type { ConflictItem } from '../lib/spawn'
import ConflictDialog from '../components/ConflictDialog'
import { ClampedDescription } from '../components/ClampedDescription'

interface Spec {
  id: string
  path?: string
  title: string
  status: string
  effective_status?: string
  needs_clarity?: boolean
  task_ids: string[]
  description?: string
}

interface Task {
  id: string
  title: string
  status: string
  spec_id: string | null
  description?: string
  priority?: string
  tags?: string[]
}

function displayStatus(s: string): 'Draft' | 'Ready' | 'Building' | 'Done' {
  if (s === 'complete') return 'Done'
  if (s === 'in-progress') return 'Building'
  if (s === 'ready' || s === 'spec') return 'Ready'
  return 'Draft'
}

function specStatusClass(ds: 'Draft' | 'Ready' | 'Building' | 'Done'): string {
  if (ds === 'Ready') return 'bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30'
  if (ds === 'Building') return 'bg-amber-500/15 text-amber-300 ring-1 ring-amber-500/30'
  if (ds === 'Done') return 'bg-green-900/40 text-green-400'
  return 'bg-slate-700 text-slate-200'
}

function taskStatusClass(s: string): string {
  if (s === 'in_progress') return 'bg-amber-500/15 text-amber-300 ring-1 ring-amber-500/30'
  if (s === 'complete') return 'bg-green-900/40 text-green-400'
  return 'bg-slate-700 text-slate-200'
}

function taskDisplayStatus(s: string): string {
  if (s === 'open') return 'Ready'
  if (s === 'in_progress') return 'In progress'
  return s
}

function SpecCard({ spec, onBuild }: { spec: Spec; onBuild: (s: Spec) => void }) {
  const ds = displayStatus(spec.status)
  const specSlug = spec.path?.split('/').pop()?.replace('.md', '') ?? spec.id
  return (
    <div
      data-testid={`kanban-card-${spec.id}`}
      className="rounded-xl bg-slate-800/50 hover:-translate-y-px transition-transform min-w-0 overflow-hidden"
    >
      <Link
        to={`/specs?focus=${encodeURIComponent(spec.path ?? spec.id)}`}
        data-testid="spec-card-link"
        className="p-4 flex flex-col gap-2 cursor-pointer block"
      >
      <span className="font-mono text-xs text-slate-400" data-testid="spec-id-chip">
        {specSlug}
      </span>
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-medium text-slate-200 flex-1 line-clamp-2 break-words">{spec.title}</span>
        <span
          data-testid="card-type-chip"
          className="text-xs px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30 shrink-0"
        >
          Spec
        </span>
      </div>
      <div className="flex items-center gap-1.5 flex-wrap">
        <span
          data-testid="card-status-pill"
          className={`text-xs px-2 py-0.5 rounded-full ${specStatusClass(ds)}`}
        >
          {ds}
        </span>
        {spec.task_ids.map((tid) => (
          <span
            key={tid}
            data-testid={`card-task-chip-${tid}`}
            className="text-xs px-2 py-0.5 rounded-full bg-slate-700/60 text-slate-300"
          >
            {tid}
          </span>
        ))}
      </div>
      {spec.description && <ClampedDescription text={spec.description} lines={3} />}
      {ds === 'Ready' && (
        <button
          data-testid="card-build-button"
          onClick={(e) => { e.stopPropagation(); onBuild(spec) }}
          className="self-start text-sm bg-blue-600 hover:bg-blue-500 text-white font-medium px-4 py-2 rounded-md shadow-sm"
        >
          Build
        </button>
      )}
      </Link>
    </div>
  )
}

function TaskCard({ task }: { task: Task }) {
  return (
    <div
      data-testid={`kanban-card-${task.id}`}
      className="rounded-xl bg-slate-800/50 hover:-translate-y-px transition-transform min-w-0 overflow-hidden"
    >
      <Link
        to={`/tasks?focus=${task.id}`}
        data-testid="task-card-link"
        className="p-4 flex flex-col gap-2 cursor-pointer block"
      >
      <span className="font-mono text-xs text-slate-400" data-testid="task-id-chip">
        →{task.id}
      </span>
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-medium text-slate-200 flex-1 line-clamp-2 break-words">{task.title}</span>
        <span
          data-testid="card-type-chip"
          className="text-xs px-2 py-0.5 rounded-full bg-indigo-900/60 text-indigo-300 shrink-0"
        >
          Task
        </span>
      </div>
      <div className="flex items-center gap-1.5 flex-wrap">
        <span
          data-testid="card-status-pill"
          className={`text-xs px-2 py-0.5 rounded-full ${taskStatusClass(task.status)}`}
        >
          {taskDisplayStatus(task.status)}
        </span>
        {task.spec_id && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-slate-700/60 text-slate-300">
            → {task.spec_id}
          </span>
        )}
      </div>
      {task.description && <ClampedDescription text={task.description} lines={3} />}
      </Link>
    </div>
  )
}

function KanbanColumn({
  id,
  label,
  count,
  emptyTestId,
  isEmpty,
  children,
}: {
  id: string
  label: string
  count: number
  emptyTestId: string
  isEmpty: boolean
  children: ReactNode
}) {
  return (
    <div data-testid={`kanban-column-${id}`} className="flex-1 flex flex-col gap-3 min-w-0">
      <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider flex items-center">
        {label}
        <span
          data-testid={`column-count-${id}`}
          className={`ml-2 text-xs px-2 py-0.5 rounded-full ${
            count > 0
              ? 'bg-emerald-500/15 text-emerald-300'
              : 'bg-slate-700/60 text-slate-300'
          }`}
        >
          {count}
        </span>
      </h3>
      {isEmpty ? (
        <div
          data-testid={emptyTestId}
          className="rounded-xl bg-slate-800/20 p-4 text-center text-sm text-slate-500"
        >
          Nothing here
        </div>
      ) : (
        <div className="flex flex-col gap-2">{children}</div>
      )}
    </div>
  )
}

const KANBAN_SPECS_CACHE_KEY = 'myos.kanbanSpecsCache.v1'
const KANBAN_TASKS_CACHE_KEY = 'myos.kanbanTasksCache.v1'

function readCache<T>(key: string): T[] {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return []
    const raw = window.localStorage.getItem(key)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as T[]) : []
  } catch { return [] }
}

function writeCache<T>(key: string, data: T[]): void {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    window.localStorage.setItem(key, JSON.stringify(data))
  } catch { /* quota errors are not fatal */ }
}

function AllView() {
  const [specs, setSpecs] = useState<Spec[]>(() => readCache<Spec>(KANBAN_SPECS_CACHE_KEY))
  const [tasks, setTasks] = useState<Task[]>(() => readCache<Task>(KANBAN_TASKS_CACHE_KEY))
  const [conflicts, setConflicts] = useState<ConflictItem[]>([])
  const [search, setSearch] = useState('')

  useEffect(() => {
    api.get<{ docs: Spec[] }>('/specs').then((r) => {
      const docs = r.docs ?? []
      setSpecs(docs)
      writeCache(KANBAN_SPECS_CACHE_KEY, docs)
    }).catch(() => {})
    api.get<{ tasks: Task[] }>('/tasks').then((r) => {
      const tasks = r.tasks ?? []
      setTasks(tasks)
      writeCache(KANBAN_TASKS_CACHE_KEY, tasks)
    }).catch(() => {})
  }, [])

  async function handleBuild(spec: Spec) {
    if (!spec.path) return
    const result = await buildSpec(spec.path)
    if (result?.status === 'conflict') setConflicts(result.conflicts)
  }

  const q = search.toLowerCase()
  const matches = (title: string) => !q || title.toLowerCase().includes(q)

  const effectiveStatus = (s: Spec) => s.effective_status ?? s.status
  const draftingSpecs = specs.filter((s) => effectiveStatus(s) === 'draft' && matches(s.title))
  const readySpecs = specs.filter((s) => (effectiveStatus(s) === 'ready' || effectiveStatus(s) === 'spec') && matches(s.title))
  const openTasks = tasks.filter((t) => t.status === 'open' && matches(t.title))
  const inProgressTasks = tasks.filter((t) => t.status === 'in_progress' && matches(t.title))

  return (
    <div data-testid="backlog-allview" className="flex flex-col gap-4">
      <div className="flex justify-end">
        <input
          data-testid="filter-search-input"
          type="text"
          placeholder="Search..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="text-xs bg-slate-800 text-slate-300 placeholder-slate-500 rounded-lg px-3 py-1 border border-slate-700 focus:outline-none focus:border-slate-500"
        />
      </div>

      <div className="flex gap-4">
        <KanbanColumn
          id="drafts"
          label="Drafts"
          count={draftingSpecs.length}
          emptyTestId="empty-state-drafts"
          isEmpty={draftingSpecs.length === 0}
        >
          {draftingSpecs.map((s) => (
            <SpecCard key={s.id} spec={s} onBuild={handleBuild} />
          ))}
        </KanbanColumn>

        <KanbanColumn
          id="ready-specs"
          label="Ready specs"
          count={readySpecs.length}
          emptyTestId="empty-state-ready-specs"
          isEmpty={readySpecs.length === 0}
        >
          {readySpecs.map((s) => (
            <SpecCard key={s.id} spec={s} onBuild={handleBuild} />
          ))}
        </KanbanColumn>

        <KanbanColumn
          id="ready-tasks"
          label="Ready tasks"
          count={openTasks.length}
          emptyTestId="empty-state-ready-tasks"
          isEmpty={openTasks.length === 0}
        >
          {openTasks.map((t) => (
            <TaskCard key={t.id} task={t} />
          ))}
        </KanbanColumn>

        <KanbanColumn
          id="in-progress"
          label="In progress"
          count={inProgressTasks.length}
          emptyTestId="empty-state-in-progress"
          isEmpty={inProgressTasks.length === 0}
        >
          {inProgressTasks.map((t) => (
            <TaskCard key={t.id} task={t} />
          ))}
        </KanbanColumn>
      </div>

      <ConflictDialog
        open={conflicts.length > 0}
        conflicts={conflicts}
        onWait={() => setConflicts([])}
        onProceed={() => setConflicts([])}
      />
    </div>
  )
}

export default function Backlog() {
  return (
    <div className="flex flex-col h-full p-6 gap-6">
      <AllView />
    </div>
  )
}
