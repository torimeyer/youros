import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import Icon from './Icon';
import { Card, SkeletonLine } from './ui';
import { api } from '../lib/api';
import { reportError } from '../lib/reportError';

interface JiraIssue {
  key: string;
  summary: string;
  status: string;
  priority: string;
  type: string;
  updated: string;
  url: string;
}

interface JiraIssuesResponse {
  issues: JiraIssue[];
}

export default function JiraWidget() {
  const navigate = useNavigate();
  const [issues, setIssues] = useState<JiraIssue[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchIssues = async () => {
      try {
        setLoading(true);
        setError(null);
        const res = await api.get<JiraIssuesResponse>('/atlassian/jira/issues');
        setIssues((res.issues || []).slice(0, 5));
      } catch (err) {
        reportError('Failed to fetch jira issues', err);
        setError('Not connected or failed to load');
      } finally {
        setLoading(false);
      }
    };

    fetchIssues();
  }, []);

  return (
    <div data-testid="widget-jira">
      <Card hover padding="sm" className="sm:p-6" onClick={() => navigate('/jira')}>
        <div className="flex items-center justify-between mb-4 pr-8">
          <div className="flex items-center gap-2">
            <Icon name="bug_report" className="text-blue-600 dark:text-blue-400" size={20} />
            <h2 className="text-lg font-semibold">Jira Issues</h2>
          </div>
          <span className="text-xs text-slate-500">{issues.length} assigned</span>
        </div>

        {loading ? (
          <div className="space-y-3">
            <SkeletonLine width="w-3/4" />
            <SkeletonLine width="w-2/3" />
          </div>
        ) : error ? (
          <p className="text-sm text-slate-500">{error}</p>
        ) : issues.length === 0 ? (
          <p className="text-sm text-slate-500">No issues assigned to you.</p>
        ) : (
          <div className="space-y-2">
            {issues.map((issue) => (
              <div
                key={issue.key}
                onClick={(e) => {
                  e.stopPropagation();
                  navigate(`/jira/${issue.key}`);
                }}
                className="flex items-center gap-3 p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800/50 transition-colors cursor-pointer"
              >
                <span className="text-[10px] font-mono text-slate-500 shrink-0">{issue.key}</span>
                <span className="flex-1 text-sm font-medium text-white truncate">
                  {issue.summary}
                </span>
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium shrink-0 ${
                  issue.status === 'In Progress' ? 'bg-blue-500/20 text-blue-600 dark:text-blue-400' : 'bg-slate-100/60 dark:bg-slate-700/60 text-slate-600 dark:text-slate-400'
                }`}>
                  {issue.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
