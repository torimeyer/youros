import { useState, useEffect, useCallback } from "react";
import Icon from "./Icon";
import { api } from "../lib/api";
import { reportError } from '../lib/reportError';

interface Task {
  id: string;
  title: string;
  priority: string;
  status: string;
  created_at: string;
  goal?: string | null;
}

interface Goal {
  name: string;
  total: number;
  closed: number;
  open: number;
  progress: number;
  tasks: Task[];
}

interface GoalsResponse {
  goals: Goal[];
}

const priorityStyles: Record<string, string> = {
  P0: "bg-pink-500/20 text-pink-500",
  P1: "bg-orange-500/20 text-orange-500",
  P2: "bg-blue-500/20 text-blue-500",
  P3: "bg-slate-500/20 text-slate-400",
};

function progressColor(pct: number): string {
  if (pct === 100) return "bg-green-500";
  if (pct >= 75) return "bg-emerald-400";
  if (pct >= 50) return "bg-blue-400";
  if (pct >= 25) return "bg-amber-400";
  return "bg-orange-400";
}

function progressTextColor(pct: number): string {
  if (pct === 100) return "text-green-400";
  if (pct >= 75) return "text-emerald-400";
  if (pct >= 50) return "text-blue-400";
  if (pct >= 25) return "text-amber-400";
  return "text-orange-400";
}

export default function GoalsView() {
  const [goals, setGoals] = useState<Goal[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedGoals, setExpandedGoals] = useState<Set<string>>(new Set());

  const fetchGoals = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get<GoalsResponse>("/goals");
      setGoals(res.goals ?? []);
    } catch (e) {
      reportError('Failed to fetch goals', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchGoals();
  }, [fetchGoals]);

  const toggleGoal = (name: string) => {
    setExpandedGoals((prev) => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  };

  const totalTasks = goals.reduce((sum, g) => sum + g.total, 0);
  const totalClosed = goals.reduce((sum, g) => sum + g.closed, 0);
  const overallProgress = totalTasks > 0 ? Math.round((totalClosed / totalTasks) * 100) : 0;

  if (loading && goals.length === 0) {
    return <p className="text-sm text-slate-500 py-4">Loading goals...</p>;
  }

  if (!loading && goals.length === 0) {
    return (
      <div className="text-center py-12">
        <Icon name="flag" className="text-4xl text-slate-600 mb-3" />
        <p className="text-slate-400 text-sm">No goals yet.</p>
        <p className="text-slate-500 text-xs mt-1">
          Goals are created automatically from needle tags.
        </p>
      </div>
    );
  }

  return (
    <div>
      {/* Overall summary */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-lg px-5 py-4 mb-6">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium text-slate-300">Overall progress</span>
            <span className={`text-lg font-bold ${progressTextColor(overallProgress)}`}>
              {overallProgress}%
            </span>
          </div>
          <div className="flex items-center gap-4 text-xs text-slate-400">
            <span>
              <span className="text-white font-medium">{goals.length}</span> goals
            </span>
            <span>
              <span className="text-white font-medium">{totalClosed}</span>/{totalTasks} needles done
            </span>
          </div>
        </div>
        <div className="w-full bg-slate-800 rounded-full h-2">
          <div
            className={`h-2 rounded-full transition-all duration-500 ${progressColor(overallProgress)}`}
            style={{ width: `${overallProgress}%` }}
          />
        </div>
      </div>

      {/* Goal cards */}
      <div className="flex flex-col gap-3">
        {goals.map((goal) => {
          const expanded = expandedGoals.has(goal.name);
          return (
            <div
              key={goal.name}
              className="bg-slate-900/60 border border-slate-800 rounded-lg overflow-hidden hover:border-slate-700 transition-colors"
            >
              {/* Goal header */}
              <button
                onClick={() => toggleGoal(goal.name)}
                className="w-full px-5 py-4 flex items-center gap-4 text-left"
              >
                <Icon
                  name={expanded ? "expand_more" : "chevron_right"}
                  className="text-slate-500 text-lg flex-shrink-0"
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3 mb-2">
                    <span className="text-sm font-medium text-white">{goal.name}</span>
                    {goal.progress === 100 && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-500/20 text-green-400 font-medium">
                        COMPLETE
                      </span>
                    )}
                  </div>
                  <div className="w-full bg-slate-800 rounded-full h-1.5">
                    <div
                      className={`h-1.5 rounded-full transition-all duration-500 ${progressColor(goal.progress)}`}
                      style={{ width: `${goal.progress}%` }}
                    />
                  </div>
                </div>
                <div className="flex items-center gap-4 flex-shrink-0 text-xs">
                  <span className={`font-bold ${progressTextColor(goal.progress)}`}>
                    {Math.round(goal.progress)}%
                  </span>
                  <div className="flex items-center gap-2 text-slate-400">
                    <span>
                      <span className="text-green-400 font-medium">{goal.closed}</span> done
                    </span>
                    <span className="text-slate-600">/</span>
                    <span>
                      <span className="text-slate-300 font-medium">{goal.open}</span> open
                    </span>
                  </div>
                </div>
              </button>

              {/* Expanded task list */}
              {expanded && (
                <div className="border-t border-slate-800 px-5 py-3">
                  {goal.tasks.length === 0 ? (
                    <p className="text-sm text-slate-500 py-2">No needles in this goal.</p>
                  ) : (
                    <div className="flex flex-col gap-1.5">
                      {goal.tasks.map((task) => (
                        <div
                          key={task.id}
                          className="flex items-center gap-3 py-1.5 px-2 rounded hover:bg-slate-800/50"
                        >
                          <span
                            className={`w-4 h-4 rounded-full border-2 flex-shrink-0 flex items-center justify-center ${
                              task.status === "closed"
                                ? "border-green-500 bg-green-500/20"
                                : "border-slate-600"
                            }`}
                          >
                            {task.status === "closed" && (
                              <Icon name="check" className="text-green-400 text-[10px]" />
                            )}
                          </span>
                          <span className="text-slate-500 text-xs font-mono">#{task.id}</span>
                          <span
                            className={`text-sm flex-1 ${
                              task.status === "closed" ? "line-through text-slate-500" : "text-slate-300"
                            }`}
                          >
                            {task.title}
                          </span>
                          <span
                            className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${
                              priorityStyles[task.priority] ?? "bg-slate-500/20 text-slate-400"
                            }`}
                          >
                            {task.priority}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
