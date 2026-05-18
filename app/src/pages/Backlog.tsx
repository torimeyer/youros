import { useState, useEffect } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { api } from '../lib/api'
import { buildSpec } from '../lib/spawn'
import type { ConflictItem } from '../lib/spawn'
import ConflictDialog from '../components/ConflictDialog'
function displayStatus(s: string): 'Draft' | 'Ready' | 'Building' | 'Done' {
  if (s === 'complete') return 'Done'
  if (s === 'in-progress') return 'Building'
  if (s === 'ready' || s === 'spec') return 'Ready'
  return 'Draft'
}

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

function AllView() {
  const [specs, setSpecs] = useState<Spec[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [conflicts, setConflicts] = useState<ConflictItem[]>([])

  useEffect(() => {
    api.get<Spec[]>('/specs').then(setSpecs).catch(() => {})
    api.get<Task[]>('/tasks').then(setTasks).catch(() => {})
  }, [])

  const specTaskIds = new Set(specs.flatMap((s) => s.task_ids))
  const standaloneTasks = tasks.filter((t) => !specTaskIds.has(t.id))

  async function handleBuild(spec: Spec) {
    const result = await buildSpec(spec.path)
    if (result?.status === 'conflict') {
      setConflicts(result.conflicts)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
          Plans in flight
        </h2>
        <div className="flex flex-col gap-2">
          {specs.map((spec) => {
            const ds = displayStatus(spec.status)
            return (
              <div key={spec.id} className="flex items-center justify-between rounded-lg bg-slate-800/50 px-4 py-3 gap-3">
                <span className="text-sm text-slate-200 flex-1">{spec.title}</span>
                <span className="text-xs px-2 py-0.5 rounded-full bg-slate-700 text-slate-300">{ds}</span>
                {ds === 'Ready' && (
                  <button
                    onClick={() => handleBuild(spec)}
                    className="text-xs bg-blue-600 hover:bg-blue-500 text-white font-medium px-3 py-1 rounded-lg"
                  >
                    Build
                  </button>
                )}
              </div>
            )
          })}
        </div>
      </section>

      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
          Standalone tasks
        </h2>
        <div className="flex flex-col gap-2">
          {standaloneTasks.map((task) => (
            <div key={task.id} className="flex items-center gap-3 rounded-lg bg-slate-800/50 px-4 py-3">
              <span className="text-sm text-slate-200">{task.title}</span>
              <span className="text-xs px-2 py-0.5 rounded-full bg-slate-700 text-slate-400">{task.status}</span>
            </div>
          ))}
          {standaloneTasks.length === 0 && (
            <p className="text-sm text-slate-500">No standalone tasks.</p>
          )}
        </div>
      </section>

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
      active
        ? 'bg-slate-700 text-white'
        : 'text-slate-400 hover:text-slate-200'
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
      {isSpecs && <Specs />}
      {isTasksTab && <Tasks />}
    </div>
  )
}
