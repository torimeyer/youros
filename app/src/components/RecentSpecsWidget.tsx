import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import Icon from './Icon';
import { Card, SkeletonLine } from './ui';
import { api } from '../lib/api';
import { formatRelative } from '../lib/time';

interface Spec {
  path: string;
  filename: string;
  title: string;
  status: 'draft' | 'ready' | 'in-progress' | 'complete' | 'plan';
  created_at: string;
  promoted_at: string;
  task_ids?: string[];
}

interface RecentSpecsResponse {
  docs: Spec[];
}

export default function RecentSpecsWidget() {
  const navigate = useNavigate();
  const [specs, setSpecs] = useState<Spec[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchRecentSpecs = async () => {
      try {
        setLoading(true);
        setError(null);
        const res = await api.get<RecentSpecsResponse>('/specs/recent');
        setSpecs(res.docs || []);
      } catch (err) {
        console.error('Failed to fetch recent specs:', err);
        setError('Failed to load specs');
      } finally {
        setLoading(false);
      }
    };

    fetchRecentSpecs();
  }, []);

  const handleSpecClick = (spec: Spec) => {
    if (spec.status === 'plan') {
      const needleId = spec.task_ids?.[0];
      if (needleId) {
        navigate(`/tasks?highlight=${encodeURIComponent(`→${needleId}`)}`);
      } else {
        navigate('/tasks');
      }
    } else {
      navigate(`/specs?highlight=${encodeURIComponent(spec.path)}`);
    }
  };

  return (
    <div data-testid="widget-recent-specs">
      <Card hover padding="sm" className="sm:p-6">
        <div className="flex items-center gap-2 mb-4 pr-8">
          <Icon name="description" className="text-purple-400" size={20} />
          <h2 className="text-lg font-semibold">Recent Specs</h2>
        </div>

        {loading ? (
          <div className="space-y-3">
            <SkeletonLine width="w-3/4" />
            <SkeletonLine width="w-2/3" />
            <SkeletonLine width="w-3/4" />
          </div>
        ) : error ? (
          <p className="text-sm text-slate-400">{error}</p>
        ) : specs.length === 0 ? (
          <div className="flex items-center gap-3 py-6">
            <div className="w-10 h-10 rounded-full bg-slate-800 flex items-center justify-center shrink-0">
              <Icon name="assignment" className="text-slate-500" size={18} />
            </div>
            <p className="text-sm text-slate-400">No specs yet</p>
          </div>
        ) : (
          <div className="space-y-2">
            {specs.map((spec) => {
              const timestamp = spec.created_at || spec.promoted_at || '';
              const timeLabel = formatRelative(timestamp);
              const isPlan = spec.status === 'plan';

              return (
                <button
                  key={spec.path}
                  onClick={() => handleSpecClick(spec)}
                  className="w-full text-left flex items-start gap-3 p-3 rounded-lg hover:bg-slate-800/50 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500/50"
                >
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5 ${isPlan ? 'bg-blue-500/20' : 'bg-purple-500/20'}`}>
                    <Icon name={isPlan ? 'task_alt' : 'description'} className={isPlan ? 'text-blue-400' : 'text-purple-400'} size={16} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-white truncate">
                      {isPlan ? `Plan: ${spec.title}` : spec.title}
                    </p>
                    <p className="text-xs text-slate-400 mt-0.5">{timeLabel}</p>
                  </div>
                  <Icon name="chevron_right" className="text-slate-500 shrink-0 mt-0.5" size={18} />
                </button>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
