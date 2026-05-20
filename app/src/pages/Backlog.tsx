import { useState, useEffect, type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { api } from '../lib/api'
import { buildSpec } from '../lib/spawn'
import type { ConflictItem } from '../lib/spawn'
import ConflictDialog from '../components/ConflictDialog'

import Specs from './Specs'
import Tasks from './Tasks'

interface Spec {
  id: string
  path: string
  title: string
  status: string
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

function SpecCard({ spec, onBuild }: { spec: Spec; onBuild: (s: Spec) => void }) {
  const ds = displayStatus(spec.status)
  const [expanded, setExpanded] = useState(false)
  return (
    <div
      data-testid={`kanban-card-${spec.id}`}
      className="rounded-xl bg-slate-800/50 p-4 flex flex-col gap-2 hover:-translate-y-px transition-transform cursor-pointer min-w-0 overflow-hidden"
    >
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
      {spec.description && (
        <div className="flex flex-col gap-1">
          <div className={`text-xs text-slate-400 whitespace-pre-wrap break-words ${expanded ? '' : 'line-clamp-3'}`}>
            {spec.description}
          </div>
          <button
            data-testid="card-expand"
            onClick={(e) => { e.stopPropagation(); setExpanded(!expanded) }}
            className="text-xs text-slate-500 hover:text-slate-300 self-start"
          >
            {expanded ? 'Show less' : 'Show more'}
          </button>
        </div>
      )}
      {ds === 'Ready' && (
        <button
          data-testid="card-build-button"
          onClick={() => onBuild(spec)}
          className="self-start text-sm bg-blue-600 hover:bg-blue-500 text-white font-medium px-4 py-2 rounded-md shadow-sm"
        >
          Build
        </button>
      )}
    </div>
  )
}

function TaskCard({ task }: { task: Task }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div
      data-testid={`kanban-card-${task.id}`}
      className="rounded-xl bg-slate-800/50 p-4 flex flex-col gap-2 hover:-translate-y-px transition-transform cursor-pointer min-w-0 overflow-hidden"
    >
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
          {task.status}
        </span>
        {task.spec_id && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-slate-700/60 text-slate-300">
            → {task.spec_id}
          </span>
        )}
      </div>
      {task.description && (
        <div className="flex flex-col gap-1">
          <div className={`text-xs text-slate-400 whitespace-pre-wrap break-words ${expanded ? '' : 'line-clamp-3'}`}>
            {task.description}
          </div>
          <button
            data-testid="card-expand"
            onClick={(e) => { e.stopPropagation(); setExpanded(!expanded) }}
            className="text-xs text-slate-500 hover:text-slate-300 self-start"
          >
            {expanded ? 'Show less' : 'Show more'}
          </button>
        </div>
      )}
    </div>
  )
}

function KanbanColumn({
  id,
  label,
  emptyTestId,
  isEmpty,
  children,
}: {
  id: string
  label: string
  emptyTestId: string
  isEmpty: boolean
  children: ReactNode
}) {
  return (
    <div data-testid={`kanban-column-${id}`} className="flex-1 flex flex-col gap-3 min-w-0">
      <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">{label}</h3>
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

function AllView() {
  const [specs, setSpecs] = useState<Spec[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [conflicts, setConflicts] = useState<ConflictItem[]>([])
  const [search, setSearch] = useState('')

  useEffect(() => {
    api.get<{ docs: Spec[] }>('/specs').then((r) => setSpecs(r.docs ?? [])).catch(() => {})
    api.get<{ tasks: Task[] }>('/tasks').then((r) => setTasks(r.tasks ?? [])).catch(() => {})
  }, [])

  async function handleBuild(spec: Spec) {
    const result = await buildSpec(spec.path)
    if (result?.status === 'conflict') setConflicts(result.conflicts)
  }

  const q = search.toLowerCase()
  const matches = (title: string) => !q || title.toLowerCase().includes(q)

  const draftingSpecs = specs.filter((s) => s.status === 'draft' && matches(s.title))
  const readySpecs = specs.filter((s) => (s.status === 'ready' || s.status === 'spec') && matches(s.title))
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
          id="drafting"
          label="Drafting"
          emptyTestId="empty-state-drafting"
          isEmpty={draftingSpecs.length + openTasks.length === 0}
        >
          {draftingSpecs.map((s) => (
            <SpecCard key={s.id} spec={s} onBuild={handleBuild} />
          ))}
          {openTasks.map((t) => (
            <TaskCard key={t.id} task={t} />
          ))}
        </KanbanColumn>

        <KanbanColumn
          id="ready"
          label="Ready"
          emptyTestId="empty-state-ready"
          isEmpty={readySpecs.length === 0}
        >
          {readySpecs.map((s) => (
            <SpecCard key={s.id} spec={s} onBuild={handleBuild} />
          ))}
        </KanbanColumn>

        <KanbanColumn
          id="in-progress"
          label="In progress"
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
  const location = useLocation()
  const path = location.pathname

  const tabClass = (active: boolean) =>
    `px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
      active ? 'bg-slate-700 text-white' : 'text-slate-400 hover:text-slate-200'
    }`

  const isAll = path === '/backlog'
  const isSpecs = path === '/backlog/specs'
  const isTasksTab = path === '/backlog/tasks'
  return (
    <div className="flex flex-col h-full p-6 gap-6">
      <nav className="flex gap-1">
        <NavLink to="/backlog" end className={() => tabClass(isAll)}>
          All
        </NavLink>
        <NavLink to="/backlog/specs" className={() => tabClass(isSpecs)}>
          Specs
        </NavLink>
        <NavLink to="/backlog/tasks" className={() => tabClass(isTasksTab)}>
          Tasks
        </NavLink>
      </nav>

      {isAll && <AllView />}
      {isSpecs && <Specs embedded />}
      {isTasksTab && <Tasks embedded />}

    </div>
  )
}
