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
        <div className="flex items-center justify-between mb-4 pr-2">
          <div className="flex items-center gap-2">
            <Icon name="block" className="text-orange-400" size={20} />
            <h2 className="text-lg font-semibold">Cross-team Blockers</h2>
          </div>
          <span className="text-xs text-slate-500">
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
          <p className="text-sm text-slate-500">No blockers right now.</p>
        ) : (
          <div className="space-y-2">
            {blockers.map((blocker) => (
              <div
                key={blocker.key}
                className="flex items-start gap-3 p-2 rounded-lg bg-slate-800/40"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <a
                      href={blocker.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[10px] font-mono text-slate-500 hover:text-slate-300 shrink-0"
                    >
                      {blocker.key}
                    </a>
                    <span className="flex-1 text-sm font-medium text-white truncate">
                      {blocker.summary}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-slate-400">
                    <span>{blocker.age_days}d old</span>
                    {blocker.owners.length > 0 && (
                      <span className="truncate">{blocker.owners.join(', ')}</span>
                    )}
                  </div>
                </div>
                <button
                  disabled
                  title="Nudge via Slack or Jira — coming in v2"
                  aria-label="Nudge"
                  className="shrink-0 px-2 py-1 text-xs rounded-md bg-slate-700/60 text-slate-500 cursor-not-allowed opacity-50"
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
