import { useEffect, useState, useCallback } from "react";
import { formatTimestamp } from '../lib/time';
import { api } from "../lib/api";

export interface RuleFire {
  ts: string;
  rule: string;
  tool: string;
  decision: "allow" | "block" | string;
  reason: string;
}

export interface RuleActivityModalProps {
  open: boolean;
  ruleKey: string;
  ruleTitle: string;
  onClose: () => void;
}

type DecisionFilter = "all" | "block" | "allow";



export default function RuleActivityModal({
  open,
  ruleKey,
  ruleTitle,
  onClose,
}: RuleActivityModalProps) {
  const [fires, setFires] = useState<RuleFire[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<DecisionFilter>("all");

  // Load fires when modal opens or rule changes.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setFires(null);
    api
      .get<RuleFire[]>(`/rules/fires?rule=${encodeURIComponent(ruleKey)}&limit=20`)
      .then((rows) => {
        if (cancelled) return;
        setFires(Array.isArray(rows) ? rows : []);
        setLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setError("Couldn't load activity. Try again.");
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, ruleKey]);

  // Esc closes the modal.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  const handleBackdropClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );

  if (!open) return null;

  const filtered = (fires ?? []).filter((f) => {
    if (filter === "all") return true;
    return f.decision === filter;
  });

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="rule-activity-modal-title"
      data-testid="rule-activity-modal-backdrop"
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl w-full max-w-2xl shadow-2xl">
        <div className="flex items-start justify-between gap-4 p-5 border-b border-slate-200 dark:border-slate-800">
          <div>
            <h2
              id="rule-activity-modal-title"
              className="text-white font-semibold text-base"
            >
              Recent activity
            </h2>
            <p className="text-slate-600 dark:text-slate-400 text-sm mt-1">{ruleTitle}</p>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-slate-600 dark:text-slate-400 text-sm">
              Show
              <select
                data-testid="rule-activity-filter"
                value={filter}
                onChange={(e) => setFilter(e.target.value as DecisionFilter)}
                className="ml-2 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-white text-sm rounded px-2 py-1"
              >
                <option value="all">All</option>
                <option value="block">Blocks only</option>
                <option value="allow">Allows only</option>
              </select>
            </label>
            <button
              data-testid="rule-activity-close"
              onClick={onClose}
              className="text-slate-600 dark:text-slate-400 hover:text-white text-sm px-2 py-1 rounded transition-colors"
              aria-label="Close"
            >
              Close
            </button>
          </div>
        </div>

        <div className="p-5 max-h-[60vh] overflow-y-auto">
          {loading && (
            <div className="text-slate-500 text-sm" data-testid="rule-activity-loading">
              Loading...
            </div>
          )}
          {error && (
            <div className="text-red-600 dark:text-red-400 text-sm" data-testid="rule-activity-error">
              {error}
            </div>
          )}
          {!loading && !error && filtered.length === 0 && (
            <div
              className="text-slate-500 text-sm"
              data-testid="rule-activity-empty"
            >
              No activity yet
            </div>
          )}
          {!loading && !error && filtered.length > 0 && (
            <table className="w-full text-sm" data-testid="rule-activity-table">
              <thead>
                <tr className="text-slate-500 text-left">
                  <th className="font-normal pb-2 pr-3">When</th>
                  <th className="font-normal pb-2 pr-3">Tool</th>
                  <th className="font-normal pb-2 pr-3">Decision</th>
                  <th className="font-normal pb-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((f, idx) => (
                  <tr
                    key={`${f.ts}-${idx}`}
                    className="border-t border-slate-200 dark:border-slate-800 align-top"
                    data-testid={`rule-activity-row-${idx}`}
                  >
                    <td className="text-slate-700 dark:text-slate-300 py-2 pr-3 whitespace-nowrap">
                      {formatTimestamp(f.ts)}
                    </td>
                    <td className="text-slate-700 dark:text-slate-300 py-2 pr-3 whitespace-nowrap">
                      {f.tool}
                    </td>
                    <td className="py-2 pr-3 whitespace-nowrap">
                      <span
                        className={`inline-flex items-center gap-1.5 text-xs ${
                          f.decision === "block"
                            ? "text-red-600 dark:text-red-400"
                            : f.decision === "allow"
                              ? "text-green-600 dark:text-green-400"
                              : "text-slate-600 dark:text-slate-400"
                        }`}
                      >
                        <span
                          className={`inline-block w-2 h-2 rounded-full ${
                            f.decision === "block"
                              ? "bg-red-500"
                              : f.decision === "allow"
                                ? "bg-green-500"
                                : "bg-slate-500"
                          }`}
                        />
                        {f.decision}
                      </span>
                    </td>
                    <td className="text-slate-600 dark:text-slate-400 py-2">{f.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
