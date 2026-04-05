import { useState, useEffect, useCallback, useRef } from "react";
import Icon from "../components/Icon";
import TopBar from "../components/TopBar";
import GoalsView from "../components/GoalsView";
import { api } from "../lib/api";

interface Task {
  id: string;
  title: string;
  priority: string;
  status: string;
  created_at: string;
  description?: string;
  tags?: string[];
  goal?: string | null;
}

interface TasksResponse {
  tasks: Task[];
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

export default function Tasks() {
  const inputRef = useRef<HTMLInputElement>(null);

  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [newTaskTitle, setNewTaskTitle] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("open");
  const [priorityFilter, setPriorityFilter] = useState<string | null>(null);
  const [goalFilter, setGoalFilter] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"list" | "grid">("list");
  const [banner, setBanner] = useState<string | null>(null);
  const [openPriorityDropdown, setOpenPriorityDropdown] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"tasks" | "goals">("tasks");

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

  useEffect(() => {
    fetchTasks();
  }, [fetchTasks]);

  // Close the priority dropdown when clicking outside
  useEffect(() => {
    if (!openPriorityDropdown) return;
    const handleClick = () => setOpenPriorityDropdown(null);
    document.addEventListener("click", handleClick);
    return () => document.removeEventListener("click", handleClick);
  }, [openPriorityDropdown]);

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

  const updatePriority = async (id: string, priority: string) => {
    try {
      // Optimistic update: change it in the UI right away
      setTasks((prev) =>
        prev.map((t) => (t.id === id ? { ...t, priority } : t))
      );
      setOpenPriorityDropdown(null);
      await api.patch(`/tasks/${id}`, { priority });
    } catch (e) {
      console.error("Failed to update priority:", e);
      // Revert on failure
      await fetchTasks();
    }
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
    } catch (e) {
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

  // Filtering logic
  const isThisWeek = (dateStr: string) => {
    const d = new Date(dateStr);
    const now = new Date();
    const weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    return d >= weekAgo;
  };

  // Collect all unique goals for the filter dropdown
  const allGoals = Array.from(new Set(tasks.map((t) => t.goal).filter(Boolean) as string[])).sort();

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

  if (goalFilter) {
    filteredTasks = filteredTasks.filter((t) => t.goal === goalFilter);
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

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="Tasks" />

      <div className="pt-16 p-8 max-w-6xl mx-auto">
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
                onClick={() => setActiveTab("goals")}
                className={activeTab === "goals"
                  ? "text-blue-400 border-b-2 border-blue-400 pb-1 font-medium"
                  : "text-slate-400 pb-1 hover:text-slate-300"}
              >
                Goals
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
              onClick={copyTaskList}
              className="p-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg border border-slate-700"
            >
              <Icon name="content_copy" className="text-slate-400 text-base" />
            </button>
          </div>
        </div>

        {activeTab === "goals" ? (
          <GoalsView />
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
              <div className="flex items-center gap-4">
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

                {allGoals.length > 0 && (
                  <>
                    <div className="w-px h-5 bg-slate-800" />
                    <select
                      value={goalFilter ?? ""}
                      onChange={(e) => setGoalFilter(e.target.value || null)}
                      className="bg-slate-800 border border-slate-700 rounded-lg px-2 py-1 text-xs text-slate-300 outline-none"
                    >
                      <option value="">All projects</option>
                      {allGoals.map((g) => (
                        <option key={g} value={g}>{g}</option>
                      ))}
                    </select>
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
            <div className={viewMode === "grid" ? "grid grid-cols-2 lg:grid-cols-3 gap-2" : "flex flex-col gap-2"}>
              {loading && tasks.length === 0 && (
                <p className="text-sm text-slate-500 py-4">Loading tasks...</p>
              )}
              {!loading && filteredTasks.length === 0 && (
                <p className="text-sm text-slate-500 py-4">No tasks match this filter.</p>
              )}
              {filteredTasks.map((task) => (
                <div
                  key={task.id}
                  className="bg-slate-900/60 border border-slate-800 rounded-lg px-4 py-3 flex items-center gap-3 hover:border-slate-700 transition-colors"
                >
                  <Icon
                    name="drag_indicator"
                    className="text-slate-700 text-lg cursor-grab"
                  />
                  <button
                    onClick={() => task.status === "closed" ? reopenTask(task.id) : closeTask(task.id)}
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
                    <span className={`text-sm ${task.status === "closed" ? "line-through text-slate-500" : ""}`}>
                      {task.title}
                    </span>
                    {task.goal && (
                      <span className="ml-2 inline-flex items-center gap-1 text-[11px] text-slate-400 bg-slate-800 px-1.5 py-0.5 rounded">
                        <Icon name="flag" className="text-[11px] text-slate-500" />
                        {task.goal}
                      </span>
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
              ))}
            </div>

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

      {/* Floating add button */}
      <button
        onClick={() => inputRef.current?.focus()}
        className="fixed bottom-8 right-8 w-14 h-14 bg-pink-500 hover:bg-pink-600 rounded-full flex items-center justify-center shadow-lg shadow-pink-500/25"
      >
        <Icon name="add" className="text-white text-2xl" />
      </button>
    </div>
  );
}
