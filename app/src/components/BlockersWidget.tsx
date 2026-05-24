import { useState, useEffect } from 'react';
import Icon from './Icon';
import { Card, SkeletonLine } from './ui';
import { api } from '../lib/api';
import { reportError } from '../lib/reportError';

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

interface NudgeState {
  key: string;
  channel: 'slack' | 'jira';
  loading: boolean;
  result: string | null; // success message or error
  error: boolean;
}

export default function BlockersWidget() {
  const [blockers, setBlockers] = useState<Blocker[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nudgePicker, setNudgePicker] = useState<string | null>(null); // key of open picker
  const [nudgeChannel, setNudgeChannel] = useState<'slack' | 'jira'>('jira');
  const [nudgeState, setNudgeState] = useState<Record<string, NudgeState>>({});

  useEffect(() => {
    const fetchBlockers = async () => {
      try {
        setLoading(true);
        setError(null);
        const res = await api.get<BlockersResponse>('/coordination/blockers');
        setBlockers(res.blockers || []);
      } catch (err) {
        reportError('Failed to fetch blockers', err);
        setError('Not connected or failed to load');
      } finally {
        setLoading(false);
      }
    };

    fetchBlockers();
  }, []);

  const openPicker = (key: string) => {
    setNudgePicker(key);
    setNudgeChannel('jira');
  };

  const sendNudge = async (blockerKey: string) => {
    setNudgeState((prev) => ({
      ...prev,
      [blockerKey]: { key: blockerKey, channel: nudgeChannel, loading: true, result: null, error: false },
    }));
    setNudgePicker(null);
    try {
      const res = await api.post<{ success: boolean; message_id?: string; error?: string }>(
        '/coordination/nudge',
        { jira_key: blockerKey, channel: nudgeChannel }
      );
      if (res.success) {
        setNudgeState((prev) => ({
          ...prev,
          [blockerKey]: { key: blockerKey, channel: nudgeChannel, loading: false, result: 'Nudge sent', error: false },
        }));
      } else {
        setNudgeState((prev) => ({
          ...prev,
          [blockerKey]: { key: blockerKey, channel: nudgeChannel, loading: false, result: res.error || 'Failed to send nudge', error: true },
        }));
      }
    } catch {
      setNudgeState((prev) => ({
        ...prev,
        [blockerKey]: { key: blockerKey, channel: nudgeChannel, loading: false, result: 'Failed to send nudge', error: true },
      }));
    }
    // Clear feedback after 4s
    setTimeout(() => {
      setNudgeState((prev) => {
        const next = { ...prev };
        delete next[blockerKey];
        return next;
      });
    }, 4000);
  };

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
            {blockers.map((blocker) => {
              const ns = nudgeState[blocker.key];
              const isPicking = nudgePicker === blocker.key;

              return (
                <div
                  key={blocker.key}
                  data-testid={`blocker-row-${blocker.key}`}
                  className="p-2 rounded-lg hover:bg-slate-800/50 transition-colors"
                >
                  <div className="flex items-start gap-3">
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
                      aria-label={`Nudge ${blocker.key}`}
                      data-testid={`blocker-nudge-${blocker.key}`}
                      disabled={ns?.loading}
                      onClick={() => openPicker(blocker.key)}
                      className="shrink-0 px-2 py-1 rounded text-xs font-medium bg-blue-600/20 text-blue-400 hover:bg-blue-600/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {ns?.loading ? 'Sending…' : 'Send nudge'}
                    </button>
                  </div>

                  {/* Channel picker */}
                  {isPicking && (
                    <div
                      data-testid={`nudge-picker-${blocker.key}`}
                      className="mt-2 p-2 bg-slate-800 rounded-lg border border-slate-700/60"
                    >
                      <p className="text-xs text-slate-400 mb-2">Send nudge via:</p>
                      <div className="flex gap-3 mb-2">
                        <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
                          <input
                            type="radio"
                            name={`nudge-channel-${blocker.key}`}
                            value="jira"
                            checked={nudgeChannel === 'jira'}
                            onChange={() => setNudgeChannel('jira')}
                            data-testid="nudge-channel-jira"
                          />
                          Jira comment
                        </label>
                        <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
                          <input
                            type="radio"
                            name={`nudge-channel-${blocker.key}`}
                            value="slack"
                            checked={nudgeChannel === 'slack'}
                            onChange={() => setNudgeChannel('slack')}
                            data-testid="nudge-channel-slack"
                          />
                          Slack
                        </label>
                      </div>
                      <div className="flex gap-2">
                        <button
                          type="button"
                          data-testid={`nudge-confirm-${blocker.key}`}
                          onClick={() => sendNudge(blocker.key)}
                          className="px-2 py-1 rounded text-xs font-medium bg-blue-600 text-white hover:bg-blue-500 transition-colors"
                        >
                          Send
                        </button>
                        <button
                          type="button"
                          data-testid={`nudge-cancel-${blocker.key}`}
                          onClick={() => setNudgePicker(null)}
                          className="px-2 py-1 rounded text-xs font-medium bg-slate-700 text-slate-300 hover:bg-slate-600 transition-colors"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Inline result feedback */}
                  {ns?.result && (
                    <p
                      data-testid={`nudge-result-${blocker.key}`}
                      className={`mt-1 text-xs ${ns.error ? 'text-red-400' : 'text-green-400'}`}
                    >
                      {ns.result}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}


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
