import { useState, useEffect, useCallback, useRef } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  closestCenter,
  type DragStartEvent,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Icon from "../components/Icon";
import TopBar from "../components/TopBar";
import { LoadingState, EmptyState } from "../components/ui";
import LabelsView from "../components/LabelsView";
import HealthCheckView from "../components/HealthCheckView";
import type { Label } from "../components/LabelsView";
import { api } from "../lib/api";
import { onTasksChange } from "../lib/sidebarBus";
import SharePopover from "../components/SharePopover";
import ExportButton from "../components/ExportButton";
import TasksAuditModal from "../components/TasksAuditModal";
import PlanWavesPanel from "../components/PlanWavesPanel";
import RecurringTasksSection from "../components/RecurringTasksSection";
import { FilterDrawer, type StatusFilter as StatusPill, type ClosedSortOrder, type SortBy } from "./tasks/FilterDrawer";
// StatusFilter keeps the extended legacy set of single-select string values
// used by Tasks.tsx internal logic (all / shelved / week / recurring). The
// FilterDrawer component now only exposes the three real statuses
// (open / in_progress / closed) which we track separately via
// selectedStatuses below.
type StatusFilter = StatusPill | "all" | "shelved" | "week" | "recurring";

// Statuses the user may set manually. in_progress is reserved for agents only.
export const USER_SELECTABLE_STATUSES = ["open", "closed"] as const;

import ConfirmModal from "../components/ConfirmModal";
import { ComprehensiveBuildPill } from "../components/ComprehensiveBuild";
import { useRunningAgentsStore } from "../stores/runningAgents";

interface Task {
  id: string;
  title: string;
  priority: string;
  status: string;
  created_at: string;
  description?: string;
  tags?: string[];
  goal?: string | null;
  label_ids?: string[];
  auto_label_ids?: string[];
  blocks?: string[];
  depends_on?: string[];
  unblocks?: number;
  closed_at?: string | null;
  closed_reason?: "completed" | "duplicate" | "archived" | null;
  // Claude Code session UUID this task is linked to. Populated by the
  // backend for auto-filed session tasks (the session's own row) and
  // for child tasks that were created during that session. null for
  // tasks with no session link.
  session_id?: string | null;
  // How many tasks were created during this session. Only meaningful
  // for the session's own row (session_id AND description starts with
  // 'session-task:'). Always 0 for child tasks.
  child_task_count?: number;
  // Free-text notes the user can attach to a task. Displayed inline
  // below the description and editable from the detail panel.
  notes?: string | null;
  // Relative path to a plan file if one exists for this needle.
  plan_path?: string | null;
  // Wave number assigned by the last wave-planning run (1-indexed). null when
  // no wave plan has been run yet.
  wave_number?: number | null;
}

// A task is "active" (shown under the Open tab and counted in the
// open/P0/P1/P2 badges) if it is anything other than closed or shelved.
// ostk's state machine produces both "open" and "in_progress" for active
// work, and before this helper existed the page checked strictly
// for status === "open" so every in_progress task was invisible on
// the Tasks tab. See needle 277 for the regression context.
function isActiveTask(t: { status: string }): boolean {
  return t.status !== "closed" && t.status !== "shelved";
}


interface BlockerTask {
  id: string;
  title: string;
  description?: string | null;
  priority?: string | null;
  status?: string | null;
}

interface Blocker {
  text: string;
  resolved: boolean;
  blocker_id?: string;
  blocker_task?: BlockerTask | null;
  explanation?: string | null;
}

interface TaskBriefing {
  task_id: string;
  priority: string;
  status: string;
  title: string;
  sphere: string | null;
  neighbors: string[];
  blocked_by: Blocker[];
  unblocks: string[];
  all_blockers_resolved: boolean;
  raw: string;
}

interface BriefingResponse {
  briefing: TaskBriefing;
}

interface TaskTrace {
  headline: string;
  specs: string[];
  drafts: string[];
  agentfiles: string[];
  depends_on: string[];
  blocks: string[];
  commits: string[];
}

interface TraceResponse {
  trace: TaskTrace;
}

interface TasksResponse {
  tasks: Task[];
}

interface LabelsResponse {
  labels: Label[];
}

const priorityStyles: Record<string, string> = {
  P0: "bg-pink-500/20 text-pink-500",
  P1: "bg-orange-500/20 text-orange-500",
  P2: "bg-blue-500/20 text-blue-500",
  P3: "bg-slate-500/20 text-slate-400",
};

const priorityDotColors: Record<string, string> = {
  P0: "bg-pink-500",
  P1: "bg-orange-500",
  P2: "bg-blue-500",
  P3: "bg-slate-500",
};

const PRIORITIES = ["P0", "P1", "P2", "P3"] as const;

// Returns text up to (and including) the first sentence boundary ("." or ":").
// Used to show a short summary in the collapsed row; full text appears in the
// detail panel when the row is expanded.
export function getFirstSentence(text: string): string {
  const idx = text.search(/[.:]/)
  if (idx < 0) return text
  return text.slice(0, idx + 1).trim()
}

// First-paint cache (needle 299).
//
// Why this exists:
//   Tori filed the same bug three times. /tasks and /agents showed an
//   empty "Loading..." state for several seconds every time she opened
//   the pages cold. Rounds 275 and 278 fixed the backend but she still
//   saw the blank first paint because the page renders empty until the
//   fetch resolves. The render path measured under 300ms in tests with
//   fake latency but that is not what Tori sees. In a real cold browser
//   the gap between mount and first data is whatever the slowest thing
//   in the pipeline takes: vite transform, JS parse, fetch, React
//   commit, StrictMode double mount.
//
// The fix:
//   Cache the last successful /tasks response in localStorage so the
//   VERY FIRST render on the next visit paints rows from that cache
//   instead of an empty state. The live fetch still fires in the
//   background and overwrites the cache when it arrives. Worst case
//   Tori sees slightly stale rows for a fraction of a second before
//   they refresh. Best case she never sees "Loading tasks..." again.
//
// The invariant, enforced by regression tests in Tasks.test.tsx:
//   Every page that fetches primary data on mount must paint real rows
//   within 300ms of render, seeded from localStorage if necessary.
const TASKS_CACHE_KEY = "myos.tasksCache.v1";

function readTasksCache(): Task[] {
  try {
    if (typeof window === "undefined" || !window.localStorage) return [];
    const raw = window.localStorage.getItem(TASKS_CACHE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as Task[]) : [];
  } catch {
    return [];
  }
}

function writeTasksCache(tasks: Task[]) {
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    window.localStorage.setItem(TASKS_CACHE_KEY, JSON.stringify(tasks));
  } catch {
    // Quota or serialization errors are not fatal. Skip the cache.
  }
}

const STALE_DAYS = 7;

function isStale(task: Task): boolean {
  if (task.status !== "open") return false;
  const created = new Date(task.created_at).getTime();
  const now = Date.now();
  return now - created > STALE_DAYS * 24 * 60 * 60 * 1000;
}

interface SortableTaskWrapperProps {
  taskId: string;
  children: (dragHandleProps: React.HTMLAttributes<HTMLSpanElement>) => React.ReactNode;
}

function SortableTaskWrapper({ taskId, children }: SortableTaskWrapperProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: taskId });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  };

  return (
    <div ref={setNodeRef} style={style}>
      {children({ ...attributes, ...listeners })}
    </div>
  );
}

