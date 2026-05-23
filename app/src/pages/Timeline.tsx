import { useState, useEffect, useCallback } from "react";
import Icon from "../components/Icon";
import TopBar from "../components/TopBar";
import ExportButton from "../components/ExportButton";
import { api } from "../lib/api";
import { reportError } from '../lib/reportError';
import { EmptyState } from "../components/ui";

interface Task {
  id: string;
  title: string;
  priority: string;
  status: string;
  created_at: string;
  tags?: string[];
  goal?: string | null;
}

interface TasksResponse {
  tasks: Task[];
}

const COLS = 28;
const dates = Array.from({ length: COLS }, (_, i) => i + 1);

const goalColors = [
  { bg: "bg-blue-500/30", border: "border-blue-500", text: "text-blue-400", dot: "bg-blue-500" },
  { bg: "bg-pink-500/30", border: "border-pink-500", text: "text-pink-400", dot: "bg-pink-500" },
  { bg: "bg-purple-500/30", border: "border-purple-500", text: "text-purple-400", dot: "bg-purple-500" },
  { bg: "bg-cyan-500/30", border: "border-cyan-500", text: "text-cyan-400", dot: "bg-cyan-500" },
  { bg: "bg-orange-500/30", border: "border-orange-500", text: "text-orange-400", dot: "bg-orange-500" },
  { bg: "bg-emerald-500/30", border: "border-emerald-500", text: "text-emerald-400", dot: "bg-emerald-500" },
];

const priorityDot: Record<string, string> = {
  P0: "bg-pink-500",
  P1: "bg-orange-500",
  P2: "bg-blue-500",
  P3: "bg-slate-500",
};

type ViewMode = "week" | "month" | "quarter";
type StatusFilter = "open" | "closed";

