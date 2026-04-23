import { useState, useCallback, useEffect } from "react";
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

interface AutofixAllResult {
  fixed: number;
  skipped: number;
  details: Array<{ issue_type: string; task_id: string; fixed: boolean; details?: string; reason?: string }>;
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

// Issue types that have auto-fix support
const FIXABLE_TYPES = new Set(["no_description", "no_labels", "no_priority"]);

// Filter out the noisy "isolated" issue type. Singleton tasks are not
// actually problems, so we never surface them in the UI.
function filterUsefulIssues(issues: HealthIssue[]): HealthIssue[] {
  return issues.filter((i) => i.type !== "isolated");
}

type IssueFilter = "all" | "duplicate" | "no_description";

interface FixState {
  status: "idle" | "running" | "success" | "error";
  message?: string;
}

export default function HealthCheckView() {
  const [result, setResult] = useState<HealthCheckResult | null>(null);
  const [duplicates, setDuplicates] = useState<DuplicateCandidate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [issueFilter, setIssueFilter] = useState<IssueFilter>("all");
  const [resolvingPair, setResolvingPair] = useState<string | null>(null);
  const [resolvingAll, setResolvingAll] = useState(false);
  const [resolveMessage, setResolveMessage] = useState<{ text: string; ok: boolean } | null>(null);

  // Per-issue fix state: key is "type:task_id"
  const [fixStates, setFixStates] = useState<Record<string, FixState>>({});
  // Fix-all state
  const [fixAllRunning, setFixAllRunning] = useState(false);
  const [fixAllMessage, setFixAllMessage] = useState<{ text: string; ok: boolean } | null>(null);
  // Confirm modal for fix-all
  const [showFixAllConfirm, setShowFixAllConfirm] = useState(false);

  // Escape closes the fix-all confirm.
  useEffect(() => {
    if (!showFixAllConfirm) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setShowFixAllConfirm(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showFixAllConfirm]);

  const runHealthCheck = useCallback(async (clearMessages = true) => {
    setLoading(true);
    setError(null);
    if (clearMessages) {
      setResolveMessage(null);
      setFixAllMessage(null);
      setFixStates({});
    }
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

  const resolvePair = useCallback(async (pair: DuplicateCandidate) => {
    const pairKey = `${pair.task_a.id}-${pair.task_b.id}`;
    setResolvingPair(pairKey);
    setResolveMessage(null);
    try {
      await api.post("/tasks/duplicates/resolve", {
        keep_id: pair.task_a.id,
        discard_id: pair.task_b.id,
      });
      setDuplicates((prev) =>
        prev.filter(
          (p) => !(p.task_a.id === pair.task_a.id && p.task_b.id === pair.task_b.id)
        )
      );
      setResolveMessage({ text: `Closed #${pair.task_b.id} as duplicate.`, ok: true });
    } catch (e: unknown) {
      const msg =
        e instanceof Error ? e.message : "Could not resolve this pair. Try again.";
      setResolveMessage({ text: msg, ok: false });
      console.error("Resolve duplicate failed:", e);
    } finally {
      setResolvingPair(null);
    }
  }, []);

  const resolveAll = useCallback(async () => {
    setResolvingAll(true);
    setResolveMessage(null);
    try {
      const res = await api.post<{ resolved: number }>("/tasks/duplicates/resolve-all", {});
      setDuplicates([]);
      setResolveMessage({
        text: `Closed ${res.resolved} duplicate task${res.resolved === 1 ? "" : "s"}.`,
        ok: true,
      });
    } catch (e: unknown) {
      const msg =
        e instanceof Error ? e.message : "Could not resolve all duplicates. Try again.";
      setResolveMessage({ text: msg, ok: false });
      console.error("Resolve all duplicates failed:", e);
    } finally {
      setResolvingAll(false);
    }
  }, []);

  const fixIssue = useCallback(async (issue: HealthIssue, taskId: string) => {
    const key = `${issue.type}:${taskId}`;
    setFixStates((prev) => ({ ...prev, [key]: { status: "running" } }));
    try {
      const res = await api.post<{ fixed: boolean; details?: string; reason?: string; fixable?: boolean }>(
        `/tasks/health/autofix/${issue.type}`,
        { task_id: taskId }
      );
      if (res.fixed) {
        setFixStates((prev) => ({
          ...prev,
          [key]: { status: "success", message: res.details },
        }));
      } else {
        setFixStates((prev) => ({
          ...prev,
          [key]: { status: "error", message: res.reason ?? "Could not fix this issue." },
        }));
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Fix failed. Try again.";
      setFixStates((prev) => ({ ...prev, [key]: { status: "error", message: msg } }));
    }
  }, []);

  const runFixAll = useCallback(async () => {
    setShowFixAllConfirm(false);
    setFixAllRunning(true);
    setFixAllMessage(null);
    try {
      const res = await api.post<AutofixAllResult>("/tasks/health/autofix-all", {});
      const { fixed, skipped } = res;
      if (fixed === 0 && skipped === 0) {
        setFixAllMessage({ text: "Nothing to fix.", ok: true });
      } else {
        setFixAllMessage({
          text: `Fixed ${fixed} issue${fixed === 1 ? "" : "s"}${skipped > 0 ? `, skipped ${skipped}` : ""}.`,
          ok: true,
        });
      }
      // Re-run the health check to reflect changes (keep fix-all message visible)
      await runHealthCheck(false);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Fix all failed. Try again.";
      setFixAllMessage({ text: msg, ok: false });
    } finally {
      setFixAllRunning(false);
    }
  }, [runHealthCheck]);

  const usefulIssues = result ? filterUsefulIssues(result.issues) : [];

  const filteredIssues = usefulIssues.filter(
    (i) => issueFilter === "all" || i.type === issueFilter
  );

  const issueCountByType = (type: string) =>
    usefulIssues.filter((i) => i.type === type).length;

  const fixableIssueCount = usefulIssues.filter(
    (i) => FIXABLE_TYPES.has(i.type) && i.task_ids.length > 0
  ).reduce((acc, i) => acc + i.task_ids.length, 0);

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-4">
        <div className="w-16 h-16 rounded-full bg-red-500/10 flex items-center justify-center">
          <Icon name="error" className="text-3xl text-red-400" />
        </div>
        <p className="text-sm text-red-300">{error}</p>
        <button
          onClick={() => runHealthCheck()}
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
          onClick={() => runHealthCheck()}
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
      {/* Confirm modal for Fix all */}
      {showFixAllConfirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          data-testid="fix-all-confirm-modal"
        >
          <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 max-w-sm w-full mx-4 space-y-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-emerald-500/10 flex items-center justify-center flex-shrink-0">
                <Icon name="auto_fix_high" className="text-xl text-emerald-400" />
              </div>
              <div>
                <h3 className="text-sm font-medium text-white">Fix all issues?</h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  This will fix {fixableIssueCount} issue{fixableIssueCount === 1 ? "" : "s"} automatically. Duplicates are not included.
                </p>
              </div>
            </div>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setShowFixAllConfirm(false)}
                className="text-xs px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={runFixAll}
                data-testid="fix-all-confirm-button"
                className="text-xs px-3 py-1.5 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white transition-colors"
              >
                Fix all
              </button>
            </div>
          </div>
        </div>
      )}

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

      {/* Rerun + Fix all buttons */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-white">
          {allClean ? "Everything looks good!" : "Issues to review"}
        </h3>
        <div className="flex items-center gap-2">
          {!allClean && fixableIssueCount > 0 && (
            <button
              onClick={() => setShowFixAllConfirm(true)}
              disabled={fixAllRunning}
              data-testid="fix-all-button"
              className="flex items-center gap-1.5 text-xs bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 hover:text-emerald-300 border border-emerald-500/30 px-2.5 py-1 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {fixAllRunning ? (
                <span className="w-3 h-3 rounded-full border border-emerald-400 border-t-transparent animate-spin" />
              ) : (
                <Icon name="auto_fix_high" className="text-sm" />
              )}
              Fix all ({fixableIssueCount})
            </button>
          )}
          <button
            onClick={() => runHealthCheck()}
            className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-300 bg-slate-800 hover:bg-slate-700 px-2.5 py-1 rounded-lg border border-slate-700 transition-colors"
          >
            <Icon name="refresh" className="text-sm" />
            Run again
          </button>
        </div>
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

      {/* Fix-all feedback banner */}
      {fixAllMessage && (
        <div
          className={`rounded-lg px-4 py-2.5 flex items-center gap-2 text-sm ${
            fixAllMessage.ok
              ? "bg-emerald-500/10 border border-emerald-500/30 text-emerald-300"
              : "bg-red-500/10 border border-red-500/30 text-red-300"
          }`}
          data-testid="fix-all-message"
        >
          <Icon name={fixAllMessage.ok ? "check_circle" : "error"} className="text-base flex-shrink-0" />
          {fixAllMessage.text}
        </div>
      )}

      {/* Resolve feedback banner */}
      {resolveMessage && (
        <div
          className={`rounded-lg px-4 py-2.5 flex items-center gap-2 text-sm ${
            resolveMessage.ok
              ? "bg-emerald-500/10 border border-emerald-500/30 text-emerald-300"
              : "bg-red-500/10 border border-red-500/30 text-red-300"
          }`}
        >
          <Icon name={resolveMessage.ok ? "check_circle" : "error"} className="text-base flex-shrink-0" />
          {resolveMessage.text}
        </div>
      )}

      {/* Possible duplicates section (uses the real pairwise detector) */}
      {duplicates.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="text-xs font-medium uppercase tracking-wider text-slate-400">
              Possible duplicate pairs
            </h4>
            <button
              onClick={resolveAll}
              disabled={resolvingAll}
              className="flex items-center gap-1.5 text-xs bg-red-500/10 hover:bg-red-500/20 text-red-400 hover:text-red-300 border border-red-500/30 px-2.5 py-1 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              data-testid="resolve-all-button"
            >
              {resolvingAll ? (
                <span className="w-3 h-3 rounded-full border border-red-400 border-t-transparent animate-spin" />
              ) : (
                <Icon name="delete_sweep" className="text-sm" />
              )}
              Resolve all
            </button>
          </div>
          <div className="flex flex-col gap-2">
            {duplicates.map((pair, idx) => {
              const pairKey = `${pair.task_a.id}-${pair.task_b.id}`;
              const isResolving = resolvingPair === pairKey;
              return (
                <div
                  key={`dup-${idx}`}
                  className="border border-amber-500/30 bg-amber-500/10 rounded-lg px-4 py-3"
                >
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2 text-amber-400">
                      <Icon name="content_copy" className="text-base" />
                      <span className="text-xs font-medium uppercase tracking-wider opacity-80">
                        {Math.round(pair.similarity * 100)}% similar
                      </span>
                    </div>
                    <button
                      onClick={() => resolvePair(pair)}
                      disabled={isResolving || resolvingAll}
                      className="flex items-center gap-1 text-xs bg-red-500/10 hover:bg-red-500/20 text-red-400 hover:text-red-300 border border-red-500/30 px-2 py-0.5 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                      data-testid={`resolve-pair-${idx}`}
                    >
                      {isResolving ? (
                        <span className="w-3 h-3 rounded-full border border-red-400 border-t-transparent animate-spin" />
                      ) : (
                        <Icon name="close" className="text-xs" />
                      )}
                      Resolve
                    </button>
                  </div>
                  <div className="flex flex-col gap-1 text-sm">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-slate-400">
                        #{pair.task_a.id}
                      </span>
                      <span className="text-white">{pair.task_a.title}</span>
                      <span className="ml-auto text-xs text-emerald-400 font-medium">keep</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-slate-400">
                        #{pair.task_b.id}
                      </span>
                      <span className="text-white">{pair.task_b.title}</span>
                      <span className="ml-auto text-xs text-red-400 font-medium">close</span>
                    </div>
                  </div>
                </div>
              );
            })}
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
                className={`border rounded-lg px-4 py-3 ${
                  severityColors[issue.severity] ?? severityColors.info
                }`}
              >
                <div className="flex items-start gap-3">
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
                      <div className="flex items-center gap-1.5 mt-1 flex-wrap">
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
                    {/* Per-task fix buttons and feedback */}
                    {FIXABLE_TYPES.has(issue.type) && issue.task_ids.length > 0 && (
                      <div className="flex items-center gap-2 mt-2 flex-wrap">
                        {issue.task_ids.map((tid) => {
                          const key = `${issue.type}:${tid}`;
                          const fixState = fixStates[key] ?? { status: "idle" };
                          if (fixState.status === "success") {
                            return (
                              <span
                                key={tid}
                                className="flex items-center gap-1 text-xs text-emerald-400"
                                data-testid={`fix-success-${issue.type}-${tid}`}
                              >
                                <Icon name="check_circle" className="text-sm" />
                                Fixed
                              </span>
                            );
                          }
                          if (fixState.status === "error") {
                            return (
                              <span
                                key={tid}
                                className="flex items-center gap-1 text-xs text-red-400"
                                data-testid={`fix-error-${issue.type}-${tid}`}
                                title={fixState.message}
                              >
                                <Icon name="error" className="text-sm" />
                                {fixState.message ?? "Fix failed"}
                              </span>
                            );
                          }
                          return (
                            <button
                              key={tid}
                              onClick={() => fixIssue(issue, tid)}
                              disabled={fixState.status === "running"}
                              data-testid={`fix-button-${issue.type}-${tid}`}
                              className="flex items-center gap-1 text-xs bg-white/10 hover:bg-white/20 px-2 py-0.5 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              {fixState.status === "running" ? (
                                <span className="w-3 h-3 rounded-full border border-current border-t-transparent animate-spin" />
                              ) : (
                                <Icon name="auto_fix_high" className="text-xs" />
                              )}
                              Fix #{tid}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
