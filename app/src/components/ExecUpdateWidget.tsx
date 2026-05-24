import { useState, useEffect } from 'react';
import Icon from './Icon';
import { Card, SkeletonLine } from './ui';
import { api } from '../lib/api';
import ExecUpdateComposer from './ExecUpdateComposer';

// v2: list returns summary fields only (no markdown in list response)
interface DraftSummary {
  draft_id: string;
  audience: string;
  created_at: string;
  source_count: number;
}

interface DraftsResponse {
  drafts: DraftSummary[];
}

interface PromoteResponse {
  spec_path: string;
  task_id: string | null;
}

function formatAge(dateStr: string): string {
  try {
    const diff = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);
    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins}m ago`;
    if (hours < 24) return `${hours}h ago`;
    return `${days}d ago`;
  } catch {
    return '';
  }
}

export default function ExecUpdateWidget() {
  const [drafts, setDrafts] = useState<DraftSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [composerOpen, setComposerOpen] = useState(false);
  const [promotingId, setPromotingId] = useState<string | null>(null);
  const [promoteResult, setPromoteResult] = useState<Record<string, string>>({});

  const fetchDrafts = async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await api.get<DraftsResponse>('/narrative/drafts?limit=3');
      setDrafts((res.drafts || []).slice(0, 3));
    } catch {
      setError('Could not load progress updates');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDrafts();
  }, []);

  const handleComposerComplete = () => {
    setComposerOpen(false);
    fetchDrafts();
  };

  const handlePromote = async (draftId: string) => {
    setPromotingId(draftId);
    try {
      const res = await api.post<PromoteResponse>(`/narrative/draft/${draftId}/promote`, {});
      setPromoteResult(prev => ({
        ...prev,
        [draftId]: `Promoted to spec`,
      }));
      // suppress unused warning
      void res;
    } catch {
      setPromoteResult(prev => ({
        ...prev,
        [draftId]: 'Could not promote',
      }));
    } finally {
      setPromotingId(null);
    }
  };

  return (
    <>
      <Card className="p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Icon name="edit_note" size={18} className="text-blue-400" />
            <div>
              <h2 className="text-lg font-semibold text-white">Progress Updates</h2>
              <p className="text-xs text-slate-400">Short write-ups of your recent work to share with others.</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setComposerOpen(true)}
            className="flex items-center gap-1 px-2 py-1 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded-md transition-colors"
            aria-label="New update"
          >
            <Icon name="add" size={14} />
            New update
          </button>
        </div>

        {loading && (
          <div data-testid="exec-update-loading" className="space-y-2">
            <SkeletonLine className="h-3 w-3/4" />
            <SkeletonLine className="h-3 w-1/2" />
            <SkeletonLine className="h-3 w-2/3" />
          </div>
        )}

        {!loading && error && (
          <p data-testid="exec-update-error" className="text-sm text-red-400">
            {error}
          </p>
        )}

        {!loading && !error && drafts.length === 0 && (
          <div data-testid="exec-update-empty" className="text-center py-4">
            <p className="text-sm text-slate-400">No drafts yet.</p>
            <p className="text-xs text-slate-500 mt-1">
              Click "New update" to generate your first progress update.
            </p>
          </div>
        )}

        {!loading && !error && drafts.length > 0 && (
          <div className="space-y-3">
            {drafts.map((draft) => (
              <div
                key={draft.draft_id}
                data-testid={`draft-item-${draft.draft_id}`}
                className="border border-slate-700 rounded-lg p-3 hover:border-slate-600 transition-colors"
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-blue-300 capitalize">
                    {draft.audience}
                  </span>
                  <span className="text-xs text-slate-500">
                    {formatAge(draft.created_at)}
                  </span>
                </div>

                <p className="text-xs text-slate-400 mb-2">
                  {draft.source_count} source{draft.source_count !== 1 ? 's' : ''}
                </p>

                {promoteResult[draft.draft_id] ? (
                  <p className="text-xs text-green-400">{promoteResult[draft.draft_id]}</p>
                ) : (
                  <button
                    type="button"
                    data-testid={`promote-btn-${draft.draft_id}`}
                    onClick={() => handlePromote(draft.draft_id)}
                    disabled={promotingId === draft.draft_id}
                    className="flex items-center gap-1 px-2 py-1 text-xs bg-slate-700 hover:bg-slate-600 text-slate-200 rounded transition-colors disabled:opacity-50"
                    aria-label="Promote to spec"
                  >
                    <Icon name="upgrade" size={12} />
                    {promotingId === draft.draft_id ? 'Promoting…' : 'Promote to spec'}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      {composerOpen && (
        <ExecUpdateComposer
          onComplete={handleComposerComplete}
          onCancel={() => setComposerOpen(false)}
        />
      )}
    </>
  );
}