export default function Timeline() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [viewMode, setViewMode] = useState<ViewMode>("month");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("open");
  const [priorityFilter, setPriorityFilter] = useState<string | null>(null);

  const fetchTasks = useCallback(async () => {
    try {
      const res = await api.get<TasksResponse>("/tasks");
      setTasks(res.tasks ?? []);
    } catch (e) {
      reportError('Failed to fetch tasks', e);
    }
  }, []);

  useEffect(() => {
    fetchTasks();
  }, [fetchTasks]);

  // Filter tasks
  let filtered = tasks.filter((t) => t.status === statusFilter);
  if (priorityFilter) {
    filtered = filtered.filter((t) => t.priority === priorityFilter);
  }

  // Group tasks by their goal (set by the backend from non-phase tags)
  const goalMap = new Map<string, Task[]>();
  for (const task of filtered) {
    const goal = task.goal || "Untagged";
    if (!goalMap.has(goal)) goalMap.set(goal, []);
    goalMap.get(goal)!.push(task);
  }

  const goalNames = Array.from(goalMap.keys());

  // Map task creation date to a column (day of month)
  const getCol = (dateStr: string) => {
    const d = new Date(dateStr);
    return d.getDate();
  };

  const now = new Date();
  const todayCol = now.getDate();
  const monthName = now.toLocaleDateString("en-US", { month: "long", year: "numeric" });

  const viewModeClass = (v: ViewMode) =>
    viewMode === v
      ? "px-3 py-1 rounded-md bg-slate-800 text-white font-medium"
      : "px-3 py-1 rounded-md text-slate-400 hover:text-slate-300";

  return (
    <div className="min-h-dvh bg-slate-950 text-white flex flex-col">
      <TopBar title="Timeline" />

      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:px-8 sm:pb-8 flex-1 flex flex-col">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div className="flex flex-wrap items-center gap-4 sm:gap-6">
            <h1 data-testid="page-header" className="text-xl sm:text-2xl font-bold">Timeline</h1>
            <div className="flex items-center gap-1 text-sm">
              {(["week", "month", "quarter"] as ViewMode[]).map((v) => (
                <button key={v} className={viewModeClass(v)} onClick={() => setViewMode(v)}>
                  {v.charAt(0).toUpperCase() + v.slice(1)}
                </button>
              ))}
            </div>
            <button className="px-3 py-1.5 bg-blue-500 hover:bg-blue-600 rounded-md text-sm font-medium">
              Today
            </button>
          </div>

          <div className="flex items-center gap-4">
            <div className="flex items-center gap-1 text-sm bg-slate-800 rounded-lg overflow-hidden">
              <button
                onClick={() => setStatusFilter("open")}
                className={`px-3 py-1 ${statusFilter === "open" ? "accent-bg !text-white" : "text-slate-300 hover:text-white"}`}
              >
                Open
              </button>
              <button
                onClick={() => setStatusFilter("closed")}
                className={`px-3 py-1 ${statusFilter === "closed" ? "accent-bg !text-white" : "text-slate-300 hover:text-white"}`}
              >
                Closed
              </button>
            </div>
            <div className="flex items-center gap-1 text-xs">
              {["ALL", "P0", "P1", "P2", "P3"].map((p) => (
                <button
                  key={p}
                  onClick={() => setPriorityFilter(p === "ALL" ? null : (priorityFilter === p ? null : p))}
                  className={`px-2.5 py-1 rounded-full ${
                    (p === "ALL" && !priorityFilter) || priorityFilter === p
                      ? "accent-bg !text-white"
                      : "text-slate-300 hover:text-white"
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>
            <ExportButton
              contentLabel="timeline"
              formats={["markdown"]}
              buildUrl={(format) => `/api/export/timeline?format=${format}&days=30`}
            />
          </div>
        </div>

        {/* Date nav */}
        <div className="flex items-center gap-4 mb-6">
          <button className="p-1 text-slate-400 hover:text-white">
            <Icon name="chevron_left" className="text-lg" />
          </button>
          <span className="text-sm font-medium text-slate-300">{monthName}</span>
          <button className="p-1 text-slate-400 hover:text-white">
            <Icon name="chevron_right" className="text-lg" />
          </button>
        </div>

        {/* Gantt chart */}
        <div className="flex-1 bg-slate-900/40 border border-slate-800 rounded-lg overflow-hidden">
          {/* Date headers */}
          <div
            className="grid border-b border-slate-800"
            style={{ gridTemplateColumns: `200px repeat(${COLS}, 1fr)` }}
          >
            <div className="px-4 py-2 text-xs text-slate-600 border-r border-slate-800" />
            {dates.map((d) => (
              <div
                key={d}
                className={`py-2 text-center text-xs font-mono ${
                  d === todayCol ? "text-blue-400 font-bold" : "text-slate-600"
                }`}
              >
                {d}
              </div>
            ))}
          </div>

          <div className="relative">
            {/* Today marker */}
            <div
              className="absolute top-0 bottom-0 w-px bg-blue-500 z-10"
              style={{
                left: `calc(200px + (${todayCol - 1} * ((100% - 200px) / ${COLS})) + ((100% - 200px) / ${COLS * 2}))`,
              }}
            />

            {goalNames.length === 0 && (
              <EmptyState
                icon="calendar_today"
                title="No tasks to display."
                description="Add tags to your tasks to organize them."
              />
            )}

            {goalNames.map((goalName, gi) => {
              const goalTasks = goalMap.get(goalName)!;
              const style = goalColors[gi % goalColors.length];

              // Goal bar spans from earliest to latest task creation date
              const cols = goalTasks.map((t) => getCol(t.created_at)).filter((c) => c >= 1 && c <= COLS);
              const minCol = cols.length > 0 ? Math.min(...cols) : 1;
              const maxCol = cols.length > 0 ? Math.max(...cols) + 2 : COLS; // extend 2 days past last task
              const barStart = Math.max(1, minCol);
              const barEnd = Math.min(COLS, maxCol);
              const barSpan = barEnd - barStart + 1;

              return (
                <div key={goalName}>
                  {/* Goal bar row */}
                  <div
                    className="grid border-b border-slate-800/50 items-center"
                    style={{ gridTemplateColumns: `200px repeat(${COLS}, 1fr)` }}
                  >
                    <div className="px-4 py-3 text-sm font-medium text-slate-200 truncate border-r border-slate-800 flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full ${style.dot}`} />
                      {goalName}
                      <span className="text-[10px] text-slate-500 ml-auto">{goalTasks.length}</span>
                    </div>
                    {dates.map((d) => {
                      if (d === barStart) {
                        return (
                          <div key={d} className="py-2 px-0.5" style={{ gridColumn: `span ${barSpan}` }}>
                            <div className={`h-7 rounded ${style.bg} border ${style.border} flex items-center px-2`}>
                              <span className={`text-[10px] font-bold tracking-wider ${style.text}`}>
                                {goalTasks.length} task{goalTasks.length !== 1 ? "s" : ""}
                              </span>
                            </div>
                          </div>
                        );
                      }
                      if (d > barStart && d <= barEnd) return null;
                      return <div key={d} />;
                    })}
                  </div>

                  {/* Individual task rows under this goal */}
                  {goalTasks.map((task) => {
                    const col = getCol(task.created_at);
                    return (
                      <div
                        key={task.id}
                        className="grid border-b border-slate-800/30 items-center"
                        style={{ gridTemplateColumns: `200px repeat(${COLS}, 1fr)` }}
                      >
                        <div className="px-4 py-2 text-xs text-slate-400 truncate border-r border-slate-800 pl-8">
                          {task.id} {task.title}
                        </div>
                        {dates.map((d) => (
                          <div key={d} className="flex items-center justify-center">
                            {d === col && (
                              <div
                                className={`w-3 h-3 rounded-full ${priorityDot[task.priority] || "bg-slate-500"} shadow-lg`}
                                title={`${task.id} ${task.title} (${task.priority})`}
                              />
                            )}
                          </div>
                        ))}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </div>

        {/* Footer bar */}
        <div className="mt-4 flex items-center justify-between bg-slate-900/60 border border-slate-800 rounded-lg px-4 py-2 text-xs font-mono text-slate-500">
          <span>v0.1 | GANTT_v1.0 | {now.toLocaleString().toUpperCase()}</span>
          <span>
            {filtered.length} TASK{filtered.length !== 1 ? "S" : ""} |{" "}
            {goalNames.length} GOAL{goalNames.length !== 1 ? "S" : ""} |{" "}
            <span className="text-amber-400">
              {tasks.filter((t) => t.status === "open").length} OPEN
            </span>
          </span>
        </div>
      </div>
    </div>
  );
}