export default function Tasks() {
  const inputRef = useRef<HTMLInputElement>(null);
  const taskRowRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const focusParam = searchParams.get("focus");
  const focusAppliedRef = useRef(false);

  // Seed tasks from the localStorage cache on first mount so the
  // very first paint shows real rows instead of a blank "Loading..."
  // state. The live fetch still runs in useEffect and overwrites
  // this as soon as fresh data arrives. See needle 299.
  const [tasks, setTasks] = useState<Task[]>(() => readTasksCache());
  const [labels, setLabels] = useState<Label[]>([]);
  // Start in the non-loading state when we have cached rows to show,
  // otherwise start loading so the first-ever visit still renders the
  // "Loading tasks..." hint.
  const [loading, setLoading] = useState<boolean>(() => readTasksCache().length === 0);
  const [newTaskTitle, setNewTaskTitle] = useState("");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<"context" | "history" | "related">("context");
  const [briefing, setBriefing] = useState<TaskBriefing | null>(null);
  const [briefingLoading, setBriefingLoading] = useState(false);
  const [trace, setTrace] = useState<TaskTrace | null>(null);
  const [linkedContext, setLinkedContext] = useState<{
    emails: { id: string; subject: string; from: string; snippet: string }[];
    events: { id: string; summary: string; start: string }[];
    files: { id: string; name: string; mimeType: string; webViewLink: string }[];
  } | null>(null);
  const [linkedLoading, setLinkedLoading] = useState(false);
  const [traceLoading, setTraceLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  // Single-select status pill. "all" shows every non-shelved task sorted by
  // status priority (in_progress → open → closed). The legacy statusFilter
  // string is still used for shelved/week/recurring views triggered by code
  // paths that call setStatusFilter directly.
  const [selectedStatus, setSelectedStatus] = useState<StatusPill>("all");
  const [closedSortOrder] = useState<ClosedSortOrder>("newest");
  const [sortBy, setSortBy] = useState<SortBy>("date-desc");
  // Agents currently running or queued. Keyed by task_id.
  const [runningAgentTaskIds, setRunningAgentTaskIds] = useState<Set<string>>(
    () => new Set()
  );
  // Real-time agent list from the app-level WS feed (useRunningAgentsFeed in App.tsx).
  const storeAgents = useRunningAgentsStore((s) => s.agents);
  // Build state per task_id: "running" | "queued" — only set for tasks that
  // have a comprehensive build agent tracked in the build queue.
  const [buildStateByTaskId, setBuildStateByTaskId] = useState<
    Map<string, "running" | "queued">
  >(() => new Map());
  const [banner, setBanner] = useState<string | null>(null);
  const [openPriorityDropdown, setOpenPriorityDropdown] = useState<string | null>(null);
  const [openLabelDropdown, setOpenLabelDropdown] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"tasks" | "labels" | "health">("tasks");
  const [openLinkDropdown, setOpenLinkDropdown] = useState<string | null>(null);
  const [linkTarget, setLinkTarget] = useState("");
  const [commitTaskId, setCommitTaskId] = useState<string | null>(null);
  const [commitMessage, setCommitMessage] = useState("");
  const [commitLoading, setCommitLoading] = useState(false);
  const [commitResult, setCommitResult] = useState<string | null>(null);
  const [autoLabelingTaskId, setAutoLabelingTaskId] = useState<string | null>(null);
  const [labelAllLoading, setLabelAllLoading] = useState(false);
  const [labelAllResult, setLabelAllResult] = useState<string | null>(null);
  const [activeDragId, setActiveDragId] = useState<string | null>(null);
  const [showTaskSharePopover, setShowTaskSharePopover] = useState(false);
  const [undoDelete, setUndoDelete] = useState<{ task: Task; timer: ReturnType<typeof setTimeout> } | null>(null);
  const [closeError, setCloseError] = useState<string | null>(null);
  // IDs the user has clicked delete on but the 5s undo timer has not
  // fired yet. fetchTasks polls every 3s and the real DELETE only
  // fires on timer expiry, so without this guard the next poll hands
  // back the row we just optimistically removed and the task flashes
  // back into view for a second. The set is read only inside
  // fetchTasks so a ref (no re-render) is fine.
  const pendingDeleteIdsRef = useRef<Set<string>>(new Set());
  const [openActionMenu, setOpenActionMenu] = useState<string | null>(null);
  // Notes editing state for the detail panel context tab.
  const [notesValue, setNotesValue] = useState<string>("");
  const [notesSaving, setNotesSaving] = useState(false);
  const [notesSaved, setNotesSaved] = useState(false);
  // Tracks which row's "what is comprehensive build?" help popover is
  // open. Null when none. We track by task id so the popover is
  // anchored next to the right row.
  const [openBuildHelp, setOpenBuildHelp] = useState<string | null>(null);
  const buildHelpRef = useRef<HTMLDivElement | null>(null);
  const [selectedTaskIds, setSelectedTaskIds] = useState<Set<string>>(new Set());
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [auditModalOpen, setAuditModalOpen] = useState(false);
  const [wavesModalOpen, setWavesModalOpen] = useState(false);
  // task_id → wave number from the last planning run. Empty when never planned.
  const [waveAssignments, setWaveAssignments] = useState<Record<string, number>>({});
  // Inline title editing: which task row is in edit mode, the draft value,
  // and a ref to suppress the blur-save when Escape was just pressed.
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const [editingTitleDraft, setEditingTitleDraft] = useState("");
  const titleEditCancelledRef = useRef(false);
  // Priority reason prompt: when a user changes priority, we show a small
  // input asking "Why?" before committing the change. The user can skip it.
  const [pendingPriorityChange, setPendingPriorityChange] = useState<{
    taskId: string;
    priority: string;
  } | null>(null);
  const [priorityReason, setPriorityReason] = useState("");
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [importSource, setImportSource] = useState<'linear' | 'jira'>('linear');
  const [importLoading, setImportLoading] = useState(false);
  const [importResult, setImportResult] = useState<{ ok: boolean; created: number; errors: string[] } | null>(null);
  const [importFields, setImportFields] = useState<Record<string, string>>({});
  const [showOverflowMenu, setShowOverflowMenu] = useState(false);
  const overflowMenuRef = useRef<HTMLDivElement | null>(null);
  const [deleteAllConfirmOpen, setDeleteAllConfirmOpen] = useState(false);
  const [deleteAllLoading, setDeleteAllLoading] = useState(false);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } })
  );

  const fetchTasks = useCallback(async () => {
    // Do NOT flip loading back on while we have cached rows to show.
    // The cache seeded the first paint with real data, so refreshing
    // should happen silently in the background. The only time we
    // ever want the "Loading tasks..." hint is on the very first
    // ever visit when the cache is empty, and the initial useState
    // value handles that case.
    try {
      const res = await api.get<TasksResponse>("/tasks");
      const nextTasks = res.tasks ?? [];
      const pending = pendingDeleteIdsRef.current;
      const visible = pending.size === 0
        ? nextTasks
        : nextTasks.filter((t) => !pending.has(t.id));
      setTasks(visible);
      writeTasksCache(visible);
    } catch (e) {
      console.error("Failed to fetch tasks:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchLabels = useCallback(async () => {
    try {
      const res = await api.get<LabelsResponse>("/labels");
      setLabels(res.labels ?? []);
    } catch (e) {
      console.error("Failed to fetch labels:", e);
    }
  }, []);


  const fetchBriefing = useCallback(async (taskId: string) => {
    setBriefingLoading(true);
    setBriefing(null);
    try {
      const res = await api.get<BriefingResponse>(`/tasks/${taskId}/briefing`);
      setBriefing(res.briefing ?? null);
    } catch (e) {
      console.error("Failed to fetch briefing:", e);
    } finally {
      setBriefingLoading(false);
    }
  }, []);

  const fetchTrace = useCallback(async (taskId: string) => {
    setTraceLoading(true);
    setTrace(null);
    try {
      const res = await api.get<TraceResponse>(`/tasks/${taskId}/trace`);
      setTrace(res.trace ?? null);
    } catch (e) {
      console.error("Failed to fetch trace:", e);
    } finally {
      setTraceLoading(false);
    }
  }, []);

  const fetchLinkedContext = useCallback(async (taskId: string) => {
    setLinkedLoading(true);
    setLinkedContext(null);
    try {
      const res = await api.get<{
        emails: { id: string; subject: string; from: string; snippet: string }[];
        events: { id: string; summary: string; start: string }[];
        files: { id: string; name: string; mimeType: string; webViewLink: string }[];
      }>(`/tasks/${taskId}/context`);
      setLinkedContext(res);
    } catch {
      // Silently fail. The Related tab just shows "No related items."
    } finally {
      setLinkedLoading(false);
    }
  }, []);

  const handleTaskClick = useCallback((taskId: string) => {
    if (selectedTaskId === taskId) {
      setSelectedTaskId(null);
      setBriefing(null);
      setTrace(null);
      setLinkedContext(null);
    } else {
      setSelectedTaskId(taskId);
      setDetailTab("context");
      fetchBriefing(taskId);
      fetchTrace(taskId);
      fetchLinkedContext(taskId);
      // Sync notes editor with the newly selected task's current notes value.
      const clicked = tasks.find(t => t.id === taskId);
      setNotesValue(clicked?.notes ?? "");
      setNotesSaved(false);
    }
  }, [selectedTaskId, tasks, fetchBriefing, fetchTrace, fetchLinkedContext]);

  useEffect(() => {
    fetchTasks();
    fetchLabels();
    // Load any previously saved wave assignments so task rows can show badges
    // without having to open the plan-waves panel first.
    api.get<{ assignments: Record<string, number> }>('/tasks/waves/assignments')
      .then((res) => setWaveAssignments(res.assignments ?? {}))
      .catch(() => { /* silent — no assignments yet */ });
  }, [fetchTasks, fetchLabels]);

  // Poll running agents every 3s to drive the per-row in-progress indicator
  // (needle: no live feedback that the agent is working on a task). The
  // endpoint returns {agents: [...]}; we extract any agent whose status is
  // non-terminal (running / queued / starting) and whose label or task_id
  // references a task id we already know about.
  useEffect(() => {
    let cancelled = false;
    const refreshAgents = async () => {
      try {
        const resp = await api.get("/agents") as {
          agents?: Array<{
            status?: string;
            task_id?: string | null;
            needle_id?: string | null;
            label?: string | null;
            build_state?: "running" | "queued" | null;
          }>;
        };
        if (cancelled) return;
        const next = new Set<string>();
        const nextBuildStates = new Map<string, "running" | "queued">();
        const terminal = new Set([
          "completed",
          "succeeded",
          "failed",
          "error",
          "cancelled",
          "canceled",
          "stopped",
          "timeout",
        ]);
        for (const a of resp?.agents ?? []) {
          const isRunning = !a.status || !terminal.has(a.status.toLowerCase());
          if (!isRunning) continue;
          if (a.task_id) next.add(a.task_id);
          // Needle-linked agents carry needle_id instead of task_id.
          // Task ids in the UI are formatted as "→<needle_id>" (→ + number).
          if (a.needle_id) next.add(`→${a.needle_id}`);
          // Some agents encode the task id in their label, e.g.
          // "plan/task-123" or "comprehensive/task-123". Accept any
          // label containing a token that matches a known task id.
          if (a.label) {
            // Extract needle ID from names like "fix-1062-description-hash"
            const needleInLabel = a.label.match(/^[a-z_]+-(\d{3,6})-/)
            if (needleInLabel) next.add(`→${needleInLabel[1]}`)
            for (const tok of a.label.split(/[^A-Za-z0-9_-]+/)) {
              if (tok) next.add(tok);
            }
          }
          // Track build_state for comprehensive builds so we can show
          // "Building" vs "Queued" pills instead of the generic "In progress".
          if (a.build_state && a.task_id) {
            nextBuildStates.set(a.task_id, a.build_state);
          }
        }
        setRunningAgentTaskIds(next);
        setBuildStateByTaskId(nextBuildStates);
      } catch {
        // silent; /agents may not exist in some test harnesses.
      }
    };
    refreshAgents();
    const interval = setInterval(refreshAgents, 30000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  // Real-time fast path: re-derive runningAgentTaskIds from the app-level
  // running agents store (populated by useRunningAgentsFeed → /ws/agents/state).
  // Fires within milliseconds of a WS delta instead of waiting up to 3s for
  // the HTTP poll above. The HTTP poll remains as a safety-net when WS drops.
  useEffect(() => {
    const terminal = new Set(["completed", "succeeded", "failed", "error", "cancelled", "canceled", "stopped", "timeout"]);
    const next = new Set<string>();
    const nextBuildStates = new Map<string, "running" | "queued">();
    for (const a of storeAgents) {
      const isRunning = !a.status || !terminal.has(a.status.toLowerCase());
      if (!isRunning) continue;
      if (a.task_id) next.add(a.task_id);
      if (a.needle_id) next.add(`→${a.needle_id}`);
      if (a.label) {
        const needleInLabel = a.label.match(/^[a-z_]+-(\d{3,6})-/);
        if (needleInLabel) next.add(`→${needleInLabel[1]}`);
        for (const tok of a.label.split(/[^A-Za-z0-9_-]+/)) {
          if (tok) next.add(tok);
        }
      }
      if (a.build_state && a.task_id) {
        nextBuildStates.set(a.task_id, a.build_state);
      }
    }
    setRunningAgentTaskIds(next);
    setBuildStateByTaskId(nextBuildStates);
  }, [storeAgents]);

  // Live updates for new tasks. Two channels:
  //
  //  1. sidebarBus: any frontend-initiated task mutation (POST /tasks, close,
  //     reorder, delete, quick-spawn, Calendar add, Slack reply-to-task, etc.)
  //     goes through api.ts which calls bumpTasks() on success. Subscribing
  //     here means the Tasks page refetches within milliseconds of the user
  //     clicking "Add task" anywhere in the app.
  //
  //  2. Safety-net poll every 3 s: backend-initiated creations (AC fallback,
  //     builders, session-task auto-file) never touch the frontend api wrapper
  //     and therefore never bump the bus. Without a poll they would stay
  //     invisible until the user manually navigated away and back. 3 s keeps
  //     the page feeling live without hammering the API.
  useEffect(() => {
    const off = onTasksChange(() => {
      fetchTasks();
    });
    const interval = setInterval(() => {
      fetchTasks();
    }, 3000);
    return () => {
      off();
      clearInterval(interval);
    };
  }, [fetchTasks]);

  // Deep-link handler: when ?focus=<id> is in the URL, auto-select that task,
  // switch to the correct tab if needed, scroll it into view, then clear the
  // query param so a page refresh will not keep re-scrolling.
  useEffect(() => {
    if (!focusParam) return;
    if (focusAppliedRef.current) return;
    if (tasks.length === 0) return;

    const match = tasks.find((t) => t.id === focusParam);
    if (!match) return;

    focusAppliedRef.current = true;

    // Make sure we are on the Tasks tab (not Labels / Health / Groups)
    setActiveTab("tasks");

    // Make sure the task is visible under the current status filter. If the
    // focused task is closed or shelved but the current filter hides it, switch.
    // "all" shows everything, so no switch needed.
    if (statusFilter !== "all") {
      if (match.status === "shelved" && statusFilter !== "shelved") {
        setStatusFilter("shelved");
      }
    }
    // Always switch to the status that frames the focused task so the user
    // sees the relevant context (e.g. deep-linking a closed task shows the
    // closed view, not everything).
    if (match.status === "closed") {
      setSelectedStatus("closed");
    } else if (runningAgentTaskIds.has(match.id)) {
      setSelectedStatus("in_progress");
    } else if (isActiveTask(match)) {
      setSelectedStatus("open");
    }

    // Expand the briefing for this task.
    setSelectedTaskId(match.id);
    setDetailTab("context");
    fetchBriefing(match.id);
    fetchTrace(match.id);

    // Scroll the row into view once the DOM has rendered it.
    setTimeout(() => {
      const node = taskRowRefs.current[match.id];
      if (node && typeof node.scrollIntoView === "function") {
        node.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }, 60);

    // Clear the query param so a refresh will not re-apply this.
    const next = new URLSearchParams(searchParams);
    next.delete("focus");
    setSearchParams(next, { replace: true });
  }, [focusParam, tasks, statusFilter, fetchBriefing, fetchTrace, searchParams, setSearchParams]);

  // Listen for quick-add-task event from TopBar
  useEffect(() => {
    const handler = () => {
      setActiveTab("tasks");
      inputRef.current?.focus();
    };
    window.addEventListener('myos-quick-add-task', handler);
    return () => window.removeEventListener('myos-quick-add-task', handler);
  }, []);

  // Handle ?new=1 URL param (fired by keyboard shortcut 'n' in Layout.tsx)
  useEffect(() => {
    if (searchParams.get("new") !== "1") return;
    setActiveTab("tasks");
    setTimeout(() => inputRef.current?.focus(), 50);
    const next = new URLSearchParams(searchParams);
    next.delete("new");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  // Close dropdowns when clicking outside
  useEffect(() => {
    if (!openPriorityDropdown && !openLabelDropdown && !openLinkDropdown && !openActionMenu) return;
    const handleClick = () => {
      setOpenPriorityDropdown(null);
      setOpenLabelDropdown(null);
      setOpenLinkDropdown(null);
      setOpenActionMenu(null);
      setOpenBuildHelp(null);
    };
    document.addEventListener("click", handleClick);
    return () => document.removeEventListener("click", handleClick);
  }, [openPriorityDropdown, openLabelDropdown, openLinkDropdown, openActionMenu]);

  // Overflow menu closes on outside click.
  useEffect(() => {
    if (!showOverflowMenu) return;
    const handleMouseDown = (e: MouseEvent) => {
      if (overflowMenuRef.current && !overflowMenuRef.current.contains(e.target as Node)) {
        setShowOverflowMenu(false);
      }
    };
    document.addEventListener("mousedown", handleMouseDown);
    return () => document.removeEventListener("mousedown", handleMouseDown);
  }, [showOverflowMenu]);

  // Escape closes the Linear/Jira import modal.
  useEffect(() => {
    if (!importModalOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setImportModalOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [importModalOpen]);

  // "What is comprehensive build?" help popover closes on outside click
  // and on Escape. Uses a ref so clicks inside the popover itself do
  // not dismiss it. See needle 295 for the plain-language help copy.
  useEffect(() => {
    if (!openBuildHelp) return;
    const handleMouseDown = (e: MouseEvent) => {
      if (buildHelpRef.current && !buildHelpRef.current.contains(e.target as Node)) {
        setOpenBuildHelp(null);
      }
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpenBuildHelp(null);
      }
    };
    document.addEventListener("mousedown", handleMouseDown);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleMouseDown);
      document.removeEventListener("keydown", handleKey);
    };
  }, [openBuildHelp]);

  const addTask = async () => {
    const title = newTaskTitle.trim();
    if (!title) return;
    try {
      await api.post("/tasks", { title, priority: "P1" });
      setNewTaskTitle("");
      await fetchTasks();
    } catch (e) {
      console.error("Failed to add task:", e);
    }
  };

  const closeTask = async (id: string) => {
    try {
      await api.post(`/tasks/${id}/close?source=user`);
      await fetchTasks();
    } catch (e: unknown) {
      console.error("Failed to close task:", e);
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        "Couldn't close the task. Please try again.";
      setCloseError(msg);
      setTimeout(() => setCloseError(null), 5000);
    }
  };

  const reopenTask = async (id: string) => {
    try {
      await api.post(`/tasks/${id}/reopen`);
      await fetchTasks();
    } catch (e) {
      console.error("Failed to reopen task:", e);
    }
  };

  const shelveTask = async (id: string) => {
    try {
      setTasks((prev) =>
        prev.map((t) => (t.id === id ? { ...t, status: "shelved" } : t))
      );
      setOpenActionMenu(null);
      await api.post(`/tasks/${id}/shelve`);
      await fetchTasks();
    } catch (e) {
      console.error("Failed to pause task:", e);
      await fetchTasks();
    }
  };

  const unshelveTask = async (id: string) => {
    try {
      setTasks((prev) =>
        prev.map((t) => (t.id === id ? { ...t, status: "open" } : t))
      );
      setOpenActionMenu(null);
      await api.post(`/tasks/${id}/unshelve`);
      await fetchTasks();
    } catch (e) {
      console.error("Failed to resume task:", e);
      await fetchTasks();
    }
  };

  const deleteTask = (id: string) => {
    // Cancel any previous pending delete
    if (undoDelete) {
      clearTimeout(undoDelete.timer);
      api.delete(`/tasks/${undoDelete.task.id}`).catch(() => {});
      pendingDeleteIdsRef.current.delete(undoDelete.task.id);
    }

    const task = tasks.find((t) => t.id === id);
    if (!task) return;

    // Optimistically remove from UI
    if (selectedTaskId === id) {
      setSelectedTaskId(null);
      setBriefing(null);
      setTrace(null);
    }
    setTasks((prev) => prev.filter((t) => t.id !== id));
    pendingDeleteIdsRef.current.add(id);

    // Start a 5-second timer before actually deleting
    const timer = setTimeout(() => {
      api.delete(`/tasks/${id}`)
        .catch((e) => console.error("Failed to delete task:", e))
        .finally(() => {
          pendingDeleteIdsRef.current.delete(id);
        });
      setUndoDelete(null);
    }, 5000);

    setUndoDelete({ task, timer });
  };

  const handleUndo = () => {
    if (!undoDelete) return;
    clearTimeout(undoDelete.timer);
    pendingDeleteIdsRef.current.delete(undoDelete.task.id);
    setTasks((prev) => [...prev, undoDelete.task]);
    setUndoDelete(null);
  };

  const updatePriority = async (id: string, priority: string) => {
    // Show a small "Why?" prompt so the reason can be logged. The user
    // can skip it (commits without a reason) or type a short note.
    setOpenPriorityDropdown(null);
    setPendingPriorityChange({ taskId: id, priority });
    setPriorityReason("");
  };

  const commitPriorityChange = async (skip?: boolean) => {
    if (!pendingPriorityChange) return;
    const { taskId, priority } = pendingPriorityChange;
    const reason = skip ? undefined : priorityReason.trim() || undefined;
    setPendingPriorityChange(null);
    setPriorityReason("");
    try {
      setTasks((prev) =>
        prev.map((t) => (t.id === taskId ? { ...t, priority } : t))
      );
      await api.patch(`/tasks/${taskId}`, { priority, reason });
    } catch (e) {
      console.error("Failed to update priority:", e);
      await fetchTasks();
    }
  };

  const updateTaskTitle = async (taskId: string, newTitle: string) => {
    const trimmed = newTitle.trim();
    setEditingTaskId(null);
    if (!trimmed) {
      setBanner("Title can't be empty.");
      setTimeout(() => setBanner(null), 3000);
      return;
    }
    const prev = tasks.find((t) => t.id === taskId)?.title ?? "";
    if (trimmed === prev) return;
    setTasks((ts) => ts.map((t) => (t.id === taskId ? { ...t, title: trimmed } : t)));
    try {
      await api.patch(`/tasks/${taskId}`, { title: trimmed });
    } catch (e) {
      console.error("Failed to update title:", e);
      setTasks((ts) => ts.map((t) => (t.id === taskId ? { ...t, title: prev } : t)));
      setBanner("Couldn't save the title. Try again.");
      setTimeout(() => setBanner(null), 4000);
    }
  };

  const assignLabel = async (taskId: string, labelId: string) => {
    try {
      await api.post(`/tasks/${taskId}/labels/${labelId}`);
      // Optimistic update
      setTasks((prev) =>
        prev.map((t) =>
          t.id === taskId
            ? { ...t, label_ids: [...(t.label_ids || []), labelId] }
            : t
        )
      );
      setOpenLabelDropdown(null);
      fetchLabels(); // Refresh label counts
    } catch (e) {
      console.error("Failed to assign label:", e);
    }
  };

  const removeLabel = async (taskId: string, labelId: string) => {
    try {
      await api.delete(`/tasks/${taskId}/labels/${labelId}`);
      // Optimistic update
      setTasks((prev) =>
        prev.map((t) =>
          t.id === taskId
            ? { ...t, label_ids: (t.label_ids || []).filter((id) => id !== labelId) }
            : t
        )
      );
      fetchLabels(); // Refresh label counts
    } catch (e) {
      console.error("Failed to remove label:", e);
    }
  };

  const handleCommit = async (taskId: string) => {
    const msg = commitMessage.trim();
    if (!msg) return;
    setCommitLoading(true);
    setCommitResult(null);
    try {
      const res = await api.post<{ result: string }>(`/tasks/${taskId}/commit`, { message: msg });
      setCommitResult(res.result ?? "Saved.");
      setCommitMessage("");
      // Refresh trace if open (to show the new commit)
      if (selectedTaskId === taskId) {
        fetchTrace(taskId);
      }
    } catch (e) {
      setCommitResult("Something went wrong. Please try again.");
      console.error("Failed to commit:", e);
    } finally {
      setCommitLoading(false);
    }
  };


  const linkTask = async (sourceId: string, relation: string, targetId: string) => {
    try {
      await api.post(`/tasks/${sourceId}/link`, { target: targetId, relation });
      setOpenLinkDropdown(null);
      setLinkTarget("");
      await fetchTasks();
    } catch (e) {
      console.error("Failed to link tasks:", e);
    }
  };

  const unlinkTask = async (sourceId: string, relation: string, targetId: string) => {
    try {
      await api.delete(`/tasks/${sourceId}/link?target=${targetId}&relation=${relation}`);
      await fetchTasks();
    } catch (e) {
      console.error("Failed to unlink tasks:", e);
    }
  };

  const autoLabelTask = async (taskId: string) => {
    setAutoLabelingTaskId(taskId);
    try {
      const res = await api.post<{ label_ids: string[] }>(`/tasks/${taskId}/labels/auto`);
      setTasks((prev) =>
        prev.map((t) =>
          t.id === taskId ? { ...t, label_ids: res.label_ids ?? t.label_ids } : t
        )
      );
      fetchLabels();
    } catch (e: unknown) {
      const msg = (e as { message?: string })?.message || '';
      setBanner(msg.includes('API key') ? msg : 'Auto-labeling failed.');
      setTimeout(() => setBanner(null), 5000);
    } finally {
      setAutoLabelingTaskId(null);
    }
  };

  const labelAllTasks = async () => {
    setLabelAllLoading(true);
    setLabelAllResult(null);
    try {
      const res = await api.post<{ labeled: number }>("/tasks/backfill-labels");
      const count = res.labeled ?? 0;
      setLabelAllResult(count === 0 ? "All tasks already labeled." : `Labeled ${count} task${count === 1 ? "" : "s"}.`);
      await fetchTasks();
      fetchLabels();
    } catch (e: unknown) {
      const msg = (e as { message?: string })?.message || '';
      setLabelAllResult(msg.includes('API key') ? msg : 'Something went wrong. Please try again.');
    } finally {
      setLabelAllLoading(false);
      setTimeout(() => setLabelAllResult(null), 5000);
    }
  };

  // "plan" keeps the old one-shot plan agent.
  // "comprehensive" runs the full build pattern: plan, build, write tests,
  // run tests, run pytest and tsc, only report done when everything is green.
  // "quick" is the legacy one-shot that just writes code with
  // no tests and no gates. Kept as an escape hatch for fast drafts.
  //
  // The backend accepts template="comprehensive" as the primary name
  // and template="saa" as an alias, to match Tori's muscle memory.
  // We post "comprehensive" from the UI to keep telemetry clean.
  type SpawnMode = "plan" | "comprehensive" | "quick";

  const spawnAgentForTask = async (taskId: string, mode: SpawnMode) => {
    const task = tasks.find((t) => t.id === taskId);
    if (!task) return;
    setActionLoading(taskId);
    try {
      let prompt: string;
      let namePrefix: string;
      let bannerLabel: string;
      // Build-mode spawns route to isolation="worktree" because their prompts
      // contain edit verbs ("implement"). The server rejects locks:["*"] for
      // worktree spawns — the wildcard is the read-only opt-out only.
      // Plan-mode spawns have no edit verbs so they get isolation="none" where
      // locks:["*"] is the correct opt-out. Use a task-specific lock so
      // parallel spawns for different tasks never conflict with each other,
      // while a duplicate spawn for the same task still gets 409.
      const BUILD_LOCKS = [`tasks/${taskId}`];

      const body: {
        name: string;
        prompt: string;
        model: string;
        budget: number;
        template?: string;
        task_id: string;
        locks: string[];
        isolation?: string;
      } = {
        name: "",
        prompt: "",
        model: "sonnet",
        budget: 2.0,
        task_id: taskId,
        locks: [],
      };

      if (mode === "plan") {
        prompt = `Create a detailed plan for this task: "${task.title}". Break it down into steps, identify risks, and estimate effort.`;
        namePrefix = "plan";
        bannerLabel = "Plan";
        body.locks = ["*"];
        body.isolation = "none";
      } else if (mode === "comprehensive") {
        prompt = `Implement this task: "${task.title}". Follow the comprehensive build pattern. Plan the approach, build the solution, write tests, run them, and only report done when everything is green.`;
        namePrefix = "implement";
        bannerLabel = "Comprehensive build";
        body.template = "comprehensive";
        body.locks = BUILD_LOCKS;
      } else {
        prompt = `Implement this task: "${task.title}". Write the code, tests, and documentation needed.`;
        namePrefix = "implement";
        bannerLabel = "Quick build";
        body.locks = BUILD_LOCKS;
      }

      body.name = `${namePrefix}-${taskId.replace(/[^a-zA-Z0-9]/g, "")}`;
      body.prompt = prompt;

      await api.post("/agents/spawn", body);
      setBanner(`${bannerLabel} started for "${task.title}".`);
      setTimeout(() => setBanner(null), 4000);
    } catch (e) {
      console.error(`Failed to spawn ${mode} agent:`, e);
      const label = mode === "plan" ? "plan" : mode === "comprehensive" ? "Comprehensive build" : "Quick build";
      const detail =
        e != null &&
        typeof e === 'object' &&
        (e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
      const isLockConflict =
        (e as { status?: number })?.status === 409 &&
        typeof detail === 'object' &&
        detail !== null &&
        (detail as { error?: string }).error === 'lock_conflict';
      if (isLockConflict) {
        const heldBy = (detail as { conflicts?: Array<{ held_by_spawn?: string }> }).conflicts?.[0]?.held_by_spawn;
        setBanner(
          heldBy
            ? `Another build (${heldBy}) is editing some of the same files. Wait for it to finish, then try again.`
            : `Another build is already running on the same files. Wait for it to finish, then try again.`
        );
      } else {
        setBanner(`Could not start ${label}. Please try again.`);
      }
      setTimeout(() => setBanner(null), 4000);
    } finally {
      setActionLoading(null);
      setOpenActionMenu(null);
      setOpenBuildHelp(null);
    }
  };


  const toggleTaskSelection = (taskId: string) => {
    setSelectedTaskIds((prev) => {
      const next = new Set(prev);
      if (next.has(taskId)) {
        next.delete(taskId);
      } else {
        next.add(taskId);
      }
      return next;
    });
  };

  // Bulk Implement all defaults to the comprehensive build pattern.
  // One button, not two, matches the per-row menu default. See needle 295.
  const bulkAction = async (mode: "plan" | "implement") => {
    const ids = Array.from(selectedTaskIds);
    if (ids.length === 0) return;
    setActionLoading("bulk");
    try {
      for (const id of ids) {
        if (mode === "plan") {
          await spawnAgentForTask(id, "plan");
        } else if (mode === "implement") {
          await spawnAgentForTask(id, "comprehensive");
        }
      }
      setSelectedTaskIds(new Set());
    } finally {
      setActionLoading(null);
    }
  };

  const reorderTask = async (taskId: string, newPriority: string, position: number) => {
    try {
      await api.post("/tasks/reorder", { task_id: taskId, new_priority: newPriority, position });
    } catch (e) {
      console.error("Failed to save task order:", e);
      // Revert optimistic update on error
      await fetchTasks();
    }
  };

  const handleDragStart = (event: DragStartEvent) => {
    setActiveDragId(String(event.active.id));
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    setActiveDragId(null);

    if (!over) return;

    const activeId = String(active.id);
    const overId = String(over.id);

    const activeTask = tasks.find((t) => t.id === activeId);
    if (!activeTask) return;

    // Determine target priority
    let targetPriority: string;
    let targetTaskId: string | null = null;

    if (overId.startsWith("group-")) {
      targetPriority = overId.replace("group-", "");
    } else {
      const overTask = tasks.find((t) => t.id === overId);
      if (!overTask) return;
      targetPriority = overTask.priority;
      targetTaskId = overTask.id;
    }

    // Build ordered list for the target priority group
    const groupTasks = tasks.filter((t) => t.priority === targetPriority);
    let newGroupOrder: Task[];

    if (activeTask.priority === targetPriority) {
      // Reordering within the same group
      const oldIndex = groupTasks.findIndex((t) => t.id === activeId);
      const newIndex = targetTaskId
        ? groupTasks.findIndex((t) => t.id === targetTaskId)
        : groupTasks.length - 1;
      if (oldIndex === newIndex) return;
      newGroupOrder = arrayMove(groupTasks, oldIndex, newIndex);
    } else {
      // Moving to a different priority group
      const newIndex = targetTaskId
        ? groupTasks.findIndex((t) => t.id === targetTaskId)
        : groupTasks.length;
      const insertAt = newIndex < 0 ? groupTasks.length : newIndex;
      newGroupOrder = [
        ...groupTasks.slice(0, insertAt),
        { ...activeTask, priority: targetPriority },
        ...groupTasks.slice(insertAt),
      ];
    }

    // Optimistic UI update
    setTasks((prev) => {
      const withoutActive = prev.filter((t) => t.id !== activeId);
      const updatedActive = { ...activeTask, priority: targetPriority };
      // Place updated active task at position in the full list
      const otherPriorityTasks = withoutActive.filter((t) => t.priority !== targetPriority);
      const merged = [...otherPriorityTasks];
      // Insert the new group order inline, just append; visual sort is by priority
      return [...merged, ...newGroupOrder.map((t) => (t.id === activeId ? updatedActive : t))];
    });

    // Persist: position = index within the new group
    const newPosition = newGroupOrder.findIndex((t) => t.id === activeId);
    reorderTask(activeId, targetPriority, newPosition);
  };

  const handleNext = async () => {
    try {
      const res = await api.get<{ task?: Task; message?: string }>("/tasks/next");
      if (res.task) {
        setBanner(`Up next: ${res.task.title} (${res.task.priority})`);
      } else if (res.message) {
        setBanner(res.message);
      } else {
        setBanner("No suggestion available right now.");
      }
      setTimeout(() => setBanner(null), 5000);
    } catch {
      setBanner("Could not get a suggestion right now.");
      setTimeout(() => setBanner(null), 5000);
    }
  };

  const copyTaskList = () => {
    const text = filteredTasks
      .map((t) => `[${t.priority}] ${t.title} (${t.status})`)
      .join("\n");
    navigator.clipboard.writeText(text).catch(console.error);
  };

  const executeDeleteAll = async () => {
    setDeleteAllLoading(true);
    setDeleteAllConfirmOpen(false);

    // Build the filter body matching what is currently visible on screen.
    const body: {
      status: string;
      priority?: string[];
      labels?: string[];
    } = { status: "all" };

    if (statusFilter === "open") {
      body.status = "open";
    } else if (statusFilter === "closed") {
      body.status = "closed";
    }
    // "week" and "shelved" are client-side sub-filters of open; pass "open"
    // so the backend deletes only open tasks and let the count speak for itself.
    else if (statusFilter === "week" || statusFilter === "shelved") {
      body.status = "open";
    }

    // Optimistically clear the visible list immediately.
    setTasks((prev) => prev.filter((t) => !filteredTasks.some((ft) => ft.id === t.id)));

    try {
      await api.post("/tasks/delete-all", body);
    } catch (e) {
      console.error("Failed to delete all tasks:", e);
    } finally {
      setDeleteAllLoading(false);
      // Refetch to reconcile any divergence.
      fetchTasks();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      addTask();
    }
  };

  const isThisWeek = (dateStr: string) => {
    const d = new Date(dateStr);
    const now = new Date();
    const weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    return d >= weekAgo;
  };

  // Build a lookup map: label id -> Label
  const labelsById = new Map(labels.map((l) => [l.id, l]));

  // Build a lookup map: task id -> Task (for showing dependency titles)
  const tasksById = new Map(tasks.map((t) => [t.id, t]));

  // Filtering logic
  let filteredTasks = tasks;

  // Determine whether we're in a tri-state pill view (Open/InProgress/Closed)
  // or a legacy single-select view (all/shelved/week/recurring). Legacy
  // branches are triggered only when the user explicitly selects one of the
  // legacy statuses via code paths that still call setStatusFilter.
  const isLegacyView =
    statusFilter === "shelved" ||
    statusFilter === "week" ||
    statusFilter === "recurring";

  if (isLegacyView) {
    if (statusFilter === "shelved") {
      filteredTasks = filteredTasks.filter((t) => t.status === "shelved");
    } else if (statusFilter === "week") {
      filteredTasks = filteredTasks.filter(
        (t) => isActiveTask(t) && isThisWeek(t.created_at)
      );
    }
    // recurring branch is handled separately in the render; skip here.
  } else {
    // Single-select pill. Shelved tasks are a legacy bucket that the four
    // pills do not cover, so they are always hidden here.
    filteredTasks = filteredTasks.filter((t) => {
      if (t.status === "shelved") return false;
      if (selectedStatus === "all") return true;
      const effective: StatusPill = (runningAgentTaskIds.has(t.id) && t.status !== "closed")
        ? "in_progress"
        : t.status === "closed"
        ? "closed"
        : "open";
      return effective === selectedStatus;
    });
  }

  const onlyClosedSelected = !isLegacyView && selectedStatus === "closed";

  if (!isLegacyView && selectedStatus === "all") {
    filteredTasks = [...filteredTasks].sort((a, b) => {
      // 1. Status group: running agents first, closed last
      const statusPriority = (t: typeof a) => {
        if (runningAgentTaskIds.has(t.id) && t.status !== "closed") return 0;
        if (t.status === "closed") return 2;
        return 1;
      };
      const statusDiff = statusPriority(a) - statusPriority(b);
      if (statusDiff !== 0) return statusDiff;
      // 2. When a date-based sort is active, respect it across ALL statuses (→1066)
      if (sortBy === "date-desc") {
        return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
      }
      if (sortBy === "date-asc") {
        return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
      }
      // 3. Default: priority (P0 < P1 < P2 < P3 < none), then newest first
      const priorityOrder = (t: typeof a) => {
        const p = t.priority;
        if (p === "P0") return 0;
        if (p === "P1") return 1;
        if (p === "P2") return 2;
        if (p === "P3") return 3;
        return 4;
      };
      const priorityDiff = priorityOrder(a) - priorityOrder(b);
      if (priorityDiff !== 0) return priorityDiff;
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });
  } else if (onlyClosedSelected) {
    filteredTasks = [...filteredTasks].sort((a, b) => {
      const aTime = a.closed_at ? new Date(a.closed_at).getTime() : 0;
      const bTime = b.closed_at ? new Date(b.closed_at).getTime() : 0;
      return closedSortOrder === "oldest" ? aTime - bTime : bTime - aTime;
    });
  } else {
    // Apply the user-chosen sort order to non-closed views.
    filteredTasks = [...filteredTasks].sort((a, b) => {
      if (sortBy === "date-asc") {
        return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
      }
      if (sortBy === "date-desc") {
        const aRun = runningAgentTaskIds.has(a.id) && a.status !== "closed";
        const bRun = runningAgentTaskIds.has(b.id) && b.status !== "closed";
        if (aRun !== bRun) return aRun ? -1 : 1;
        return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
      }
      if (sortBy === "status") {
        // Open tasks before closed/shelved; within same status keep by date desc.
        const statusOrder: Record<string, number> = { in_progress: 0, open: 1, shelved: 2, closed: 3 };
        const aOrd = statusOrder[a.status] ?? 99;
        const bOrd = statusOrder[b.status] ?? 99;
        if (aOrd !== bOrd) return aOrd - bOrd;
        return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
      }
      if (sortBy === "label") {
        // Find the first label name for each task, alphabetically.
        const getLabelName = (task: Task) => {
          const id = (task.label_ids || [])[0];
          if (!id) return "\uFFFF"; // tasks without labels sort last
          return labels.find((l) => l.id === id)?.name ?? "\uFFFF";
        };
        const aName = getLabelName(a);
        const bName = getLabelName(b);
        if (aName !== bName) return aName.localeCompare(bName);
        return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
      }
      // Default: newest first
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });
  }

  // Render groups: flat sorted list for "all" view, priority-grouped for others.
  const taskGroups: { key: string; tasks: Task[] }[] =
    !isLegacyView && selectedStatus === "all"
      ? [{ key: "all-sorted", tasks: filteredTasks }]
      : PRIORITIES.reduce<{ key: string; tasks: Task[] }[]>((acc, p) => {
          const grouped = filteredTasks.filter((t) =>
            p === "P3" ? t.priority === "P3" || !t.priority : t.priority === p
          );
          if (grouped.length > 0) acc.push({ key: p, tasks: grouped });
          return acc;
        }, []);

  // Unfiltered counts used by the FilterDrawer status badge pills so the
  // user can see how many tasks live in each status bucket before choosing
  // one. These deliberately ignore priority/label/thread filters.
  const openCount = tasks.filter(
    (t) =>
      isActiveTask(t) &&
      !runningAgentTaskIds.has(t.id)
  ).length;
  const inProgressCount = tasks.filter(
    (t) => runningAgentTaskIds.has(t.id) && t.status !== "closed"
  ).length;
  const closedCount = tasks.filter((t) => t.status === "closed").length;
  const filterCounts: Partial<Record<StatusPill, number>> = {
    open: openCount,
    in_progress: inProgressCount,
    closed: closedCount,
  };

  // The footer counter must match what is VISIBLE in the list. filteredTasks
  // already has every active filter applied (status, priority, label, thread,
  // session hide). Use it as the source of truth for the displayed count.
  const visibleCount = filteredTasks.length;

  const filtersHidingAllTasks = false;

  const clearAllFilters = () => {
    setStatusFilter("open");
  };

  /** Render dependency pills showing what blocks/depends-on this task */
  const renderDependencyPills = (task: Task) => {
    const blocks = task.blocks || [];
    const dependsOn = task.depends_on || [];
    if (blocks.length === 0 && dependsOn.length === 0) return null;
    return (
      <div className="flex items-center gap-1 flex-shrink-0">
        {dependsOn.map((depId) => {
          const dep = tasksById.get(depId);
          return (
            <span
              key={`dep-${depId}`}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium cursor-pointer hover:opacity-80 bg-amber-500/15 text-amber-400 border border-amber-500/30"
              onClick={(e) => {
                e.stopPropagation();
                unlinkTask(task.id, "depends-on", depId);
              }}
              title={`Needs ${depId} done first. Click to remove.`}
            >
              <Icon name="block" className="text-[9px]" />
              {dep ? `${depId}` : depId}
              <Icon name="close" className="text-[9px]" />
            </span>
          );
        })}
        {blocks.map((blockId) => {
          const blocked = tasksById.get(blockId);
          return (
            <span
              key={`blk-${blockId}`}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium cursor-pointer hover:opacity-80 bg-blue-500/15 text-blue-400 border border-blue-500/30"
              onClick={(e) => {
                e.stopPropagation();
                unlinkTask(task.id, "blocks", blockId);
              }}
              title={`Blocks ${blockId}. Click to remove.`}
            >
              <Icon name="lock" className="text-[9px]" />
              {blocked ? `${blockId}` : blockId}
              <Icon name="close" className="text-[9px]" />
            </span>
          );
        })}
      </div>
    );
  };

  /** Render label pills for a task */
  const renderTaskLabels = (task: Task) => {
    const taskLabelIds = task.label_ids || [];
    const autoLabelIds = new Set(task.auto_label_ids || []);
    if (taskLabelIds.length === 0) return null;
    return (
      <div className="flex items-center gap-1 flex-shrink-0">
        {taskLabelIds.map((lid) => {
          const label = labelsById.get(lid);
          if (!label) return null;
          const isAuto = autoLabelIds.has(lid);
          return (
            <span
              key={lid}
              data-testid={isAuto ? `auto-label-${task.id}-${lid}` : undefined}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium cursor-pointer hover:opacity-80"
              style={{
                backgroundColor: label.color + "20",
                color: label.color,
                border: `1px solid ${label.color}40`,
              }}
              onClick={(e) => {
                e.stopPropagation();
                removeLabel(task.id, lid);
              }}
              title={
                isAuto
                  ? `Auto-applied. Click x to remove "${label.name}".`
                  : `Click to remove "${label.name}" label`
              }
            >
              {label.name}
              <Icon name="close" className="text-[9px]" />
            </span>
          );
        })}
      </div>
    );
  };

  /** Render a dropdown to assign labels to a task */
  const renderLabelDropdown = (task: Task) => {
    const taskLabelIds = task.label_ids || [];
    const availableLabels = labels.filter((l) => !taskLabelIds.includes(l.id));

    return (
      <div className="relative">
        <button
          onClick={(e) => {
            e.stopPropagation();
            setOpenLabelDropdown(openLabelDropdown === task.id ? null : task.id);
          }}
          className="p-1 text-slate-600 hover:text-slate-400 transition-colors"
          title="Add a label"
        >
          <Icon name="label" className="text-sm" />
        </button>
        {openLabelDropdown === task.id && (
          <div className="absolute right-0 top-full mt-1 z-50 bg-slate-800 border border-slate-700 rounded-lg shadow-xl py-1 min-w-[140px]">
            {availableLabels.length === 0 ? (
              <p className="px-3 py-2 text-xs text-slate-500">
                {labels.length === 0 ? "No labels created yet" : "All labels assigned"}
              </p>
            ) : (
              availableLabels.map((label) => (
                <button
                  key={label.id}
                  onClick={(e) => {
                    e.stopPropagation();
                    assignLabel(task.id, label.id);
                  }}
                  className="w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 hover:bg-slate-700 transition-colors"
                >
                  <span
                    className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                    style={{ backgroundColor: label.color }}
                  />
                  <span className="text-slate-300">{label.name}</span>
                </button>
              ))
            )}
          </div>
        )}
      </div>
    );
  };


  /** Render a dropdown to add a dependency link */
  const renderLinkDropdown = (task: Task) => {
    const otherTasks = tasks.filter(
      (t) =>
        t.id !== task.id &&
        !(task.blocks || []).includes(t.id) &&
        !(task.depends_on || []).includes(t.id)
    );

    return (
      <div className="relative">
        <button
          onClick={(e) => {
            e.stopPropagation();
            setOpenLinkDropdown(openLinkDropdown === task.id ? null : task.id);
            setLinkTarget("");
          }}
          className="p-1 text-slate-600 hover:text-slate-400 transition-colors"
          title="Add a dependency"
        >
          <Icon name="account_tree" className="text-sm" />
        </button>
        {openLinkDropdown === task.id && (
          <div
            className="absolute right-0 top-full mt-1 z-50 bg-slate-800 border border-slate-700 rounded-lg shadow-xl py-2 min-w-[240px]"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="px-3 pb-1.5 text-[10px] font-semibold text-slate-500 uppercase tracking-wide">
              Link to another task
            </p>
            <div className="px-3 pb-2">
              <input
                type="text"
                value={linkTarget}
                onChange={(e) => setLinkTarget(e.target.value)}
                placeholder="Type a task ID..."
                className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-slate-500"
                autoFocus
              />
            </div>
            {linkTarget.trim() && (
              <div className="border-t border-slate-700 pt-1">
                {(() => {
                  const targetId = linkTarget.trim().replace(/^#/, "");
                  const normalizedTarget = targetId.startsWith("→") ? targetId : `→${targetId.replace(/^0+/, "").padStart(3, "0")}`;
                  const targetTask = tasksById.get(normalizedTarget);
                  return (
                    <>
                      {targetTask && (
                        <p className="px-3 py-1 text-[10px] text-slate-500 truncate">
                          {normalizedTarget}: {targetTask.title}
                        </p>
                      )}
                      <button
                        onClick={() => linkTask(task.id, "blocks", normalizedTarget)}
                        className="w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 hover:bg-slate-700 transition-colors text-blue-400"
                      >
                        <Icon name="lock" className="text-sm" />
                        This task blocks {normalizedTarget}
                      </button>
                      <button
                        onClick={() => linkTask(task.id, "depends-on", normalizedTarget)}
                        className="w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 hover:bg-slate-700 transition-colors text-amber-400"
                      >
                        <Icon name="block" className="text-sm" />
                        This task needs {normalizedTarget} first
                      </button>
                    </>
                  );
                })()}
              </div>
            )}
            {!linkTarget.trim() && otherTasks.length > 0 && (
              <div className="max-h-32 overflow-y-auto">
                {otherTasks.slice(0, 8).map((t) => (
                  <button
                    key={t.id}
                    onClick={() => setLinkTarget(t.id)}
                    className="w-full text-left px-3 py-1 text-xs flex items-center gap-2 hover:bg-slate-700 transition-colors"
                  >
                    <span className="text-slate-500 font-mono">{t.id}</span>
                    <span className="text-slate-300 truncate">{t.title}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="min-h-dvh bg-slate-950 text-white">
      <TopBar title="Tasks" />

      <div data-tour="tasks" className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8 max-w-6xl mx-auto">
        {/* Banner */}
        {banner && banner.trim() && (
          <div className="mb-4 px-4 py-3 bg-purple-500/20 border border-purple-500/40 rounded-lg text-sm text-purple-200 flex items-center justify-between">
            <span>{banner}</span>
            <button onClick={() => setBanner(null)} className="text-purple-400 hover:text-white ml-4">
              <Icon name="close" className="text-base" />
            </button>
          </div>
        )}

        {/* Primary toolbar: one tight row */}
        <div className="flex items-center gap-2 mb-3 flex-wrap" data-testid="primary-toolbar">
          {/* Title + LIVE */}
          <h1 data-testid="page-header" className="text-xl sm:text-2xl font-bold">Tasks</h1>
          <span className="flex items-center gap-1.5 text-xs text-green-400 bg-green-500/10 px-2 py-0.5 rounded-full" data-testid="live-badge">
            <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
            LIVE
          </span>

          {/* View tabs */}
          <div className="flex items-center gap-3 text-sm ml-2 mr-auto">
            <button onClick={() => setActiveTab("tasks")} className={activeTab === "tasks" ? "text-blue-400 border-b-2 border-blue-400 pb-0.5 font-medium" : "text-slate-400 pb-0.5 hover:text-slate-300"}>Tasks</button>
            <button onClick={() => setActiveTab("labels")} className={activeTab === "labels" ? "text-blue-400 border-b-2 border-blue-400 pb-0.5 font-medium" : "text-slate-400 pb-0.5 hover:text-slate-300"}>Labels</button>
            <button onClick={() => setActiveTab("health")} className={activeTab === "health" ? "text-blue-400 border-b-2 border-blue-400 pb-0.5 font-medium" : "text-slate-400 pb-0.5 hover:text-slate-300"}>Health</button>
          </div>

          {/* Primary AI action */}
          <div className="flex items-center gap-1 shrink-0">
            <button
              onClick={() => setWavesModalOpen(true)}
              data-testid="plan-waves-btn"
              className="flex items-center gap-1.5 bg-slate-800 hover:bg-slate-700 text-sm px-3 py-1.5 rounded-lg border border-slate-700"
            >
              <Icon name="account_tree" className="text-purple-400 text-base" />
              <span className="hidden sm:inline">
                {Object.keys(waveAssignments).length > 0 ? "Update waves" : "Plan waves"}
              </span>
              <span className="sm:hidden">Waves</span>
            </button>
            <div className="relative group/wavesinfo">
              <button
                data-testid="plan-waves-info"
                className="p-1.5 text-slate-500 hover:text-slate-300 transition-colors"
                aria-label="What are waves?"
                tabIndex={0}
              >
                <Icon name="info" className="text-sm" />
              </button>
              <div className="absolute right-0 top-full mt-1 z-50 hidden group-hover/wavesinfo:block group-focus-within/wavesinfo:block w-72 bg-slate-800 border border-slate-700 rounded-xl shadow-xl p-3 text-xs text-slate-300 leading-relaxed pointer-events-none">
                <p className="font-semibold text-slate-100 mb-1">What are waves?</p>
                <p>Waves group your tasks into batches. Tasks in the same wave can run at the same time. Each wave waits for the one before it to finish — so you always know what to start next.</p>
              </div>
            </div>
          </div>

          {/* Overflow menu */}
          <div className="relative" ref={overflowMenuRef}>
            <button
              onClick={() => setShowOverflowMenu((v) => !v)}
              data-testid="overflow-menu-trigger"
              className="p-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg border border-slate-700 text-slate-400"
              title="More actions"
            >
              <Icon name="more_horiz" className="text-base" />
            </button>
            {showOverflowMenu && (
              <div
                data-testid="overflow-menu"
                className="absolute right-0 top-full mt-1 z-50 bg-slate-800 border border-slate-700 rounded-xl shadow-xl py-1 min-w-[180px]"
                onClick={(e) => e.stopPropagation()}
              >
                <button
                  data-testid="label-all-btn"
                  onClick={() => { setShowOverflowMenu(false); labelAllTasks(); }}
                  disabled={labelAllLoading}
                  className="w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-slate-700 transition-colors text-slate-300 disabled:opacity-50"
                >
                  {labelAllLoading ? <Icon name="hourglass_empty" className="text-purple-400 text-sm animate-spin" /> : <Icon name="label" className="text-purple-400 text-sm" />}
                  {labelAllLoading ? "Labeling..." : "Label all"}
                </button>
                <button
                  onClick={() => { setShowOverflowMenu(false); setImportModalOpen(true); setImportResult(null); setImportFields({}); }}
                  className="w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-slate-700 transition-colors text-slate-300"
                  data-testid="overflow-import"
                >
                  <Icon name="download" className="text-slate-400 text-sm" />
                  Import
                </button>
                <button
                  onClick={() => { setShowOverflowMenu(false); setShowTaskSharePopover((v) => !v); }}
                  className="w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-slate-700 transition-colors text-slate-300"
                  data-testid="overflow-share"
                >
                  <Icon name="share" className="text-slate-400 text-sm" />
                  Share
                </button>
                <ExportButton
                    contentLabel="tasks"
                    buildUrl={(format) => {
                      const exportStatus = statusFilter === "closed" ? "closed" : "open";
                      return `/api/export/tasks?format=${format}&status=${exportStatus}`;
                    }}
                  />
                <button
                  onClick={() => { setShowOverflowMenu(false); copyTaskList(); }}
                  className="w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-slate-700 transition-colors text-slate-300"
                  data-testid="overflow-copy"
                >
                  <Icon name="content_copy" className="text-slate-400 text-sm" />
                  Copy list
                </button>
                <div className="border-t border-slate-700 my-1" />
                <button
                  onClick={() => { setShowOverflowMenu(false); setAuditModalOpen(true); }}
                  data-testid="tasks-audit-button"
                  className="w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-slate-700 transition-colors text-blue-300"
                >
                  <Icon name="fact_check" className="text-blue-400 text-sm" />
                  Audit for review
                </button>
                <div className="border-t border-slate-700 my-1" />
                <button
                  onClick={() => { setShowOverflowMenu(false); setDeleteAllConfirmOpen(true); }}
                  data-testid="overflow-delete-all"
                  disabled={deleteAllLoading || filteredTasks.length === 0}
                  className="w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-red-900/40 transition-colors text-red-400 disabled:opacity-40"
                >
                  <Icon name="delete_sweep" className="text-red-400 text-sm" />
                  {deleteAllLoading ? "Deleting..." : "Delete all"}
                </button>
              </div>
            )}
            {showTaskSharePopover && (
              <SharePopover
                shareType="task_list"
                contentIds={filteredTasks.map((t) => t.id)}
                title={statusFilter === "open" ? "Open tasks" : statusFilter === "all" ? "All tasks" : statusFilter === "closed" ? "Closed tasks" : statusFilter === "shelved" ? "Paused tasks" : "Tasks this week"}
                onClose={() => setShowTaskSharePopover(false)}
              />
            )}
          </div>
        </div>

        {/* Label all result toast */}
        {labelAllResult && (
          <div className="mb-2 text-xs text-purple-300 bg-purple-500/10 px-3 py-1.5 rounded-md border border-purple-500/30 inline-block">
            {labelAllResult}
          </div>
        )}

        {activeTab === "health" ? (
          <HealthCheckView />
        ) : activeTab === "labels" ? (
          <LabelsView
            onFilterByLabel={(id) => {
              if (id) setActiveTab("tasks");
            }}
            activeLabelId={null}
            onLabelsChanged={() => {
              fetchLabels();
              fetchTasks();
            }}
          />
        ) : (
          <>
            {/* Quick add input */}
            <div className="flex items-center gap-2 sm:gap-3 mb-4 sm:mb-6">
              <div className="flex-1 relative">
                <input
                  ref={inputRef}
                  type="text"
                  value={newTaskTitle}
                  onChange={(e) => setNewTaskTitle(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="What needs to be done?"
                  className="w-full bg-slate-900/60 border border-slate-800 rounded-lg px-4 py-2.5 text-sm text-slate-300 placeholder-slate-600 focus:outline-none focus:border-slate-600"
                />
              </div>
              <button
                onClick={addTask}
                className="w-11 h-11 sm:w-9 sm:h-9 flex items-center justify-center bg-blue-500 hover:bg-blue-600 rounded-full text-white shrink-0"
              >
                <Icon name="add" className="text-lg" />
              </button>
            </div>

            {/* Always-visible filter panel */}
            <FilterDrawer
              open={true}
              selectedStatus={selectedStatus}
              filterCounts={filterCounts}
              sortBy={sortBy}
              onStatusChange={(s) => {
                setSelectedStatus(s);
                // Leaving a legacy view snaps statusFilter back to "all"
                // so legacy branches become no-ops.
                setStatusFilter("all");
              }}
              onSortByChange={setSortBy}
            />

            {/* Bulk action bar */}
            {selectedTaskIds.size > 0 && (
              <div className="flex items-center gap-3 mb-4 px-4 py-2.5 bg-blue-500/10 border border-blue-500/30 rounded-lg">
                <span className="text-sm text-blue-300 font-medium">
                  {selectedTaskIds.size} selected
                </span>
                <div className="flex items-center gap-2 ml-auto">
                  <button
                    onClick={() => bulkAction("plan")}
                    disabled={actionLoading === "bulk"}
                    className="flex items-center gap-1.5 px-3 py-1 bg-purple-500/20 hover:bg-purple-500/30 text-purple-300 text-xs rounded-lg border border-purple-500/30 disabled:opacity-50"
                  >
                    <Icon name="description" className="text-sm" />
                    Plan all
                  </button>
                  <button
                    onClick={() => bulkAction("implement")}
                    disabled={actionLoading === "bulk"}
                    className="flex items-center gap-1.5 px-3 py-1 bg-green-500/20 hover:bg-green-500/30 text-green-300 text-xs rounded-lg border border-green-500/30 disabled:opacity-50"
                  >
                    <Icon name="code" className="text-sm" />
                    Implement all
                  </button>
                  <button
                    onClick={() => setSelectedTaskIds(new Set())}
                    className="p-1 text-slate-400 hover:text-slate-200"
                    title="Clear selection"
                  >
                    <Icon name="close" className="text-sm" />
                  </button>
                </div>
              </div>
            )}

            {/* Recurring tasks tab */}
            {statusFilter === "recurring" && (
              <RecurringTasksSection showSuggestions />
            )}

            {/* Task list */}
            {statusFilter !== "recurring" && <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragStart={handleDragStart}
              onDragEnd={handleDragEnd}
            >
            <div className="flex flex-col gap-2">
              {loading && tasks.length === 0 && (
                <LoadingState variant="skeleton-list" />
              )}
              {!loading && filteredTasks.length === 0 && tasks.length === 0 && (
                <EmptyState
                  icon="checklist"
                  title="No tasks yet."
                  description="Type a task above, or tell myOS an idea in chat and it will create tasks for you."
                />
              )}
              {!loading && filteredTasks.length === 0 && tasks.length > 0 && (
                <p className="text-sm text-slate-500 py-4">No tasks match this filter.</p>
              )}
              {taskGroups.map(({ key: priority, tasks: groupTasks }) => (
                  <SortableContext
                    key={priority}
                    items={groupTasks.map((t) => t.id)}
                    strategy={verticalListSortingStrategy}
                  >
                    {groupTasks.map((task) => (
              <SortableTaskWrapper key={task.id} taskId={task.id}>
                {(dragHandleProps) => (
                <div>
                  <div
                    ref={(el) => { taskRowRefs.current[task.id] = el; }}
                    data-testid={`task-row-${task.id}`}
                    data-in-progress={
                      (runningAgentTaskIds.has(task.id) && task.status !== "closed") ? "true" : undefined
                    }
                    onClick={() => handleTaskClick(task.id)}
                    className={`group ${
                      (runningAgentTaskIds.has(task.id) && task.status !== "closed")
                        ? "ring-1 ring-blue-400/60 "
                        : ""
                    }bg-slate-900/60 border rounded-lg px-4 py-3 flex items-center gap-3 cursor-pointer transition-colors ${
                      selectedTaskId === task.id
                        ? "border-blue-500/60 bg-blue-500/5"
                        : "border-slate-800 hover:border-slate-700"
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={selectedTaskIds.has(task.id)}
                      onClick={(e) => e.stopPropagation()}
                      onChange={() => toggleTaskSelection(task.id)}
                      className="w-3.5 h-3.5 rounded border-slate-600 bg-slate-800 text-blue-500 focus:ring-0 flex-shrink-0 cursor-pointer"
                      aria-label={`select ${task.title}`}
                    />
                    {runningAgentTaskIds.has(task.id) && task.status !== "closed" && (
                      <span
                        data-testid={`task-in-progress-indicator-${task.id}`}
                        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-blue-500/20 text-blue-300 border border-blue-500/40"
                        title="Agent is working on this task"
                      >
                        <span className="w-1.5 h-1.5 rounded-full bg-blue-300 animate-pulse" />
                        In progress
                      </span>
                    )}
                    <span
                      {...dragHandleProps}
                      onClick={(e) => e.stopPropagation()}
                      className="text-slate-700 text-lg cursor-grab touch-none select-none"
                      title="Drag to reorder"
                    >
                      <Icon name="drag_indicator" className="text-lg" />
                    </span>
                    <button
                      onClick={(e) => { e.stopPropagation(); task.status === "closed" ? reopenTask(task.id) : closeTask(task.id); }}
                      title={task.status === "closed" ? "Reopen task" : "Close task"}
                      className={`w-5 h-5 rounded-full border-2 flex-shrink-0 flex items-center justify-center transition-colors ${
                        task.status === "closed"
                          ? "border-green-500 bg-green-500/20 hover:border-amber-400 hover:bg-amber-500/20"
                          : "border-slate-600 hover:border-slate-400"
                      }`}
                    >
                      {task.status === "closed" && (
                        <Icon name="check" className="text-green-400 text-xs" />
                      )}
                    </button>
                    <span className="text-slate-500 text-sm font-mono">
                      #{task.id}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        {editingTaskId === task.id ? (
                          <input
                            data-testid={`title-edit-input-${task.id}`}
                            autoFocus
                            value={editingTitleDraft}
                            onChange={(e) => setEditingTitleDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                e.preventDefault();
                                titleEditCancelledRef.current = false;
                                updateTaskTitle(task.id, editingTitleDraft);
                              } else if (e.key === "Escape") {
                                titleEditCancelledRef.current = true;
                                setEditingTaskId(null);
                              }
                            }}
                            onBlur={() => {
                              if (!titleEditCancelledRef.current) {
                                updateTaskTitle(task.id, editingTitleDraft);
                              }
                              titleEditCancelledRef.current = false;
                            }}
                            onClick={(e) => e.stopPropagation()}
                            className="text-sm bg-slate-800 border border-blue-500 rounded px-1.5 py-0.5 w-full focus:outline-none text-slate-200 min-w-0"
                          />
                        ) : (
                          <>
                            <span
                              data-testid={`task-title-${task.id}`}
                              className={`text-sm line-clamp-2 cursor-text ${task.status === "closed" ? "line-through text-slate-500" : ""} ${task.status === "shelved" ? "text-slate-500" : ""}`}
                              title={task.title}
                              onClick={(e) => {
                                e.stopPropagation();
                                titleEditCancelledRef.current = false;
                                setEditingTaskId(task.id);
                                setEditingTitleDraft(task.title);
                              }}
                            >
                              {task.title}
                            </span>
                            <button
                              data-testid={`edit-title-btn-${task.id}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                titleEditCancelledRef.current = false;
                                setEditingTaskId(task.id);
                                setEditingTitleDraft(task.title);
                              }}
                              className="opacity-0 group-hover:opacity-100 text-slate-500 hover:text-slate-300 transition-opacity flex-shrink-0"
                              title="Edit title"
                              aria-label={`edit title of ${task.title}`}
                            >
                              <Icon name="edit" className="text-xs" />
                            </button>
                          </>
                        )}
                        {task.status === "shelved" && (
                          <span
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-500/15 text-slate-400 border border-slate-500/30"
                          >
                            <Icon name="pause" className="text-[10px]" />
                            Paused
                          </span>
                        )}
                        {runningAgentTaskIds.has(task.id) && task.status !== "closed" && (() => {
                          const buildState = buildStateByTaskId.get(task.id);
                          if (buildState) {
                            return (
                              <ComprehensiveBuildPill
                                taskId={task.id}
                                buildState={buildState}
                              />
                            );
                          }
                          return null;
                        })()}
                        {task.status === "closed" && task.closed_reason === "completed" && (
                          <span
                            data-testid={`closed-badge-${task.id}`}
                            className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-green-500/15 text-green-400 border border-green-500/30"
                          >
                            Done
                          </span>
                        )}
                        {task.status === "closed" && task.closed_reason === "duplicate" && (
                          <span
                            data-testid={`closed-badge-${task.id}`}
                            className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-500/15 text-slate-400 border border-slate-500/30"
                          >
                            Duplicate
                          </span>
                        )}
                        {task.status === "closed" && task.closed_reason === "archived" && (
                          <span
                            data-testid={`closed-badge-${task.id}`}
                            className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/15 text-amber-400 border border-amber-500/30"
                          >
                            Archived
                          </span>
                        )}
                        {task.status === "closed" && task.closed_at && (
                          <span
                            data-testid={`closed-at-${task.id}`}
                            className="text-[10px] text-slate-500"
                          >
                            {new Date(task.closed_at).toLocaleDateString([], { month: 'short', day: 'numeric' })}
                          </span>
                        )}
                        {isStale(task) && (
                          <span className="inline-flex items-center gap-1 text-[11px] text-slate-500">
                            <Icon name="schedule" className="text-[11px] text-slate-500" />
                            stale
                          </span>
                        )}
                        {waveAssignments[task.id] != null && (
                          <span
                            data-testid={`wave-badge-${task.id}`}
                            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-500/15 text-purple-400 border border-purple-500/25"
                          >
                            W{waveAssignments[task.id]}
                          </span>
                        )}
                        {renderDependencyPills(task)}
                        {renderTaskLabels(task)}
                      </div>
                      {task.description && (
                        <p
                          data-testid={`task-description-${task.id}`}
                          className="text-xs text-slate-500 dark:text-slate-400 truncate mt-0.5"
                          title={task.description}
                        >
                          {getFirstSentence(task.description)}
                        </p>
                      )}
                      {task.notes && (
                        <p
                          data-testid={`task-notes-${task.id}`}
                          className="text-xs text-amber-400/70 truncate mt-0.5"
                          title={task.notes}
                        >
                          <span className="font-medium">Note:</span> {task.notes}
                        </p>
                      )}

                    </div>
                    {renderLinkDropdown(task)}
                    {renderLabelDropdown(task)}
                    {(task.label_ids || []).length === 0 && (
                      <button
                        data-testid={`auto-label-btn-${task.id}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          autoLabelTask(task.id);
                        }}
                        disabled={autoLabelingTaskId === task.id}
                        className="p-1 text-slate-600 hover:text-purple-400 disabled:opacity-50 transition-colors"
                        title="Auto-label this task"
                      >
                        {autoLabelingTaskId === task.id ? (
                          <Icon name="hourglass_empty" className="text-sm animate-spin" />
                        ) : (
                          <Icon name="auto_awesome" className="text-sm" />
                        )}
                      </button>
                    )}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        deleteTask(task.id);
                      }}
                      className="p-1 text-slate-700 hover:text-red-400 transition-colors"
                      title="Delete task permanently"
                    >
                      <Icon name="delete" className="text-sm" />
                    </button>
                    {/* Action menu (plan / implement / break down) */}
                    <div className="relative">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenActionMenu(openActionMenu === task.id ? null : task.id);
                        }}
                        className="p-1 text-slate-600 hover:text-slate-400 transition-colors"
                        title="Actions"
                      >
                        <Icon name="more_vert" className="text-sm" />
                      </button>
                      {openActionMenu === task.id && (
                        <div
                          className="absolute right-0 top-full mt-1 z-50 bg-slate-800 border border-slate-700 rounded-lg shadow-xl py-1 min-w-[180px]"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <button
                            onClick={() => spawnAgentForTask(task.id, "plan")}
                            disabled={actionLoading === task.id}
                            className="w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 hover:bg-slate-700 transition-colors text-slate-300"
                          >
                            <Icon name="description" className="text-sm text-purple-400" />
                            Plan
                          </button>
                          {/* Comprehensive build (default) plus quick escape hatch. */}
                          <div className="flex items-center w-full hover:bg-slate-700 transition-colors">
                            <button
                              onClick={() => spawnAgentForTask(task.id, "comprehensive")}
                              disabled={actionLoading === task.id}
                              className="flex-1 text-left px-3 py-1.5 text-xs flex items-center gap-2 text-slate-300"
                            >
                              <Icon name="code" className="text-sm text-green-400" />
                              Comprehensive build
                            </button>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                setOpenBuildHelp(openBuildHelp === task.id ? null : task.id);
                              }}
                              className="px-2 py-1.5 text-slate-500 hover:text-slate-200"
                              title="What does this do?"
                              aria-label="What does this do?"
                            >
                              <Icon name="help_outline" className="text-sm" />
                            </button>
                          </div>
                          <button
                            onClick={() => spawnAgentForTask(task.id, "quick")}
                            disabled={actionLoading === task.id}
                            className="w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 hover:bg-slate-700 transition-colors text-slate-300"
                          >
                            <Icon name="bolt" className="text-sm text-yellow-400" />
                            Quick build
                          </button>
                          {openBuildHelp === task.id && (
                            <div
                              ref={buildHelpRef}
                              role="dialog"
                              aria-label="What does this do?"
                              className="absolute right-full top-0 mr-2 w-72 bg-slate-900 border border-slate-700 rounded-xl shadow-xl z-50 p-4 text-xs text-slate-300 space-y-2"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <div className="font-semibold text-slate-100 text-sm">What does this do?</div>
                              <p>Comprehensive build spawns an agent that works the way you would want it to:</p>
                              <ol className="list-decimal list-inside space-y-0.5">
                                <li>Loads workspace context.</li>
                                <li>Reads the task and plans the approach.</li>
                                <li>Builds the solution.</li>
                                <li>Writes tests.</li>
                                <li>Runs the tests and fixes anything red.</li>
                                <li>Runs pytest and tsc to catch regressions.</li>
                                <li>Only reports done when everything is green.</li>
                              </ol>
                              <p>
                                Use "Comprehensive build" when you want it done right.
                                Use "Quick build" for a fast draft without tests or gates.
                              </p>
                            </div>
                          )}
                          <button
                            onClick={async () => {
                              setOpenActionMenu(null);
                              try {
                                await api.post('/specs/from-task', { task_id: task.id });
                                // Navigate to specs page to see the new draft
                                navigate('/specs');
                              } catch {
                                // silently fail if specs endpoint isn't ready
                              }
                            }}
                            className="w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 hover:bg-slate-700 transition-colors text-slate-300"
                          >
                            <Icon name="article" className="text-sm text-blue-400" />
                            Create Spec
                          </button>
                          <div className="border-t border-slate-700 my-1" />
                          {task.status === "shelved" ? (
                            <button
                              data-testid="task-action-resume"
                              onClick={() => unshelveTask(task.id)}
                              className="w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 hover:bg-slate-700 transition-colors text-slate-300"
                            >
                              <Icon name="play_arrow" className="text-sm text-green-400" />
                              Resume
                            </button>
                          ) : task.status === "in_progress" ? (
                            <button
                              data-testid="task-action-pause"
                              onClick={() => shelveTask(task.id)}
                              className="w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 hover:bg-slate-700 transition-colors text-slate-300"
                            >
                              <Icon name="pause" className="text-sm text-slate-400" />
                              Pause
                            </button>
                          ) : null}
                          {actionLoading === task.id && (
                            <div className="px-3 py-1 flex items-center gap-2 text-xs text-slate-500">
                              <Icon name="hourglass_empty" className="text-sm animate-spin" />
                              Working...
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="relative">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenPriorityDropdown(
                            openPriorityDropdown === task.id ? null : task.id
                          );
                        }}
                        className={`text-xs font-medium px-2 py-0.5 rounded cursor-pointer hover:ring-1 hover:ring-white/30 transition-all ${priorityStyles[task.priority] ?? "bg-slate-500/20 text-slate-400"}`}
                        title="Change priority"
                      >
                        {task.priority}
                      </button>
                      {task.unblocks && task.unblocks > 0 && (
                        <span
                          className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 border border-amber-500/30"
                          title={`Completing this task unblocks ${task.unblocks} other tasks`}
                        >
                          unblocks {task.unblocks}
                        </span>
                      )}
                      {openPriorityDropdown === task.id && (
                        <div className="absolute right-0 top-full mt-1 z-50 bg-slate-800 border border-slate-700 rounded-lg shadow-xl py-1 min-w-[80px]">
                          {PRIORITIES.map((p) => (
                            <button
                              key={p}
                              onClick={(e) => {
                                e.stopPropagation();
                                updatePriority(task.id, p);
                              }}
                              className={`w-full text-left px-3 py-1.5 text-xs font-medium flex items-center gap-2 hover:bg-slate-700 transition-colors ${
                                task.priority === p ? "opacity-50" : ""
                              }`}
                            >
                              <span className={`w-2 h-2 rounded-full ${priorityDotColors[p]}`} />
                              <span className={priorityStyles[p]}>{p}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Detail panel (Context + Changelog tabs) */}
                  {selectedTaskId === task.id && (
                    <div data-testid="briefing-panel" className="ml-0 sm:ml-8 mt-1 mb-2 bg-slate-50 dark:bg-slate-900/80 border border-slate-200 dark:border-blue-500/30 rounded-lg p-3 sm:p-4 text-sm text-slate-700 dark:text-slate-300">
                      {/* Action buttons */}
                      <div className="flex items-center gap-2 mb-3">
                        <button
                          onClick={() => spawnAgentForTask(task.id, "comprehensive")}
                          disabled={actionLoading === task.id}
                          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-green-500/20 text-green-400 hover:bg-green-500/30 transition-colors disabled:opacity-50"
                        >
                          <Icon name="code" className="text-sm" />
                          Comprehensive build
                        </button>
                        <button
                          onClick={() => spawnAgentForTask(task.id, "plan")}
                          disabled={actionLoading === task.id}
                          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-purple-500/20 text-purple-400 hover:bg-purple-500/30 transition-colors disabled:opacity-50"
                        >
                          <Icon name="description" className="text-sm" />
                          Plan
                        </button>
                        <button
                          onClick={() => spawnAgentForTask(task.id, "quick")}
                          disabled={actionLoading === task.id}
                          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-yellow-500/20 text-yellow-400 hover:bg-yellow-500/30 transition-colors disabled:opacity-50"
                        >
                          <Icon name="bolt" className="text-sm" />
                          Quick build
                        </button>
                      </div>
                      {/* Tab bar */}
                      <div className="flex items-center gap-4 mb-3 border-b border-slate-800 pb-2">
                        <button
                          onClick={() => setDetailTab("context")}
                          className={`text-xs font-medium pb-1 ${
                            detailTab === "context"
                              ? "text-blue-400 border-b-2 border-blue-400"
                              : "text-slate-500 hover:text-slate-300"
                          }`}
                        >
                          Context
                        </button>
                        <button
                          data-testid="changelog-tab"
                          onClick={() => setDetailTab("history")}
                          className={`text-xs font-medium pb-1 ${
                            detailTab === "history"
                              ? "text-blue-400 border-b-2 border-blue-400"
                              : "text-slate-500 hover:text-slate-300"
                          }`}
                        >
                          Changelog
                        </button>
                        <button
                          data-testid="related-tab"
                          onClick={() => setDetailTab("related")}
                          className={`text-xs font-medium pb-1 ${
                            detailTab === "related"
                              ? "text-blue-400 border-b-2 border-blue-400"
                              : "text-slate-500 hover:text-slate-300"
                          }`}
                        >
                          Related
                        </button>
                      </div>

                      {/* Context tab (briefing) */}
                      {detailTab === "context" && (
                        <>
                          {briefingLoading && (
                            <div className="flex items-center gap-2 text-slate-400">
                              <Icon name="hourglass_empty" className="text-base animate-spin" />
                              Loading context...
                            </div>
                          )}
                          {!briefingLoading && !briefing && (
                            <div className="space-y-3">
                              {task.description && (
                                <div data-testid={`task-summary-${task.id}`}>
                                  <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1">Summary</h4>
                                  <p className="text-slate-700 dark:text-slate-300 whitespace-pre-wrap">{task.description}</p>
                                </div>
                              )}
                              {!task.description && (
                                <p className="text-slate-500">No briefing available for this task.</p>
                              )}
                            </div>
                          )}
                          {!briefingLoading && briefing && (
                            <div className="space-y-3">
                              {task.description && (
                                <div data-testid={`task-summary-${task.id}`}>
                                  <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1">Summary</h4>
                                  <p className="text-slate-700 dark:text-slate-300 whitespace-pre-wrap">{task.description}</p>
                                </div>
                              )}
                              {briefing.blocked_by.length > 0 && (
                                <div>
                                  <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1">Waiting on</h4>
                                  <div className="space-y-2">
                                    {briefing.blocked_by.map((b, i) => {
                                      const blockerTask = b.blocker_task ?? null;
                                      const title = blockerTask?.title ?? b.text;
                                      const description = (blockerTask?.description ?? "").trim();
                                      const priority = blockerTask?.priority ?? "";
                                      const status = blockerTask?.status ?? "";
                                      const blockerId = b.blocker_id ?? "";
                                      const idLabel = blockerId ? `\u2192${blockerId}` : "";
                                      const priorityClass = priority && priorityStyles[priority] ? priorityStyles[priority] : "bg-slate-700 text-slate-300";
                                      const statusLabel = status === "open" ? "Open" : status === "closed" ? "Closed" : status;
                                      const isClickable = Boolean(blockerId);
                                      return (
                                        <div
                                          key={i}
                                          data-testid={`blocker-card-${i}`}
                                          onClick={() => {
                                            if (isClickable) handleTaskClick(`\u2192${blockerId}`);
                                          }}
                                          className={`rounded-md border p-2 ${
                                            b.resolved
                                              ? "border-green-500/30 bg-green-500/5 opacity-60"
                                              : "border-amber-500/30 bg-amber-500/5"
                                          } ${isClickable ? "cursor-pointer hover:border-amber-400" : ""}`}
                                        >
                                          <div className="flex items-center gap-2 text-xs">
                                            <Icon
                                              name={b.resolved ? "check_circle" : "block"}
                                              className={`text-sm ${b.resolved ? "text-green-400" : "text-amber-400"}`}
                                            />
                                            {idLabel && (
                                              <span className="font-mono text-slate-400">Blocked by {idLabel}</span>
                                            )}
                                            {priority && (
                                              <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${priorityClass}`}>
                                                {priority}
                                              </span>
                                            )}
                                            {statusLabel && (
                                              <span className="text-slate-400">{statusLabel}</span>
                                            )}
                                          </div>
                                          <div className={`mt-1 text-sm font-medium ${b.resolved ? "text-slate-500 line-through" : "text-slate-700 dark:text-slate-200"}`}>
                                            {title}
                                          </div>
                                          {description && (
                                            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400 line-clamp-2">
                                              {description}
                                            </p>
                                          )}
                                          {b.explanation && !b.resolved && (
                                            <p className="mt-2 text-xs italic text-slate-600 dark:text-slate-300">
                                              {b.explanation}
                                            </p>
                                          )}
                                        </div>
                                      );
                                    })}
                                  </div>
                                  {briefing.all_blockers_resolved && (
                                    <p className="mt-2 text-green-400 text-xs flex items-center gap-1">
                                      <Icon name="check_circle" className="text-sm" />
                                      All blockers resolved. Ready to go.
                                    </p>
                                  )}
                                </div>
                              )}

                              {briefing.unblocks.length > 0 && (
                                <div>
                                  <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1">Finishing this unblocks</h4>
                                  <ul className="space-y-1">
                                    {briefing.unblocks.map((u, i) => (
                                      <li key={i} className="flex items-center gap-2 text-slate-300">
                                        <Icon name="lock_open" className="text-sm text-blue-400" />
                                        {u}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}

                              {briefing.neighbors.length > 0 && (
                                <div>
                                  <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1">Related tasks</h4>
                                  <ul className="space-y-1">
                                    {briefing.neighbors.map((n, i) => (
                                      <li key={i} className="flex items-center gap-2 text-slate-300">
                                        <Icon name="link" className="text-sm text-slate-500" />
                                        {n}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}

                              {briefing.blocked_by.length === 0 && briefing.unblocks.length === 0 && briefing.neighbors.length === 0 && (
                                <p className="text-slate-500">This task is standalone. No blockers, no dependencies, no related tasks.</p>
                              )}
                            </div>
                          )}
                        </>
                      )}

                      {/* Plan attachment link */}
                      {detailTab === "context" && task.plan_path && (
                        <div className="mt-3 flex items-center gap-2 text-xs">
                          <Icon name="task_alt" className="text-blue-400 text-sm" />
                          <a
                            data-testid={`task-plan-${task.id}`}
                            href={`/specs?highlight=${encodeURIComponent(task.plan_path)}`}
                            onClick={e => {
                              e.preventDefault();
                              navigate(`/specs?highlight=${encodeURIComponent(task.plan_path!)}`);
                            }}
                            className="text-blue-400 hover:text-blue-300 underline underline-offset-2"
                          >
                            Plan attached
                          </a>
                        </div>
                      )}

                      {/* Notes editor — shown at the bottom of the context tab */}
                      {detailTab === "context" && (
                        <div className="mt-4 border-t border-slate-700 pt-4">
                          <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">Notes</h4>
                          <textarea
                            data-testid="task-notes-editor"
                            className="w-full bg-slate-800 border border-slate-600 rounded px-3 py-2 text-sm text-slate-200 placeholder-slate-500 resize-none focus:outline-none focus:ring-1 focus:ring-blue-500"
                            rows={4}
                            placeholder="Add a note..."
                            value={notesValue}
                            onChange={e => { setNotesValue(e.target.value); setNotesSaved(false); }}
                          />
                          <div className="flex items-center gap-2 mt-1">
                            <button
                              data-testid="task-notes-save"
                              className="text-xs px-3 py-1 rounded bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50"
                              disabled={notesSaving}
                              onClick={async () => {
                                if (!task) return;
                                setNotesSaving(true);
                                try {
                                  await api.patch(`/tasks/${task.id}`, { notes: notesValue });
                                  setTasks(prev => prev.map(t => t.id === task.id ? { ...t, notes: notesValue } : t));
                                  setNotesSaved(true);
                                } finally {
                                  setNotesSaving(false);
                                }
                              }}
                            >
                              {notesSaving ? "Saving…" : "Save"}
                            </button>
                            {notesSaved && <span className="text-xs text-green-400">Saved</span>}
                          </div>
                        </div>
                      )}

                      {/* Changelog tab (trace / attribution chain) */}
                      {detailTab === "history" && (
                        <div data-testid="trace-panel">
                          {traceLoading && (
                            <div className="flex items-center gap-2 text-slate-400">
                              <Icon name="hourglass_empty" className="text-base animate-spin" />
                              Loading history...
                            </div>
                          )}
                          {!traceLoading && !trace && (
                            <p className="text-slate-500">No history available for this task.</p>
                          )}
                          {!traceLoading && trace && (
                            <div className="space-y-3">
                              {trace.specs.length > 0 && (
                                <div>
                                  <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1">Specs</h4>
                                  <ul className="space-y-1">
                                    {trace.specs.map((s, i) => (
                                      <li key={i} className="flex items-center gap-2 text-slate-300">
                                        <Icon name="description" className="text-sm text-purple-400" />
                                        {s}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}

                              {trace.drafts.length > 0 && (
                                <div>
                                  <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1">Drafts</h4>
                                  <ul className="space-y-1">
                                    {trace.drafts.map((d, i) => (
                                      <li key={i} className="flex items-center gap-2 text-slate-300">
                                        <Icon name="edit_note" className="text-sm text-amber-400" />
                                        {d}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}

                              {trace.agentfiles.length > 0 && (
                                <div>
                                  <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1">Agent work</h4>
                                  <ul className="space-y-1">
                                    {trace.agentfiles.map((a, i) => (
                                      <li key={i} className="flex items-center gap-2 text-slate-300">
                                        <Icon name="smart_toy" className="text-sm text-cyan-400" />
                                        {a}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}

                              {trace.depends_on.length > 0 && (
                                <div>
                                  <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1">Depends on</h4>
                                  <ul className="space-y-1">
                                    {trace.depends_on.map((dep, i) => (
                                      <li key={i} className="flex items-center gap-2 text-slate-300">
                                        <Icon name="arrow_back" className="text-sm text-orange-400" />
                                        {dep}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}

                              {trace.blocks.length > 0 && (
                                <div>
                                  <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1">Blocks</h4>
                                  <ul className="space-y-1">
                                    {trace.blocks.map((b, i) => (
                                      <li key={i} className="flex items-center gap-2 text-slate-300">
                                        <Icon name="arrow_forward" className="text-sm text-blue-400" />
                                        {b}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}

                              {trace.commits.length > 0 && (
                                <div>
                                  <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1">Commits</h4>
                                  <ul className="space-y-1">
                                    {trace.commits.map((c, i) => (
                                      <li key={i} className="flex items-center gap-2 text-slate-300 font-mono text-xs">
                                        <Icon name="commit" className="text-sm text-green-400" />
                                        {c}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}

                              {trace.specs.length === 0 && trace.drafts.length === 0 && trace.agentfiles.length === 0 && trace.depends_on.length === 0 && trace.blocks.length === 0 && trace.commits.length === 0 && (
                                <p className="text-slate-500">No history yet. Specs, drafts, commits, and connections will appear here as work happens.</p>
                              )}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Related tab (cross-integration context) */}
                      {detailTab === "related" && (
                        <div data-testid="related-panel">
                          {linkedLoading && (
                            <div className="flex items-center gap-2 text-slate-400">
                              <Icon name="hourglass_empty" className="text-base animate-spin" />
                              Finding related items...
                            </div>
                          )}
                          {!linkedLoading && linkedContext && (
                            <div className="space-y-4">
                              {linkedContext.emails.length > 0 && (
                                <div>
                                  <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">Related emails</h4>
                                  <div className="space-y-2">
                                    {linkedContext.emails.map((email) => (
                                      <div
                                        key={email.id}
                                        onClick={() => navigate(`/gmail?thread=${email.id}`)}
                                        className="flex items-start gap-2 p-2 rounded-md bg-slate-800/50 hover:bg-slate-800 cursor-pointer transition-colors"
                                      >
                                        <Icon name="mail" className="text-blue-400 mt-0.5" size={14} />
                                        <div className="flex-1 min-w-0">
                                          <p className="text-slate-200 text-sm truncate">{email.subject}</p>
                                          <p className="text-slate-500 text-xs">{email.from}</p>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}

                              {linkedContext.events.length > 0 && (
                                <div>
                                  <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">Related events</h4>
                                  <div className="space-y-2">
                                    {linkedContext.events.map((ev) => (
                                      <div
                                        key={ev.id}
                                        onClick={() => navigate('/calendar')}
                                        className="flex items-start gap-2 p-2 rounded-md bg-slate-800/50 hover:bg-slate-800 cursor-pointer transition-colors"
                                      >
                                        <Icon name="event" className="text-purple-400 mt-0.5" size={14} />
                                        <div className="flex-1 min-w-0">
                                          <p className="text-slate-200 text-sm">{ev.summary}</p>
                                          {ev.start && (
                                            <p className="text-slate-500 text-xs">
                                              {(() => {
                                                try {
                                                  const d = new Date(ev.start);
                                                  return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' }) + ' at ' + d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
                                                } catch { return ev.start; }
                                              })()}
                                            </p>
                                          )}
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}

                              {linkedContext.files.length > 0 && (
                                <div>
                                  <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">Related files</h4>
                                  <div className="space-y-2">
                                    {linkedContext.files.map((file) => (
                                      <a
                                        key={file.id}
                                        href={file.webViewLink || '#'}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="flex items-start gap-2 p-2 rounded-md bg-slate-800/50 hover:bg-slate-800 transition-colors block"
                                      >
                                        <Icon name="description" className="text-emerald-400 mt-0.5" size={14} />
                                        <div className="flex-1 min-w-0">
                                          <p className="text-slate-200 text-sm truncate">{file.name}</p>
                                        </div>
                                        <Icon name="open_in_new" className="text-slate-500" size={12} />
                                      </a>
                                    ))}
                                  </div>
                                </div>
                              )}

                              {linkedContext.emails.length === 0 && linkedContext.events.length === 0 && linkedContext.files.length === 0 && (
                                <p className="text-slate-500">No related emails, events, or files found for this task.</p>
                              )}
                            </div>
                          )}
                          {!linkedLoading && !linkedContext && (
                            <p className="text-slate-500">No related items found.</p>
                          )}
                        </div>
                      )}

                      {/* Link a commit to this task */}
                      <div className="mt-4 pt-3 border-t border-slate-800">
                        {commitTaskId === task.id ? (
                          <div className="space-y-2">
                            <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Link a commit to this task</h4>
                            <div className="flex items-center gap-2">
                              <input
                                type="text"
                                value={commitMessage}
                                onChange={(e) => setCommitMessage(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") handleCommit(task.id);
                                  if (e.key === "Escape") { setCommitTaskId(null); setCommitMessage(""); setCommitResult(null); }
                                }}
                                placeholder="What did you change?"
                                className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-slate-300 placeholder-slate-600 focus:outline-none focus:border-blue-500"
                                autoFocus
                                data-testid="commit-message-input"
                              />
                              <button
                                onClick={() => handleCommit(task.id)}
                                disabled={commitLoading || !commitMessage.trim()}
                                className="px-3 py-1.5 bg-green-600 hover:bg-green-700 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-sm text-white flex items-center gap-1.5"
                                data-testid="commit-submit-btn"
                              >
                                <Icon name="commit" className="text-sm" />
                                {commitLoading ? "Saving..." : "Save"}
                              </button>
                              <button
                                onClick={() => { setCommitTaskId(null); setCommitMessage(""); setCommitResult(null); }}
                                className="p-1.5 text-slate-500 hover:text-slate-300"
                                title="Cancel"
                              >
                                <Icon name="close" className="text-sm" />
                              </button>
                            </div>
                            {commitResult && (
                              <p data-testid="commit-result" className="text-xs text-green-400 flex items-center gap-1">
                                <Icon name="check_circle" className="text-sm" />
                                {commitResult}
                              </p>
                            )}
                          </div>
                        ) : (
                          <button
                            onClick={() => { setCommitTaskId(task.id); setCommitResult(null); }}
                            className="flex items-center gap-2 text-xs text-slate-400 hover:text-slate-200 transition-colors"
                            data-testid="commit-trigger-btn"
                          >
                            <Icon name="commit" className="text-sm" />
                            Link a commit to this task
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
                )}
              </SortableTaskWrapper>
                    ))}
                  </SortableContext>
              ))}
            </div>
            <DragOverlay>
              {activeDragId ? (
                <div className="bg-slate-900/90 border border-blue-500/60 rounded-lg px-4 py-3 flex items-center gap-3 shadow-xl opacity-90">
                  <Icon name="drag_indicator" className="text-slate-400 text-lg" />
                  <span className="text-sm text-slate-300">
                    {tasks.find((t) => t.id === activeDragId)?.title ?? activeDragId}
                  </span>
                </div>
              ) : null}
            </DragOverlay>
            </DndContext>}

            {/* Footer */}
            <div className="flex items-center justify-between mt-6 text-sm" data-testid="tasks-footer">
              <div className="flex items-center gap-4 flex-wrap">
                <span className="text-slate-400" data-testid="footer-open-count">
                  <span className="text-white font-medium">{visibleCount}</span> Open
                </span>
                <span className="text-slate-400" data-testid="footer-closed-count">
                  <span className="text-white font-medium">{closedCount}</span> Closed
                </span>
                {filtersHidingAllTasks && (
                  <span className="text-slate-500 text-xs" data-testid="filters-hiding-hint">
                    {openCount} open total &middot; 0 match your filters &middot;{" "}
                    <button
                      onClick={clearAllFilters}
                      className="text-blue-400 hover:text-blue-300 underline"
                      data-testid="clear-filters-btn"
                    >
                      Clear filters
                    </button>
                  </span>
                )}

              </div>
              <span className="text-slate-600 text-xs">
                Press <kbd className="px-1.5 py-0.5 bg-slate-800 rounded text-slate-400">/</kbd> for new task
              </span>
            </div>
          </>
        )}
      </div>

      <TasksAuditModal
        open={auditModalOpen}
        onClose={() => setAuditModalOpen(false)}
        onTasksChanged={() => {
          fetchTasks();
        }}
      />

      <PlanWavesPanel
        open={wavesModalOpen}
        onClose={() => {
          setWavesModalOpen(false);
          // Refresh wave assignments so badges update after a plan run.
          api.get<{ assignments: Record<string, number> }>('/tasks/waves/assignments')
            .then((res) => setWaveAssignments(res.assignments ?? {}))
            .catch(() => {});
        }}
      />

      <ConfirmModal
        open={deleteAllConfirmOpen}
        title={`Delete all ${filteredTasks.length} task${filteredTasks.length === 1 ? "" : "s"}?`}
        message="This removes every task currently visible with your filters. It cannot be undone."
        confirmLabel="Delete all"
        danger
        onConfirm={executeDeleteAll}
        onCancel={() => setDeleteAllConfirmOpen(false)}
      />

      {/* Import modal */}
      {importModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setImportModalOpen(false)}>
          <div className="bg-slate-900 border border-slate-700 rounded-2xl p-6 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Import Tasks</h2>
              <button onClick={() => setImportModalOpen(false)} className="text-slate-400 hover:text-white">
                <Icon name="close" size={20} />
              </button>
            </div>
            <div className="flex gap-2 mb-4">
              <button
                onClick={() => { setImportSource('linear'); setImportResult(null); setImportFields({}); }}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${importSource === 'linear' ? 'bg-purple-500/20 text-purple-300' : 'bg-slate-800 text-slate-400 hover:text-slate-300'}`}
              >
                Linear
              </button>
              <button
                onClick={() => { setImportSource('jira'); setImportResult(null); setImportFields({}); }}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${importSource === 'jira' ? 'bg-blue-500/20 text-blue-300' : 'bg-slate-800 text-slate-400 hover:text-slate-300'}`}
              >
                Jira
              </button>
            </div>

            {importSource === 'linear' ? (
              <div className="space-y-3">
                <div>
                  <label className="block text-sm text-slate-400 mb-1">API Key</label>
                  <input
                    type="password"
                    value={importFields.linear_key || ''}
                    onChange={(e) => setImportFields({ ...importFields, linear_key: e.target.value })}
                    placeholder="lin_api_..."
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none focus:border-purple-500/50"
                  />
                </div>
                <div>
                  <label className="block text-sm text-slate-400 mb-1">Team ID</label>
                  <input
                    type="text"
                    value={importFields.linear_team || ''}
                    onChange={(e) => setImportFields({ ...importFields, linear_team: e.target.value })}
                    onKeyDown={(e) => { if (e.key === "Enter") (document.querySelector("[data-import-submit]") as HTMLButtonElement)?.click(); }}
                    placeholder="e.g. abc123"
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none focus:border-purple-500/50"
                  />
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <div>
                  <label className="block text-sm text-slate-400 mb-1">Jira domain</label>
                  <input
                    type="text"
                    value={importFields.jira_domain || ''}
                    onChange={(e) => setImportFields({ ...importFields, jira_domain: e.target.value })}
                    placeholder="yourcompany.atlassian.net"
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none focus:border-purple-500/50"
                  />
                </div>
                <div>
                  <label className="block text-sm text-slate-400 mb-1">Email</label>
                  <input
                    type="email"
                    value={importFields.jira_email || ''}
                    onChange={(e) => setImportFields({ ...importFields, jira_email: e.target.value })}
                    placeholder="you@company.com"
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none focus:border-purple-500/50"
                  />
                </div>
                <div>
                  <label className="block text-sm text-slate-400 mb-1">API Token</label>
                  <input
                    type="password"
                    value={importFields.jira_token || ''}
                    onChange={(e) => setImportFields({ ...importFields, jira_token: e.target.value })}
                    placeholder="Your Jira API token"
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none focus:border-purple-500/50"
                  />
                </div>
                <div>
                  <label className="block text-sm text-slate-400 mb-1">Project Key</label>
                  <input
                    type="text"
                    value={importFields.jira_project || ''}
                    onChange={(e) => setImportFields({ ...importFields, jira_project: e.target.value })}
                    onKeyDown={(e) => { if (e.key === "Enter") (document.querySelector("[data-import-submit]") as HTMLButtonElement)?.click(); }}
                    placeholder="e.g. PROJ"
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-500 outline-none focus:border-purple-500/50"
                  />
                </div>
              </div>
            )}

            {importResult && (
              <div className={`mt-4 p-3 rounded-lg text-sm ${importResult.errors.length > 0 ? 'bg-amber-500/10 border border-amber-500/30 text-amber-300' : 'bg-green-500/10 border border-green-500/30 text-green-300'}`}>
                {importResult.created > 0 && <span>Created {importResult.created} tasks. </span>}
                {importResult.errors.map((e, i) => <span key={i} className="block">{e}</span>)}
              </div>
            )}

            <button
              onClick={async () => {
                setImportLoading(true);
                setImportResult(null);
                try {
                  if (importSource === 'linear') {
                    const res = await api.post<{ ok: boolean; created: number; errors: string[] }>('/import/linear', {
                      api_key: importFields.linear_key || '',
                      team_id: importFields.linear_team || '',
                    });
                    setImportResult(res);
                  } else {
                    const res = await api.post<{ ok: boolean; created: number; errors: string[] }>('/import/jira', {
                      domain: importFields.jira_domain || '',
                      email: importFields.jira_email || '',
                      api_token: importFields.jira_token || '',
                      project_key: importFields.jira_project || '',
                    });
                    setImportResult(res);
                  }
                  fetchTasks();
                } catch (err: unknown) {
                  const message = err instanceof Error ? err.message : 'Import failed';
                  setImportResult({ ok: false, created: 0, errors: [message] });
                } finally {
                  setImportLoading(false);
                }
              }}
              disabled={importLoading}
              data-import-submit
              className="mt-4 w-full py-2.5 bg-blue-600 hover:bg-blue-700 rounded-xl font-medium text-sm transition-colors disabled:opacity-50"
            >
              {importLoading ? 'Importing...' : `Import from ${importSource === 'linear' ? 'Linear' : 'Jira'}`}
            </button>
          </div>
        </div>
      )}

      {/* Undo delete toast */}
      {/* Priority reason prompt */}
      {pendingPriorityChange && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-slate-800 border border-slate-700 rounded-xl shadow-xl p-5 w-80 space-y-3">
            <div className="text-sm font-medium text-slate-200">
              Why are you changing the priority?
            </div>
            <p className="text-xs text-slate-400">Optional. Press Skip to change without a note.</p>
            <input
              type="text"
              value={priorityReason}
              onChange={(e) => setPriorityReason(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitPriorityChange();
                if (e.key === "Escape") commitPriorityChange(true);
              }}
              placeholder="e.g. customer request, blocking release..."
              className="w-full px-3 py-2 bg-slate-900 border border-slate-600 rounded-lg text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500"
              autoFocus
            />
            <div className="flex items-center justify-end gap-2">
              <button
                onClick={() => commitPriorityChange(true)}
                className="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors"
              >
                Skip
              </button>
              <button
                onClick={() => commitPriorityChange()}
                className="px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}

      {closeError && (
        <div
          data-testid="close-task-error-toast"
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-red-950 border border-red-700 text-sm text-red-200 px-4 py-3 rounded-xl shadow-lg"
        >
          <span>{closeError}</span>
          <button
            data-testid="close-task-error-dismiss"
            onClick={() => setCloseError(null)}
            className="font-medium text-red-400 hover:text-red-300"
          >
            Dismiss
          </button>
        </div>
      )}

      {undoDelete && (
        <div
          data-testid="undo-delete-task-toast"
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-slate-800 border border-slate-700 text-sm text-slate-200 px-4 py-3 rounded-xl shadow-lg"
        >
          <span>Task deleted.</span>
          <button
            data-testid="undo-delete-task-button"
            onClick={handleUndo}
            className="font-medium text-blue-400 hover:text-blue-300"
          >
            Undo
          </button>
        </div>
      )}
    </div>
  );
}
