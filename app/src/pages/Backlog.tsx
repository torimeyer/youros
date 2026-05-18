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
}

interface Task {
  id: string
  title: string
  status: string
  spec_id: string | null
}

function displayStatus(s: string): 'Draft' | 'Ready' | 'Building' | 'Done' {
  if (s === 'complete') return 'Done'
  if (s === 'in-progress') return 'Building'
  if (s === 'ready' || s === 'spec') return 'Ready'
  return 'Draft'
}

function SpecCard({ spec, onBuild }: { spec: Spec; onBuild: (s: Spec) => void }) {
  const ds = displayStatus(spec.status)
  return (
    <div
      data-testid={`kanban-card-${spec.id}`}
      className="rounded-xl bg-slate-800/50 p-4 flex flex-col gap-2 hover:-translate-y-px transition-transform cursor-pointer"
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-medium text-slate-200 flex-1">{spec.title}</span>
        <span
          data-testid="card-type-chip"
          className="text-xs px-2 py-0.5 rounded-full bg-slate-600 text-slate-300 shrink-0"
        >
          Spec
        </span>
      </div>
      <div className="flex items-center gap-1.5 flex-wrap">
        <span
          data-testid="card-status-pill"
          className="text-xs px-2 py-0.5 rounded-full bg-slate-700 text-slate-400"
        >
          {ds}
        </span>
        {spec.task_ids.map((tid) => (
          <span
            key={tid}
            data-testid={`card-task-chip-${tid}`}
            className="text-xs px-2 py-0.5 rounded-full bg-slate-700/60 text-slate-500"
          >
            {tid}
          </span>
        ))}
      </div>
      {ds === 'Ready' && (
        <button
          data-testid="card-build-button"
          onClick={() => onBuild(spec)}
          className="self-start text-xs bg-blue-600 hover:bg-blue-500 text-white font-medium px-3 py-1 rounded-lg"
        >
          Build
        </button>
      )}
    </div>
  )
}

function TaskCard({ task }: { task: Task }) {
  return (
    <div
      data-testid={`kanban-card-${task.id}`}
      className="rounded-xl bg-slate-800/50 p-4 flex flex-col gap-2 hover:-translate-y-px transition-transform cursor-pointer"
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-medium text-slate-200 flex-1">{task.title}</span>
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
          className="text-xs px-2 py-0.5 rounded-full bg-slate-700 text-slate-400"
        >
          {task.status}
        </span>
        {task.spec_id && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-slate-700/60 text-slate-500">
            → {task.spec_id}
          </span>
        )}
      </div>
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
  const [filter, setFilter] = useState<'all' | 'specs' | 'tasks'>('all')
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
  const showSpecs = filter !== 'tasks'
  const showTasks = filter !== 'specs'

  const draftingSpecs = showSpecs ? specs.filter((s) => s.status === 'draft' && matches(s.title)) : []
  const readySpecs = showSpecs
    ? specs.filter((s) => (s.status === 'ready' || s.status === 'spec') && matches(s.title))
    : []
  const openTasks = showTasks ? tasks.filter((t) => t.status === 'open' && matches(t.title)) : []
  const inProgressTasks = showTasks
    ? tasks.filter((t) => t.status === 'in_progress' && matches(t.title))
    : []

  const chipClass = (active: boolean) =>
    `text-xs px-3 py-1 rounded-full cursor-pointer transition-colors ${
      active ? 'bg-slate-600 text-white' : 'bg-slate-800 text-slate-400 hover:text-slate-200'
    }`

  return (
    <div data-testid="backlog-allview" className="flex flex-col gap-4">
      <div className="flex items-center gap-2 flex-wrap">
        <button
          data-testid="filter-chip-all"
          onClick={() => setFilter('all')}
          className={chipClass(filter === 'all')}
        >
          All
        </button>
        <button
          data-testid="filter-chip-specs-only"
          onClick={() => setFilter('specs')}
          className={chipClass(filter === 'specs')}
        >
          Specs only
        </button>
        <button
          data-testid="filter-chip-tasks-only"
          onClick={() => setFilter('tasks')}
          className={chipClass(filter === 'tasks')}
        >
          Tasks only
        </button>
        <button data-testid="filter-chip-stale" className={chipClass(false)}>
          Stale &gt; 30d
        </button>
        <input
          data-testid="filter-search-input"
          type="text"
          placeholder="Search..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="ml-auto text-xs bg-slate-800 text-slate-300 placeholder-slate-500 rounded-lg px-3 py-1 border border-slate-700 focus:outline-none focus:border-slate-500"
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
