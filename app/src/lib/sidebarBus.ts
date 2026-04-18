// Tiny in-app pub/sub that lets any page tell the sidebar "I just changed
// agents" or "I just changed tasks" so the badges can refetch immediately
// instead of waiting out the next poll tick.
//
// This is the fallback for near-real-time badge updates when the server has
// no general broadcast channel for agent/task lifecycle events. Pages that
// mutate state (spawn an agent, create a task, close a task, etc.) call
// bumpAgents() or bumpTasks(). The sidebar subscribes and refetches on each
// bump.
//
// The api wrapper calls these automatically for any non-GET request whose
// path starts with /agents or /tasks, so most callers do not need to do
// anything. Direct callers (tests, edge cases) can still bump manually.

type Listener = () => void

const agentsListeners = new Set<Listener>()
const tasksListeners = new Set<Listener>()

export function onAgentsChange(fn: Listener): () => void {
  agentsListeners.add(fn)
  return () => {
    agentsListeners.delete(fn)
  }
}

export function onTasksChange(fn: Listener): () => void {
  tasksListeners.add(fn)
  return () => {
    tasksListeners.delete(fn)
  }
}

export function bumpAgents(): void {
  for (const fn of Array.from(agentsListeners)) {
    try {
      fn()
    } catch {
      // Never let a single subscriber throw and block the others.
    }
  }
}

export function bumpTasks(): void {
  for (const fn of Array.from(tasksListeners)) {
    try {
      fn()
    } catch {
      // Never let a single subscriber throw and block the others.
    }
  }
}

// Test-only helper.
export function _resetSidebarBus(): void {
  agentsListeners.clear()
  tasksListeners.clear()
}
