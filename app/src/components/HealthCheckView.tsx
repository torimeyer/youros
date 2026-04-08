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

interface DuplicateCandidate {
  task_a: {
    id: string;
    title: string;
    priority: string;
  };
  task_b: {
    id: string;
    title: string;
    priority: string;
  };
  similarity: number;
}

interface DuplicatesResult {
  duplicates: DuplicateCandidate[];
}

const issueTypeLabels: Record<string, string> = {
  duplicate: "Duplicate title",
  no_description: "No description",
};

const issueTypeIcons: Record<string, string> = {
  duplicate: "content_copy",
  no_description: "notes",
};

const severityColors: Record<string, string> = {
  warning: "text-amber-400 bg-amber-500/10 border-amber-500/30",
  info: "text-blue-400 bg-blue-500/10 border-blue-500/30",
};

// Filter out the noisy "isolated" issue type. Singleton tasks are not
// actually problems, so we never surface them in the UI.
function filterUsefulIssues(issues: HealthIssue[]): HealthIssue[] {
  return issues.filter((i) => i.type !== "isolated");
}

type IssueFilter = "all" | "duplicate" | "no_description";

export default function HealthCheckView() {
  const [result, setResult] = useState<HealthCheckResult | null>(null);
  const [duplicates, setDuplicates] = useState<DuplicateCandidate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [issueFilter, setIssueFilter] = useState<IssueFilter>("all");

  const runHealthCheck = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [healthRes, duplicatesRes] = await Promise.all([
        api.get<HealthCheckResult>("/tasks/health"),
        api.get<DuplicatesResult>("/tasks/duplicates"),
      ]);
      setResult(healthRes);
      setDuplicates(duplicatesRes.duplicates ?? []);
    } catch (e) {
      setError("Could not run the health check. Try again in a moment.");
      console.error("Health check failed:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  const usefulIssues = result ? filterUsefulIssues(result.issues) : [];

  const filteredIssues = usefulIssues.filter(
    (i) => issueFilter === "all" || i.type === issueFilter
  );

  const issueCountByType = (type: string) =>
    usefulIssues.filter((i) => i.type === type).length;

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
            Scan your open tasks for problems like duplicates and missing
            descriptions.
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
  const totalProblems = usefulIssues.length + duplicates.length;
  const allClean = totalProblems === 0;

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
            {totalProblems}
          </div>
        </div>
        <div className="bg-slate-900/60 border border-slate-800 rounded-lg p-4">
          <div className="flex items-center gap-2 text-slate-400 text-xs mb-1">
            <Icon name="content_copy" className="text-sm" />
            Possible duplicates
          </div>
          <div className="text-2xl font-bold text-white">{duplicates.length}</div>
        </div>
        <div className="bg-slate-900/60 border border-slate-800 rounded-lg p-4">
          <div className="flex items-center gap-2 text-slate-400 text-xs mb-1">
            <Icon name="notes" className="text-sm" />
            Missing descriptions
          </div>
          <div className="text-2xl font-bold text-white">
            {issueCountByType("no_description")}
          </div>
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
              No duplicates or missing info found.
            </p>
          </div>
        </div>
      )}

      {/* Possible duplicates section (uses the real pairwise detector) */}
      {duplicates.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-xs font-medium uppercase tracking-wider text-slate-400">
            Possible duplicate pairs
          </h4>
          <div className="flex flex-col gap-2">
            {duplicates.map((pair, idx) => (
              <div
                key={`dup-${idx}`}
                className="border border-amber-500/30 bg-amber-500/10 rounded-lg px-4 py-3"
              >
                <div className="flex items-center gap-2 mb-1 text-amber-400">
                  <Icon name="content_copy" className="text-base" />
                  <span className="text-xs font-medium uppercase tracking-wider opacity-80">
                    {Math.round(pair.similarity * 100)}% similar
                  </span>
                </div>
                <div className="flex flex-col gap-1 text-sm">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-mono text-slate-400">
                      #{pair.task_a.id}
                    </span>
                    <span className="text-white">{pair.task_a.title}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-mono text-slate-400">
                      #{pair.task_b.id}
                    </span>
                    <span className="text-white">{pair.task_b.title}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {!allClean && usefulIssues.length > 0 && (
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
              All ({usefulIssues.length})
            </button>
            {(["duplicate", "no_description"] as const).map((type) => {
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
    </div>
  );
}
