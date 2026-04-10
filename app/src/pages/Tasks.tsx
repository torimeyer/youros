import { useState, useEffect, useCallback, useRef } from "react";
import { useSearchParams } from "react-router-dom";
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
import LabelsView from "../components/LabelsView";
import HealthCheckView from "../components/HealthCheckView";
import type { Label } from "../components/LabelsView";
import { api } from "../lib/api";
import SharePopover from "../components/SharePopover";
import ExportButton from "../components/ExportButton";

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
  thread_id?: string | null;
}

interface Thread {
  id: string;
  name: string;
  needle_ids: string[];
  created_at: string;
}

interface ThreadsResponse {
  threads: Thread[];
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
};

const priorityDotColors: Record<string, string> = {
  P0: "bg-pink-500",
  P1: "bg-orange-500",
  P2: "bg-blue-500",
};

const PRIORITIES = ["P0", "P1", "P2"] as const;

type StatusFilter = "open" | "closed" | "week";

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
  const focusParam = searchParams.get("focus");
  const focusAppliedRef = useRef(false);

  const [tasks, setTasks] = useState<Task[]>([]);
  const [labels, setLabels] = useState<Label[]>([]);
  const [loading, setLoading] = useState(true);
  const [newTaskTitle, setNewTaskTitle] = useState("");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<"context" | "history">("context");
  const [briefing, setBriefing] = useState<TaskBriefing | null>(null);
  const [briefingLoading, setBriefingLoading] = useState(false);
  const [trace, setTrace] = useState<TaskTrace | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("open");
  const [priorityFilter, setPriorityFilter] = useState<string | null>(null);
  const [labelFilter, setLabelFilter] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"list" | "grid">("list");
  const [banner, setBanner] = useState<string | null>(null);
  const [openPriorityDropdown, setOpenPriorityDropdown] = useState<string | null>(null);
  const [openLabelDropdown, setOpenLabelDropdown] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"tasks" | "labels" | "groups" | "health">("tasks");
  const [openLinkDropdown, setOpenLinkDropdown] = useState<string | null>(null);
  const [linkTarget, setLinkTarget] = useState("");
  const [commitTaskId, setCommitTaskId] = useState<string | null>(null);
  const [commitMessage, setCommitMessage] = useState("");
  const [commitLoading, setCommitLoading] = useState(false);
  const [commitResult, setCommitResult] = useState<string | null>(null);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [threadFilter, setThreadFilter] = useState<string | null>(null);
  const [openThreadDropdown, setOpenThreadDropdown] = useState<string | null>(null);
  const [newThreadName, setNewThreadName] = useState("");
  const [showNewThreadInput, setShowNewThreadInput] = useState(false);
  const [autoLabelingTaskId, setAutoLabelingTaskId] = useState<string | null>(null);
  const [labelAllLoading, setLabelAllLoading] = useState(false);
  const [labelAllResult, setLabelAllResult] = useState<string | null>(null);
  const [activeDragId, setActiveDragId] = useState<string | null>(null);
  const [showTaskSharePopover, setShowTaskSharePopover] = useState(false);
  const [undoDelete, setUndoDelete] = useState<{ task: Task; timer: ReturnType<typeof setTimeout> } | null>(null);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } })
  );

  const fetchTasks = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get<TasksResponse>("/tasks");
      setTasks(res.tasks ?? []);
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

  const fetchThreads = useCallback(async () => {
    try {
      const res = await api.get<ThreadsResponse>("/threads");
      setThreads(res.threads ?? []);
    } catch (e) {
      console.error("Failed to fetch groups:", e);
    }
  }, []);


  const createThread = async (name: string) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      await api.post("/threads", { name: trimmed });
      setNewThreadName("");
      setShowNewThreadInput(false);
      await fetchThreads();
    } catch (e) {
      console.error("Failed to create group:", e);
    }
  };

  const deleteThread = async (threadId: string) => {
    try {
      await api.delete(`/threads/${threadId}`);
      if (threadFilter === threadId) setThreadFilter(null);
      await fetchThreads();
    } catch (e) {
      console.error("Failed to delete group:", e);
    }
  };

  const assignTaskToThread = async (taskId: string, threadId: string) => {
    try {
      // Remove from current thread if assigned
      const currentThreadId = tasks.find((t) => t.id === taskId)?.thread_id;
      if (currentThreadId) {
        await api.delete(`/threads/${currentThreadId}/tasks/${taskId}`);
      }
      await api.post(`/threads/${threadId}/tasks/${taskId}`);
      // Optimistic update
      setTasks((prev) =>
        prev.map((t) => (t.id === taskId ? { ...t, thread_id: threadId } : t))
      );
      setOpenThreadDropdown(null);
      fetchThreads();
    } catch (e) {
      console.error("Failed to assign task to group:", e);
    }
  };

  const removeTaskFromThread = async (taskId: string, threadId: string) => {
    try {
      await api.delete(`/threads/${threadId}/tasks/${taskId}`);
      // Optimistic update
      setTasks((prev) =>
        prev.map((t) => (t.id === taskId ? { ...t, thread_id: null } : t))
      );
      fetchThreads();
    } catch (e) {
      console.error("Failed to remove task from group:", e);
    }
  };

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

  const handleTaskClick = useCallback((taskId: string) => {
    if (selectedTaskId === taskId) {
      setSelectedTaskId(null);
      setBriefing(null);
      setTrace(null);
    } else {
      setSelectedTaskId(taskId);
      setDetailTab("context");
      fetchBriefing(taskId);
      fetchTrace(taskId);
    }
  }, [selectedTaskId, fetchBriefing, fetchTrace]);

  useEffect(() => {
    fetchTasks();
    fetchLabels();
    fetchThreads();
  }, [fetchTasks, fetchLabels, fetchThreads]);

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
    // focused task is closed but the current filter only shows open, switch.
    if (match.status === "closed" && statusFilter === "open") {
      setStatusFilter("closed");
    } else if (match.status === "open" && statusFilter === "closed") {
      setStatusFilter("open");
    }

    // Clear any priority / label / thread filter that would hide the task.
    if (priorityFilter && match.priority !== priorityFilter) {
      setPriorityFilter(null);
    }
    if (labelFilter && !(match.label_ids || []).includes(labelFilter)) {
      setLabelFilter(null);
    }
    if (threadFilter && match.thread_id !== threadFilter) {
      setThreadFilter(null);
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
  }, [focusParam, tasks, statusFilter, priorityFilter, labelFilter, threadFilter, fetchBriefing, fetchTrace, searchParams, setSearchParams]);

  // Listen for quick-add-task event from TopBar
  useEffect(() => {
    const handler = () => {
      setActiveTab("tasks");
      inputRef.current?.focus();
    };
    window.addEventListener('myos-quick-add-task', handler);
    return () => window.removeEventListener('myos-quick-add-task', handler);
  }, []);

  // Close dropdowns when clicking outside
  useEffect(() => {
    if (!openPriorityDropdown && !openLabelDropdown && !openLinkDropdown && !openThreadDropdown) return;
    const handleClick = () => {
      setOpenPriorityDropdown(null);
      setOpenLabelDropdown(null);
      setOpenLinkDropdown(null);
      setOpenThreadDropdown(null);
    };
    document.addEventListener("click", handleClick);
    return () => document.removeEventListener("click", handleClick);
  }, [openPriorityDropdown, openLabelDropdown, openLinkDropdown, openThreadDropdown]);

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
      await api.post(`/tasks/${id}/close`);
      await fetchTasks();
    } catch (e) {
      console.error("Failed to close task:", e);
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

  const deleteTask = (id: string) => {
    // Cancel any previous pending delete
    if (undoDelete) {
      clearTimeout(undoDelete.timer);
      api.delete(`/tasks/${undoDelete.task.id}`).catch(() => {});
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

    // Start a 5-second timer before actually deleting
    const timer = setTimeout(() => {
      api.delete(`/tasks/${id}`).catch((e) => console.error("Failed to delete task:", e));
      setUndoDelete(null);
    }, 5000);

    setUndoDelete({ task, timer });
  };

  const handleUndo = () => {
    if (!undoDelete) return;
    clearTimeout(undoDelete.timer);
    setTasks((prev) => [...prev, undoDelete.task]);
    setUndoDelete(null);
  };

  const updatePriority = async (id: string, priority: string) => {
    try {
      setTasks((prev) =>
        prev.map((t) => (t.id === id ? { ...t, priority } : t))
      );
      setOpenPriorityDropdown(null);
      await api.patch(`/tasks/${id}`, { priority });
    } catch (e) {
      console.error("Failed to update priority:", e);
      await fetchTasks();
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
      // Insert the new group order inline — just append; visual sort is by priority
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

  // Build a lookup map: thread id -> Thread
  const threadsById = new Map(threads.map((th) => [th.id, th]));

  // Filtering logic
  let filteredTasks = tasks;

  if (statusFilter === "open") {
    filteredTasks = filteredTasks.filter((t) => t.status === "open");
  } else if (statusFilter === "closed") {
    filteredTasks = filteredTasks.filter((t) => t.status === "closed");
  } else if (statusFilter === "week") {
    filteredTasks = filteredTasks.filter((t) => t.status === "open" && isThisWeek(t.created_at));
  }

  if (priorityFilter) {
    filteredTasks = filteredTasks.filter((t) => t.priority === priorityFilter);
  }

  if (labelFilter) {
    filteredTasks = filteredTasks.filter((t) =>
      (t.label_ids || []).includes(labelFilter)
    );
  }

  if (threadFilter) {
    filteredTasks = filteredTasks.filter((t) => t.thread_id === threadFilter);
  }

  const openCount = tasks.filter((t) => t.status === "open").length;
  const closedCount = tasks.filter((t) => t.status === "closed").length;
  const weekCount = tasks.filter((t) => t.status === "open" && isThisWeek(t.created_at)).length;
  const p0Count = tasks.filter((t) => t.status === "open" && t.priority === "P0").length;
  const p1Count = tasks.filter((t) => t.status === "open" && t.priority === "P1").length;
  const p2Count = tasks.filter((t) => t.status === "open" && t.priority === "P2").length;

  const filterCounts: Record<StatusFilter, number> = {
    open: openCount,
    closed: closedCount,
    week: weekCount,
  };

  const priorityCounts: Record<string, number> = { P0: p0Count, P1: p1Count, P2: p2Count };

  const statusFilterClass = (f: StatusFilter) =>
    statusFilter === f
      ? "px-3 py-1 rounded-md bg-slate-800 text-white font-medium flex items-center gap-1.5"
      : "px-3 py-1 rounded-md text-slate-400 hover:text-slate-300 flex items-center gap-1.5";

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
              {isAuto && (
                <span data-testid={`auto-icon-${task.id}-${lid}`} className="inline-flex">
                  <Icon name="auto_awesome" className="text-[9px]" />
                </span>
              )}
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
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="Tasks" />

      <div data-tour="tasks" className="pt-20 p-8 max-w-6xl mx-auto">
        {/* Banner */}
        {banner && (
          <div className="mb-4 px-4 py-3 bg-purple-500/20 border border-purple-500/40 rounded-lg text-sm text-purple-200 flex items-center justify-between">
            <span>{banner}</span>
            <button onClick={() => setBanner(null)} className="text-purple-400 hover:text-white ml-4">
              <Icon name="close" className="text-base" />
            </button>
          </div>
        )}

        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold">Tasks</h1>
              <span className="flex items-center gap-1.5 text-xs text-green-400 bg-green-500/10 px-2 py-0.5 rounded-full">
                <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
                LIVE
              </span>
            </div>
            <div className="flex items-center gap-4 text-sm">
              <button
                onClick={() => setActiveTab("tasks")}
                className={activeTab === "tasks"
                  ? "text-blue-400 border-b-2 border-blue-400 pb-1 font-medium"
                  : "text-slate-400 pb-1 hover:text-slate-300"}
              >
                Tasks
              </button>
              <button
                onClick={() => setActiveTab("labels")}
                className={activeTab === "labels"
                  ? "text-blue-400 border-b-2 border-blue-400 pb-1 font-medium"
                  : "text-slate-400 pb-1 hover:text-slate-300"}
              >
                Labels
              </button>
              <button
                onClick={() => setActiveTab("health")}
                className={activeTab === "health"
                  ? "text-blue-400 border-b-2 border-blue-400 pb-1 font-medium"
                  : "text-slate-400 pb-1 hover:text-slate-300"}
              >
                Health
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={handleNext}
              className="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 text-sm px-3 py-1.5 rounded-lg border border-slate-700"
            >
              <Icon name="auto_awesome" className="text-amber-400 text-base" />
              What should I do next?
            </button>
            <button
              data-testid="label-all-btn"
              onClick={labelAllTasks}
              disabled={labelAllLoading}
              className="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-sm px-3 py-1.5 rounded-lg border border-slate-700"
              title="Auto-label all unlabeled tasks"
            >
              {labelAllLoading ? (
                <Icon name="hourglass_empty" className="text-purple-400 text-base animate-spin" />
              ) : (
                <Icon name="label" className="text-purple-400 text-base" />
              )}
              {labelAllLoading ? "Labeling..." : "Label all"}
            </button>
            {labelAllResult && (
              <span className="text-xs text-purple-300 bg-purple-500/10 px-2 py-1 rounded-md border border-purple-500/30">
                {labelAllResult}
              </span>
            )}
            <button
              onClick={copyTaskList}
              className="p-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg border border-slate-700"
              title="Copy task list"
            >
              <Icon name="content_copy" className="text-slate-400 text-base" />
            </button>
            <div className="relative">
              <button
                onClick={() => setShowTaskSharePopover((v) => !v)}
                className="p-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg border border-slate-700"
                title="Share this view"
              >
                <Icon name="share" className="text-slate-400 text-base" />
              </button>
              {showTaskSharePopover && (
                <SharePopover
                  shareType="task_list"
                  contentIds={filteredTasks.map((t) => t.id)}
                  title={statusFilter === "open" ? "Open tasks" : statusFilter === "closed" ? "Closed tasks" : "Tasks this week"}
                  onClose={() => setShowTaskSharePopover(false)}
                />
              )}
            </div>
            <ExportButton
              contentLabel="tasks"
              buildUrl={(format) => {
                const exportStatus = statusFilter === "closed" ? "closed" : "open";
                return `/api/export/tasks?format=${format}&status=${exportStatus}`;
              }}
            />
          </div>
        </div>

        {activeTab === "health" ? (
          <HealthCheckView />
        ) : activeTab === "groups" ? (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <p className="text-sm text-slate-400">
                Organize related tasks into groups, like mini-projects.
              </p>
              <button
                onClick={() => setShowNewThreadInput(true)}
                className="flex items-center gap-1.5 bg-blue-500 hover:bg-blue-600 text-sm px-3 py-1.5 rounded-lg text-white"
              >
                <Icon name="add" className="text-base" />
                New group
              </button>
            </div>

            {showNewThreadInput && (
              <div className="flex items-center gap-2 bg-slate-900/60 border border-slate-800 rounded-lg px-4 py-3">
                <input
                  type="text"
                  value={newThreadName}
                  onChange={(e) => setNewThreadName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") createThread(newThreadName); }}
                  placeholder="Group name..."
                  className="flex-1 bg-transparent text-sm text-slate-300 placeholder-slate-600 focus:outline-none"
                  autoFocus
                />
                <button
                  onClick={() => createThread(newThreadName)}
                  className="px-3 py-1 bg-blue-500 hover:bg-blue-600 rounded text-xs text-white"
                >
                  Create
                </button>
                <button
                  onClick={() => { setShowNewThreadInput(false); setNewThreadName(""); }}
                  className="text-slate-500 hover:text-slate-300"
                >
                  <Icon name="close" className="text-base" />
                </button>
              </div>
            )}

            {threads.length === 0 && !showNewThreadInput && (
              <div className="text-center py-12">
                <Icon name="folder_open" className="text-4xl text-slate-700 mb-2" />
                <p className="text-sm text-slate-500">No groups yet. Create one to organize your tasks.</p>
              </div>
            )}

            {threads.map((thread) => {
              const threadTasks = tasks.filter((t) => t.thread_id === thread.id);
              const openTasks = threadTasks.filter((t) => t.status === "open");
              const closedTasks = threadTasks.filter((t) => t.status === "closed");
              return (
                <div key={thread.id} className="bg-slate-900/60 border border-slate-800 rounded-lg p-4">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <Icon name="folder" className="text-teal-400 text-lg" />
                      <h3 className="text-sm font-medium text-white">{thread.name}</h3>
                      <span className="text-xs text-slate-500">
                        {openTasks.length} open, {closedTasks.length} done
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => { setThreadFilter(thread.id); setActiveTab("tasks"); }}
                        className="text-xs text-blue-400 hover:text-blue-300 px-2 py-1"
                      >
                        View tasks
                      </button>
                      <button
                        onClick={() => deleteThread(thread.id)}
                        className="text-slate-600 hover:text-red-400 transition-colors"
                        title="Delete this group"
                      >
                        <Icon name="delete" className="text-sm" />
                      </button>
                    </div>
                  </div>
                  {threadTasks.length === 0 ? (
                    <p className="text-xs text-slate-600">
                      No tasks in this group yet. Use the folder icon on any task to add it here.
                    </p>
                  ) : (
                    <div className="space-y-1">
                      {threadTasks.slice(0, 5).map((t) => (
                        <div key={t.id} className="flex items-center gap-2 text-xs">
                          <span className={`w-1.5 h-1.5 rounded-full ${
                            t.status === "closed" ? "bg-green-500" : priorityDotColors[t.priority] || "bg-slate-500"
                          }`} />
                          <span className="text-slate-500 font-mono">#{t.id}</span>
                          <span className={t.status === "closed" ? "text-slate-500 line-through" : "text-slate-300"}>
                            {t.title}
                          </span>
                        </div>
                      ))}
                      {threadTasks.length > 5 && (
                        <p className="text-xs text-slate-600 pl-4">
                          and {threadTasks.length - 5} more...
                        </p>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : activeTab === "labels" ? (
          <LabelsView
            onFilterByLabel={(id) => {
              setLabelFilter(id);
              if (id) setActiveTab("tasks");
            }}
            activeLabelId={labelFilter}
            onLabelsChanged={() => {
              fetchLabels();
              fetchTasks();
            }}
          />
        ) : (
          <>
            {/* Quick add input */}
            <div className="flex items-center gap-3 mb-6">
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
                className="w-9 h-9 flex items-center justify-center bg-blue-500 hover:bg-blue-600 rounded-full text-white"
              >
                <Icon name="add" className="text-lg" />
              </button>
            </div>

            {/* Filters */}
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-4 flex-wrap">
                <div className="flex items-center gap-1 text-sm">
                  {(["open", "closed", "week"] as StatusFilter[]).map((f) => (
                    <button key={f} className={statusFilterClass(f)} onClick={() => setStatusFilter(f)}>
                      {f === "week" ? "This week" : f.charAt(0).toUpperCase() + f.slice(1)}
                      <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                        statusFilter === f ? "bg-blue-500/30 text-blue-300" : "bg-slate-700 text-slate-500"
                      }`}>
                        {filterCounts[f]}
                      </span>
                    </button>
                  ))}
                </div>

                <div className="w-px h-5 bg-slate-800" />

                <div className="flex items-center gap-2">
                  {["P0", "P1", "P2"].map((p) => (
                    <button
                      key={p}
                      onClick={() => setPriorityFilter(priorityFilter === p ? null : p)}
                      className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
                        priorityFilter === p
                          ? `${priorityStyles[p]} ring-1 ring-white/30`
                          : `${priorityStyles[p]} opacity-60 hover:opacity-100`
                      }`}
                    >
                      <span className={`w-2 h-2 rounded-full ${priorityDotColors[p]}`} />
                      {p} <span className="opacity-70">{priorityCounts[p]}</span>
                    </button>
                  ))}
                </div>

                {/* Label filter chips */}
                {labels.length > 0 && (
                  <>
                    <div className="w-px h-5 bg-slate-800" />
                    <div className="flex items-center gap-1.5 flex-wrap">
                      {labels.map((label) => (
                        <button
                          key={label.id}
                          onClick={() => setLabelFilter(labelFilter === label.id ? null : label.id)}
                          className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium transition-all ${
                            labelFilter === label.id
                              ? "ring-1 ring-white/30"
                              : "opacity-60 hover:opacity-100"
                          }`}
                          style={{
                            backgroundColor: label.color + "20",
                            color: label.color,
                          }}
                        >
                          <span
                            className="w-2 h-2 rounded-full"
                            style={{ backgroundColor: label.color }}
                          />
                          {label.name}
                        </button>
                      ))}
                      {labelFilter && (
                        <button
                          onClick={() => setLabelFilter(null)}
                          className="text-xs text-slate-500 hover:text-slate-300 px-1"
                          title="Clear label filter"
                        >
                          <Icon name="close" className="text-sm" />
                        </button>
                      )}
                    </div>
                  </>
                )}

                {/* Thread (group) filter chips */}
                {threads.length > 0 && (
                  <>
                    <div className="w-px h-5 bg-slate-800" />
                    <div className="flex items-center gap-1.5 flex-wrap">
                      {threads.map((thread) => (
                        <button
                          key={thread.id}
                          onClick={() => setThreadFilter(threadFilter === thread.id ? null : thread.id)}
                          className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium transition-all bg-teal-500/20 text-teal-400 ${
                            threadFilter === thread.id
                              ? "ring-1 ring-white/30"
                              : "opacity-60 hover:opacity-100"
                          }`}
                        >
                          <Icon name="folder" className="text-[10px]" />
                          {thread.name}
                        </button>
                      ))}
                      {threadFilter && (
                        <button
                          onClick={() => setThreadFilter(null)}
                          className="text-xs text-slate-500 hover:text-slate-300 px-1"
                          title="Clear group filter"
                        >
                          <Icon name="close" className="text-sm" />
                        </button>
                      )}
                    </div>
                  </>
                )}
              </div>

              <div className="flex items-center gap-1">
                <button
                  onClick={() => setViewMode("list")}
                  className={`p-1.5 ${viewMode === "list" ? "text-slate-400" : "text-slate-600"} hover:text-white`}
                >
                  <Icon name="view_list" className="text-lg" />
                </button>
                <button
                  onClick={() => setViewMode("grid")}
                  className={`p-1.5 ${viewMode === "grid" ? "text-slate-400" : "text-slate-600"} hover:text-white`}
                >
                  <Icon name="grid_view" className="text-lg" />
                </button>
              </div>
            </div>

            {/* Task list */}
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragStart={handleDragStart}
              onDragEnd={handleDragEnd}
            >
            <div className={viewMode === "grid" ? "grid grid-cols-2 lg:grid-cols-3 gap-2" : "flex flex-col gap-2"}>
              {loading && tasks.length === 0 && (
                <p className="text-sm text-slate-500 py-4">Loading tasks...</p>
              )}
              {!loading && filteredTasks.length === 0 && tasks.length === 0 && (
                <div className="text-center py-8">
                  <Icon name="checklist" className="text-4xl text-slate-700 mb-2" />
                  <p className="text-sm text-slate-400 mb-1">No tasks yet.</p>
                  <p className="text-xs text-slate-600">Type a task above, or tell myOS an idea in chat and it will create tasks for you.</p>
                </div>
              )}
              {!loading && filteredTasks.length === 0 && tasks.length > 0 && (
                <p className="text-sm text-slate-500 py-4">No tasks match this filter.</p>
              )}
              {(["P0", "P1", "P2"] as const).map((priority) => {
                const groupTasks = filteredTasks.filter((t) => t.priority === priority);
                if (groupTasks.length === 0) return null;
                return (
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
                    onClick={() => handleTaskClick(task.id)}
                    className={`bg-slate-900/60 border rounded-lg px-4 py-3 flex items-center gap-3 cursor-pointer transition-colors ${
                      selectedTaskId === task.id
                        ? "border-blue-500/60 bg-blue-500/5"
                        : "border-slate-800 hover:border-slate-700"
                    }`}
                  >
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
                        <span className={`text-sm ${task.status === "closed" ? "line-through text-slate-500" : ""}`}>
                          {task.title}
                        </span>
                        {isStale(task) && (
                          <span className="inline-flex items-center gap-1 text-[11px] text-slate-500">
                            <Icon name="schedule" className="text-[11px] text-slate-500" />
                            stale
                          </span>
                        )}
                        {renderDependencyPills(task)}
                        {renderTaskLabels(task)}
                        {task.thread_id && threadsById.get(task.thread_id) && (
                          <span
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium cursor-pointer hover:opacity-80 bg-teal-500/15 text-teal-400 border border-teal-500/30"
                            onClick={(e) => {
                              e.stopPropagation();
                              removeTaskFromThread(task.id, task.thread_id!);
                            }}
                            title={`In group "${threadsById.get(task.thread_id!)?.name}". Click to remove.`}
                          >
                            <Icon name="folder" className="text-[9px]" />
                            {threadsById.get(task.thread_id!)?.name}
                            <Icon name="close" className="text-[9px]" />
                          </span>
                        )}
                      </div>
                      {task.description && (
                        <p
                          data-testid={`task-description-${task.id}`}
                          className="text-xs text-slate-500 dark:text-slate-400 truncate mt-0.5"
                          title={task.description}
                        >
                          {task.description}
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
                    {/* Thread assignment dropdown */}
                    <div className="relative">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenThreadDropdown(openThreadDropdown === task.id ? null : task.id);
                        }}
                        className="p-1 text-slate-600 hover:text-slate-400 transition-colors"
                        title="Add to a group"
                      >
                        <Icon name="folder" className="text-sm" />
                      </button>
                      {openThreadDropdown === task.id && (
                        <div className="absolute right-0 top-full mt-1 z-50 bg-slate-800 border border-slate-700 rounded-lg shadow-xl py-1 min-w-[160px]">
                          {threads.length === 0 ? (
                            <p className="px-3 py-2 text-xs text-slate-500">
                              No groups yet. Create one in the Groups tab.
                            </p>
                          ) : (
                            threads.map((thread) => (
                              <button
                                key={thread.id}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  if (task.thread_id === thread.id) {
                                    removeTaskFromThread(task.id, thread.id);
                                    setOpenThreadDropdown(null);
                                  } else {
                                    assignTaskToThread(task.id, thread.id);
                                  }
                                }}
                                className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 hover:bg-slate-700 transition-colors ${
                                  task.thread_id === thread.id ? "text-teal-400" : "text-slate-300"
                                }`}
                              >
                                <Icon name={task.thread_id === thread.id ? "folder" : "folder_open"} className="text-sm text-teal-400" />
                                {thread.name}
                                {task.thread_id === thread.id && (
                                  <Icon name="check" className="text-xs text-teal-400 ml-auto" />
                                )}
                              </button>
                            ))
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

                  {/* Detail panel (Context + History tabs) */}
                  {selectedTaskId === task.id && (
                    <div data-testid="briefing-panel" className="ml-8 mt-1 mb-2 bg-slate-50 dark:bg-slate-900/80 border border-slate-200 dark:border-blue-500/30 rounded-lg p-4 text-sm text-slate-700 dark:text-slate-300">
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
                          data-testid="history-tab"
                          onClick={() => setDetailTab("history")}
                          className={`text-xs font-medium pb-1 ${
                            detailTab === "history"
                              ? "text-blue-400 border-b-2 border-blue-400"
                              : "text-slate-500 hover:text-slate-300"
                          }`}
                        >
                          History
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
                              {briefing.sphere && (
                                <div>
                                  <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1">Where this task sits</h4>
                                  <p className="text-slate-300">{briefing.sphere}</p>
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

                              {!briefing.sphere && briefing.blocked_by.length === 0 && briefing.unblocks.length === 0 && briefing.neighbors.length === 0 && (
                                <p className="text-slate-500">This task is standalone. No blockers, no dependencies, no related tasks.</p>
                              )}
                            </div>
                          )}
                        </>
                      )}

                      {/* History tab (trace / attribution chain) */}
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
                );
              })}
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
            </DndContext>

            {/* Footer */}
            <div className="flex items-center justify-between mt-6 text-sm">
              <div className="flex items-center gap-4">
                <span className="text-slate-400">
                  <span className="text-white font-medium">{openCount}</span> Open
                </span>
                <span className="text-slate-400">
                  <span className="text-white font-medium">{closedCount}</span> Closed
                </span>
              </div>
              <span className="text-slate-600 text-xs">
                Press <kbd className="px-1.5 py-0.5 bg-slate-800 rounded text-slate-400">/</kbd> for new task
              </span>
            </div>
          </>
        )}
      </div>

      {/* Undo delete toast */}
      {undoDelete && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-slate-800 border border-slate-700 text-sm text-slate-200 px-4 py-3 rounded-xl shadow-lg">
          <span>Task deleted.</span>
          <button
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
