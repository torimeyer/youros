import { useState, useCallback } from "react";
import Icon from "./Icon";
import { api } from "../lib/api";

interface HealthIssue {
  type: string;
  severity: string;
  message: string;
  task_ids: string[];
}

interface RefinedTask {
  id: string;
  priority: string;
  status: string;
  title: string;
  sphere: { id: number; size: number; point: string } | null;
  degree: number;
  joints: { id: string; title: string }[];
}

interface HealthSummary {
  total: number;
  issues: number;
  connected: number;
  isolated: number;
}

interface HealthCheckResult {
  tasks: RefinedTask[];
  issues: HealthIssue[];
  summary: HealthSummary;
}

const issueTypeLabels: Record<string, string> = {
  duplicate: "Duplicate title",
  no_description: "No description",
  isolated: "No linked tasks",
};

const issueTypeIcons: Record<string, string> = {
  duplicate: "content_copy",
  no_description: "notes",
  isolated: "link_off",
};

const severityColors: Record<string, string> = {
  warning: "text-amber-400 bg-amber-500/10 border-amber-500/30",
  info: "text-blue-400 bg-blue-500/10 border-blue-500/30",
};

type IssueFilter = "all" | "duplicate" | "no_description" | "isolated";

export default function HealthCheckView() {
  const [result, setResult] = useState<HealthCheckResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [issueFilter, setIssueFilter] = useState<IssueFilter>("all");

  const runHealthCheck = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get<HealthCheckResult>("/tasks/health");
      setResult(res);
    } catch (e) {
      setError("Could not run the health check. Try again in a moment.");
      console.error("Health check failed:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  const filteredIssues = result?.issues.filter(
    (i) => issueFilter === "all" || i.type === issueFilter
  ) ?? [];

  const issueCountByType = (type: string) =>
    result?.issues.filter((i) => i.type === type).length ?? 0;

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-4">
        <div className="w-16 h-16 rounded-full bg-red-500/10 flex items-center justify-center">
          <Icon name="error" className="text-3xl text-red-400" />
        </div>
        <p className="text-sm text-red-300">{error}</p>
        <button
          onClick={runHealthCheck}
          className="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 text-sm px-3 py-1.5 rounded-lg border border-slate-700"
        >
          Try again
        </button>
      </div>
    );
  }

  if (!result && !loading) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-4">
        <div className="w-16 h-16 rounded-full bg-emerald-500/10 flex items-center justify-center">
          <Icon name="health_and_safety" className="text-3xl text-emerald-400" />
        </div>
        <div className="text-center">
          <h2 className="text-lg font-medium text-white mb-1">Task Health Check</h2>
          <p className="text-sm text-slate-400 max-w-md">
            Scan your open tasks for problems like duplicates, missing descriptions,
            and tasks with no connections to other work.
          </p>
        </div>
        <button
          onClick={runHealthCheck}
          className="flex items-center gap-2 bg-emerald-500 hover:bg-emerald-600 text-white text-sm px-4 py-2 rounded-lg transition-colors"
        >
          <Icon name="play_arrow" className="text-base" />
          Run Health Check
        </button>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-3">
        <div className="w-10 h-10 rounded-full border-2 border-emerald-500 border-t-transparent animate-spin" />
        <p className="text-sm text-slate-400">Checking task quality...</p>
      </div>
    );
  }

  if (!result) return null;

  const { summary } = result;
  const allClean = summary.issues === 0;

  return (
    <div className="space-y-6">
      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="bg-slate-900/60 border border-slate-800 rounded-lg p-4">
          <div className="flex items-center gap-2 text-slate-400 text-xs mb-1">
            <Icon name="task_alt" className="text-sm" />
            Tasks checked
          </div>
          <div className="text-2xl font-bold text-white">{summary.total}</div>
        </div>
        <div className={`bg-slate-900/60 border rounded-lg p-4 ${
          allClean ? "border-emerald-500/30" : "border-amber-500/30"
        }`}>
          <div className="flex items-center gap-2 text-xs mb-1">
            <Icon
              name={allClean ? "check_circle" : "warning"}
              className={`text-sm ${allClean ? "text-emerald-400" : "text-amber-400"}`}
            />
            <span className={allClean ? "text-emerald-400" : "text-amber-400"}>
              Issues found
            </span>
          </div>
          <div className={`text-2xl font-bold ${allClean ? "text-emerald-400" : "text-amber-400"}`}>
            {summary.issues}
          </div>
        </div>
        <div className="bg-slate-900/60 border border-slate-800 rounded-lg p-4">
          <div className="flex items-center gap-2 text-slate-400 text-xs mb-1">
            <Icon name="link" className="text-sm" />
            Connected
          </div>
          <div className="text-2xl font-bold text-white">{summary.connected}</div>
        </div>
        <div className="bg-slate-900/60 border border-slate-800 rounded-lg p-4">
          <div className="flex items-center gap-2 text-slate-400 text-xs mb-1">
            <Icon name="link_off" className="text-sm" />
            Isolated
          </div>
          <div className="text-2xl font-bold text-white">{summary.isolated}</div>
        </div>
      </div>

      {/* Rerun button */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-white">
          {allClean ? "Everything looks good!" : "Issues to review"}
        </h3>
        <button
          onClick={runHealthCheck}
          className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-300 bg-slate-800 hover:bg-slate-700 px-2.5 py-1 rounded-lg border border-slate-700 transition-colors"
        >
          <Icon name="refresh" className="text-sm" />
          Run again
        </button>
      </div>

      {allClean && (
        <div className="bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-4 flex items-center gap-3">
          <Icon name="check_circle" className="text-emerald-400 text-xl" />
          <div>
            <p className="text-sm text-emerald-300 font-medium">All clear</p>
            <p className="text-xs text-emerald-400/70">
              No duplicates, missing info, or disconnected tasks found.
            </p>
          </div>
        </div>
      )}

      {!allClean && (
        <>
          {/* Issue type filter */}
          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={() => setIssueFilter("all")}
              className={`px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                issueFilter === "all"
                  ? "bg-slate-700 text-white"
                  : "text-slate-400 hover:text-slate-300"
              }`}
            >
              All ({summary.issues})
            </button>
            {(["duplicate", "no_description", "isolated"] as const).map((type) => {
              const count = issueCountByType(type);
              if (count === 0) return null;
              return (
                <button
                  key={type}
                  onClick={() => setIssueFilter(issueFilter === type ? "all" : type)}
                  className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-colors ${
                    issueFilter === type
                      ? "bg-slate-700 text-white"
                      : "text-slate-400 hover:text-slate-300"
                  }`}
                >
                  <Icon name={issueTypeIcons[type]} className="text-xs" />
                  {issueTypeLabels[type]} ({count})
                </button>
              );
            })}
          </div>

          {/* Issues list */}
          <div className="flex flex-col gap-2">
            {filteredIssues.map((issue, idx) => (
              <div
                key={`${issue.type}-${idx}`}
                className={`border rounded-lg px-4 py-3 flex items-start gap-3 ${
                  severityColors[issue.severity] ?? severityColors.info
                }`}
              >
                <Icon
                  name={issueTypeIcons[issue.type] ?? "info"}
                  className="text-lg mt-0.5 flex-shrink-0"
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-xs font-medium uppercase tracking-wider opacity-70">
                      {issueTypeLabels[issue.type] ?? issue.type}
                    </span>
                  </div>
                  <p className="text-sm">{issue.message}</p>
                  {issue.task_ids.length > 0 && (
                    <div className="flex items-center gap-1.5 mt-1">
                      {issue.task_ids.map((tid) => (
                        <span
                          key={tid}
                          className="text-xs font-mono px-1.5 py-0.5 rounded bg-white/10"
                        >
                          #{tid}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Connected tasks (collapsed by default) */}
      {result.tasks.some((t) => t.degree > 0) && (
        <details className="group">
          <summary className="text-sm text-slate-400 hover:text-slate-300 cursor-pointer flex items-center gap-1.5">
            <Icon name="expand_more" className="text-base group-open:rotate-180 transition-transform" />
            Connected tasks ({summary.connected})
          </summary>
          <div className="mt-3 flex flex-col gap-2">
            {result.tasks
              .filter((t) => t.degree > 0)
              .map((task) => (
                <div
                  key={task.id}
                  className="bg-slate-900/60 border border-slate-800 rounded-lg px-4 py-3"
                >
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-mono text-slate-500">#{task.id}</span>
                    <span className="text-sm text-white">{task.title}</span>
                    <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${
                      priorityStyle(task.priority)
                    }`}>
                      {task.priority}
                    </span>
                  </div>
                  <div className="flex items-center gap-1 flex-wrap">
                    <span className="text-xs text-slate-500">
                      Linked to {task.degree} {task.degree === 1 ? "task" : "tasks"}:
                    </span>
                    {task.joints.map((j) => (
                      <span
                        key={j.id}
                        className="text-xs text-slate-400 bg-slate-800 px-1.5 py-0.5 rounded"
                      >
                        #{j.id} {j.title}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
          </div>
        </details>
      )}
    </div>
  );
}

function priorityStyle(p: string): string {
  const map: Record<string, string> = {
    P0: "bg-pink-500/20 text-pink-500",
    P1: "bg-orange-500/20 text-orange-500",
    P2: "bg-blue-500/20 text-blue-500",
  };
  return map[p] ?? "bg-slate-500/20 text-slate-400";
}
