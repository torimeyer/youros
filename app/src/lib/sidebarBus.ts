// Tiny in-app pub/sub that lets any page tell the sidebar "I just changed
// agents" or "I just changed tasks" (or "I just created a spec") so the
// badges and pages can refetch immediately instead of waiting out the next
// poll tick.
//
// This is the fallback for near-real-time updates when the server has no
// general broadcast channel for lifecycle events. Pages that mutate state
// (spawn an agent, create a task, make a spec, etc.) call bumpAgents(),
// bumpTasks(), or bumpSpecs(). Subscribers refetch on each bump.
//
// The api wrapper calls these automatically for any non-GET request whose
// path starts with /agents, /tasks, or /specs, so most callers do not need
// to do anything. Direct callers (tests, edge cases) can still bump
// manually.
//
// Cross-tab delivery: when the browser supports BroadcastChannel, bumps
// are also fanned out to every other tab on the same origin so the
// sidebar badge in tab A updates the instant tab B spawns or completes
// an agent, without waiting on the 2 s poll. Tabs without
// BroadcastChannel (older browsers, some jsdom versions) silently skip
// this step. The 2 s poll remains a belt-and-suspenders fallback in
// every case.

type Listener = () => void

const agentsListeners = new Set<Listener>()
const tasksListeners = new Set<Listener>()
const specsListeners = new Set<Listener>()
// Calendar bus. Same pattern as tasks/agents/specs. Pages that create,
// update, or delete calendar events (the Calendar tab itself, plus
// chat's create_calendar_event tool_result handler) call bumpCalendar()
// so the Calendar page refetches right away instead of waiting for a
// manual reload or its next mount. Needle for "chat adds event but
// Calendar tab stays stale" bug.
const calendarListeners = new Set<Listener>()

// Names the user has locally dismissed (cancelled or deleted from the
// Agents page). Shared at module scope so BOTH the Agents page and the
// Sidebar badge consult the same set. Before this lived only in a
// component-local ref inside Agents.tsx, so the Sidebar kept counting
// dismissed agents for one or two poll ticks until the backend status
// caught up. Also fanned out across tabs via BroadcastChannel so dismiss
// in tab A hides the row in tab B without waiting on the next poll.
const dismissedAgents = new Set<string>()

type BroadcastPayload =
  | { kind: 'agents' | 'tasks' | 'specs' | 'calendar' }
  | { kind: 'dismiss'; name: string }

// BroadcastChannel is wrapped in a try/catch because some environments
// (Safari private mode, older jsdom) expose the constructor but throw
// when constructed. Falls back to null when unavailable.
let channel: BroadcastChannel | null = null
try {
  if (typeof BroadcastChannel !== 'undefined') {
    channel = new BroadcastChannel('myos-sidebar-bus')
    channel.onmessage = (ev: MessageEvent<BroadcastPayload>) => {
      const kind = ev.data?.kind
      if (kind === 'agents') {
        fanOut(agentsListeners)
      } else if (kind === 'tasks') {
        fanOut(tasksListeners)
      } else if (kind === 'specs') {
        fanOut(specsListeners)
      } else if (kind === 'calendar') {
        fanOut(calendarListeners)
      } else if (kind === 'dismiss') {
        // Another tab just dismissed an agent locally. Mirror it into this
        // tab's set so our Sidebar badge and Agents list filter match.
        // Note: do NOT re-post on the channel (that would cause an echo
        // loop). postMessage already does not fire onmessage in the sender.
        const name = ev.data?.name
        if (name) dismissedAgents.add(name)
      }
    }
  }
} catch {
  channel = null
}

function fanOut(listeners: Set<Listener>): void {
  for (const fn of Array.from(listeners)) {
    try {
      fn()
    } catch {
      // Never let a single subscriber throw and block the others.
    }
  }
}

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

export function onSpecsChange(fn: Listener): () => void {
  specsListeners.add(fn)
  return () => {
    specsListeners.delete(fn)
  }
}

export function onCalendarChange(fn: Listener): () => void {
  calendarListeners.add(fn)
  return () => {
    calendarListeners.delete(fn)
  }
}

export function bumpAgents(): void {
  fanOut(agentsListeners)
  // Notify other tabs. postMessage never fires onmessage in the sender,
  // so local listeners only run once.
  try {
    channel?.postMessage({ kind: 'agents' } satisfies BroadcastPayload)
  } catch {
    // ignore transient BroadcastChannel errors
  }
}

export function bumpTasks(): void {
  fanOut(tasksListeners)
  try {
    channel?.postMessage({ kind: 'tasks' } satisfies BroadcastPayload)
  } catch {
    // ignore
  }
}

export function bumpSpecs(): void {
  fanOut(specsListeners)
  try {
    channel?.postMessage({ kind: 'specs' } satisfies BroadcastPayload)
  } catch {
    // ignore
  }
}

export function bumpCalendar(): void {
  fanOut(calendarListeners)
  try {
    channel?.postMessage({ kind: 'calendar' } satisfies BroadcastPayload)
  } catch {
    // ignore
  }
}

// Fire every channel at once. Used by the global freshness hooks
// (window focus, tab visibility, resume-from-idle) so the whole app
// "perks up" the moment the user returns or resumes activity. Every
// existing subscriber refetches on its own channel, so this is enough
// to refresh agents/tasks/specs badges, the running-agents panel, and
// any page that listens on these buses, without scattering refetch
// calls across components.
export function bumpAll(): void {
  bumpAgents()
  bumpTasks()
  bumpSpecs()
  bumpCalendar()
}

// Test-only helper.
export function _resetSidebarBus(): void {
  agentsListeners.clear()
  tasksListeners.clear()
  specsListeners.clear()
  calendarListeners.clear()
  dismissedAgents.clear()
}

// Mark an agent name as locally dismissed. Both the Agents page (when the
// user cancels or removes a row) and the Sidebar (when filtering the badge
// count) consult the same set, so the badge updates in the same render
// tick. Also fanned out to other tabs on the same origin.
export function addDismissed(name: string): void {
  if (!name) return
  dismissedAgents.add(name)
  try {
    channel?.postMessage({ kind: 'dismiss', name } satisfies BroadcastPayload)
  } catch {
    // ignore transient BroadcastChannel errors
  }
}

// Pure check. Returns true when the given name has been locally dismissed.
export function isDismissed(name: string): boolean {
  return dismissedAgents.has(name)
}

// Read-only view of the dismissed set. Handy for tests and debugging.
export function getDismissed(): ReadonlySet<string> {
  return dismissedAgents
}

// Undo a dismissal. Used when the user respawns an agent with the same
// name so the placeholder row is not hidden by a stale entry.
export function clearDismissed(name: string): void {
  dismissedAgents.delete(name)
}
