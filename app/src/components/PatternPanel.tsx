/**
 * PatternPanel — "What I learned about you" review panel (→1830 →1831 →1832).
 *
 * Displays observation clusters fetched from /api/patterns/clusters.
 * Each cluster has Confirm / Dismiss actions (tier 2 / tier 0).
 * Confirmed clusters show "Approve for silent action" (tier 3).
 * Empty state shows the placeholder per NC-5.
 */

import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";

interface Cluster {
  id: string;
  kind: string;
  label: string;
  count: number;
  last_seen: string;
  tier: number;
}

interface TierState {
  [clusterId: string]: number;
}

function kindIcon(kind: string): string {
  if (kind.startsWith("task:")) return "task_alt";
  if (kind.startsWith("vocab:")) return "spellcheck";
  return "lightbulb";
}

export default function PatternPanel() {
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [loading, setLoading] = useState(true);
  const [tiers, setTiers] = useState<TierState>({});
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState<Set<string>>(new Set());

  const fetchClusters = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get<{ clusters: Cluster[] }>("/api/patterns/clusters");
      setClusters(data.clusters ?? []);
    } catch {
      // silently degrade
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchClusters(); }, [fetchClusters]);

  const setTier = async (id: string, tier: number) => {
    setPending((p) => new Set(p).add(id));
    try {
      await api.post(`/api/patterns/clusters/${id}/tier`, { tier });
      if (tier === 0) {
        setDismissed((d) => new Set(d).add(id));
      } else {
        setTiers((t) => ({ ...t, [id]: tier }));
      }
    } catch {
      // show nothing — panel is informational only
    } finally {
      setPending((p) => { const s = new Set(p); s.delete(id); return s; });
    }
  };

  const visible = clusters.filter((c) => !dismissed.has(c.id));

  if (loading) {
    return (
      <div className="py-12 text-center text-slate-500 dark:text-slate-400 text-sm">
        Loading patterns…
      </div>
    );
  }

  if (visible.length === 0) {
    return (
      <div
        data-testid="pattern-panel-empty"
        className="py-12 text-center text-slate-500 dark:text-slate-400 text-sm px-4"
      >
        Nothing learned yet. Keep using yourOS and patterns will appear here over time.
      </div>
    );
  }

  return (
    <div data-testid="pattern-panel" className="space-y-3">
      {visible.map((c) => {
        const tier = tiers[c.id] ?? c.tier;
        const busy = pending.has(c.id);
        return (
          <div
            key={c.id}
            data-testid={`pattern-cluster-${c.id}`}
            className="rounded-lg border border-slate-200 dark:border-slate-800 bg-white/60 dark:bg-slate-900/60 p-4"
          >
            <div className="flex items-start gap-3">
              <span className="material-symbols-outlined text-slate-400 mt-0.5 text-lg select-none">
                {kindIcon(c.kind)}
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-sm text-slate-800 dark:text-slate-200 font-medium">
                  {c.label}
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                  seen {c.count}×, last {c.last_seen}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2 mt-3 ml-8">
              {tier < 2 && (
                <>
                  <button
                    data-testid={`confirm-${c.id}`}
                    disabled={busy}
                    onClick={() => setTier(c.id, 2)}
                    className="text-xs px-3 py-1 rounded-md bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 transition-colors"
                  >
                    Confirm
                  </button>
                  <button
                    data-testid={`dismiss-${c.id}`}
                    disabled={busy}
                    onClick={() => setTier(c.id, 0)}
                    className="text-xs px-3 py-1 rounded-md border border-slate-300 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 disabled:opacity-50 transition-colors"
                  >
                    Dismiss
                  </button>
                </>
              )}
              {tier === 2 && (
                <>
                  <button
                    data-testid="pattern-approve-silent"
                    disabled={busy}
                    onClick={() => setTier(c.id, 3)}
                    className="text-xs px-3 py-1 rounded-md bg-emerald-600 hover:bg-emerald-700 text-white disabled:opacity-50 transition-colors"
                  >
                    Approve for silent action
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => setTiers((t) => { const s = { ...t }; delete s[c.id]; return s; })}
                    className="text-xs px-3 py-1 rounded-md border border-slate-300 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 disabled:opacity-50 transition-colors"
                  >
                    Undo
                  </button>
                </>
              )}
              {tier === 3 && (
                <span className="text-xs text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                  <span className="material-symbols-outlined text-sm">check_circle</span>
                  Silent action enabled
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
