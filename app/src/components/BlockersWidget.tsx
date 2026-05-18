import { useState, useEffect } from 'react';
import Icon from './Icon';
import { Card, SkeletonLine } from './ui';
import { api } from '../lib/api';

interface Blocker {
  key: string;
  summary: string;
  status: string;
  priority: string;
  url: string;
  updated: string;
  age_days: number;
  owners: string[];
}

interface BlockersResponse {
  blockers: Blocker[];
}

export default function BlockersWidget() {
  const [blockers, setBlockers] = useState<Blocker[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchBlockers = async () => {
      try {
        setLoading(true);
        setError(null);
        const res = await api.get<BlockersResponse>('/coordination/blockers');
        setBlockers(res.blockers || []);
      } catch (err) {
        console.error('Failed to fetch blockers:', err);
        setError('Not connected or failed to load');
      } finally {
        setLoading(false);
      }
    };

    fetchBlockers();
  }, []);

  return (
    <div data-testid="widget-blockers">
      <Card hover padding="sm" className="sm:p-6">
        <div className="flex items-center justify-between mb-4 pr-8">
          <div className="flex items-center gap-2">
            <Icon name="block" className="text-red-400" size={20} />
            <h2 className="text-lg font-semibold">Cross-team Blockers</h2>
          </div>
          <span className="text-xs text-slate-500" data-testid="blockers-count">
            {blockers.length} {blockers.length === 1 ? 'blocker' : 'blockers'}
          </span>
        </div>

        {loading ? (
          <div className="space-y-3">
            <SkeletonLine width="w-3/4" />
            <SkeletonLine width="w-2/3" />
          </div>
        ) : error ? (
          <p className="text-sm text-slate-500">{error}</p>
        ) : blockers.length === 0 ? (
          <p className="text-sm text-slate-500" data-testid="blockers-empty">
            No cross-team blockers right now.
          </p>
        ) : (
          <div className="space-y-3" data-testid="blockers-list">
            {blockers.map((blocker) => (
              <div
                key={blocker.key}
                data-testid={`blocker-row-${blocker.key}`}
                className="flex items-start gap-3 p-2 rounded-lg hover:bg-slate-800/50 transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[10px] font-mono text-slate-500 shrink-0">
                      {blocker.key}
                    </span>
                    <span className="flex-1 text-sm font-medium text-white truncate">
                      {blocker.summary}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-slate-400">
                    <span data-testid={`blocker-age-${blocker.key}`}>
                      {blocker.age_days}d old
                    </span>
                    {blocker.owners.length > 0 && (
                      <span data-testid={`blocker-owners-${blocker.key}`}>
                        {blocker.owners.join(', ')}
                      </span>
                    )}
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                      blocker.status === 'Blocked'
                        ? 'bg-red-500/20 text-red-400'
                        : 'bg-slate-700/60 text-slate-400'
                    }`}>
                      {blocker.status}
                    </span>
                  </div>
                </div>
                <button
                  type="button"
                  disabled
                  aria-label={`Nudge ${blocker.key}`}
                  data-testid={`blocker-nudge-${blocker.key}`}
                  className="shrink-0 px-2 py-1 rounded text-xs font-medium bg-slate-700/40 text-slate-500 cursor-not-allowed opacity-60"
                  title="Nudge coming in v2"
                >
                  Nudge
                </button>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
