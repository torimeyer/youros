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
  due?: string;
}

interface JiraIssuesResponse {
  issues: JiraIssue[];
}

type Group = 'To do' | 'In progress' | 'In review';
const GROUP_ORDER: Group[] = ['To do', 'In progress', 'In review'];

export function compareAttention(a: JiraIssue, b: JiraIssue, now = new Date()): number {
  const score = (i: JiraIssue): number => {
    if (i.due && new Date(i.due) < now) return 0;
    if (i.priority === 'Highest' || i.priority === 'High') return 1;
    if (now.getTime() - new Date(i.updated).getTime() > 7 * 24 * 60 * 60 * 1000) return 2;
    return 3;
  };
  const diff = score(a) - score(b);
  if (diff !== 0) return diff;
  return new Date(b.updated).getTime() - new Date(a.updated).getTime();
}

export function formatDueLabel(due: string, now = new Date()): string {
  if (!due) return '';
  const parts = due.split('T')[0].split('-').map(Number);
  const dueMs = Date.UTC(parts[0], parts[1] - 1, parts[2]);
  const todayMs = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const diffDays = Math.round((dueMs - todayMs) / 86400000);
  if (diffDays < 0) {
    const n = -diffDays;
    return `overdue ${n} day${n === 1 ? '' : 's'}`;
  }
  if (diffDays === 0) return 'due today';
  if (diffDays === 1) return 'due tomorrow';
  return `due in ${diffDays} days`;
}

function getGroup(status: string): Group {
  if (status.toLowerCase().includes('review')) return 'In review';
  if (status === 'In Progress') return 'In progress';
  return 'To do';
}

export default function JiraWidget() {
  const navigate = useNavigate();
  const [issues, setIssues] = useState<JiraIssue[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [promoteState, setPromoteState] = useState<Record<string, 'idle' | 'added' | 'error'>>({});

  useEffect(() => {
    const fetchIssues = async () => {
      try {
        setLoading(true);
        setError(null);
        const res = await api.get<JiraIssuesResponse>('/atlassian/jira/issues');
        const sorted = [...(res.issues || [])].sort(compareAttention);
        setIssues(sorted.slice(0, 5));
      } catch (err) {
        reportError('Failed to fetch jira issues', err);
        setError('Not connected or failed to load');
      } finally {
        setLoading(false);
      }
    };
    fetchIssues();
  }, []);

  const now = new Date();
  const overdueCount = issues.filter(i => i.due && new Date(i.due) < now).length;

  const grouped = GROUP_ORDER
    .map(group => ({ group, items: issues.filter(i => getGroup(i.status) === group) }))
    .filter(g => g.items.length > 0);

  return (
    <div data-testid="widget-jira">
      <Card hover padding="sm" className="sm:p-6" onClick={() => navigate('/jira')}>
        <div className="flex items-center justify-between mb-4 pr-8">
          <div className="flex items-center gap-2">
            <Icon name="bug_report" className="text-blue-600 dark:text-blue-400" size={20} />
            <h2 className="text-lg font-semibold">Jira Issues</h2>
          </div>
          {overdueCount > 0 ? (
            <span className="text-xs text-red-500">{overdueCount} overdue</span>
          ) : (
            <span className="text-xs text-slate-500">{issues.length} assigned</span>
          )}
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
          <div className="space-y-3">
            {grouped.map(({ group, items }) => (
              <div key={group}>
                <p className="text-[10px] font-medium text-slate-400 uppercase tracking-wider mb-1">{group}</p>
                <div className="space-y-1">
                  {items.map((issue) => {
                    const dueLabel = issue.due ? formatDueLabel(issue.due, now) : '';
                    const pState = promoteState[issue.key] ?? 'idle';
                    return (
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
                          {dueLabel && (
                            <span className="ml-1 text-[10px] font-normal text-slate-400">{dueLabel}</span>
                          )}
                        </span>
                        <button
                          aria-label={`Add ${issue.key} to my tasks`}
                          onClick={async (e) => {
                            e.stopPropagation();
                            try {
                              await api.post('/atlassian/jira/promote', { key: issue.key });
                              setPromoteState(prev => ({ ...prev, [issue.key]: 'added' }));
                            } catch {
                              setPromoteState(prev => ({ ...prev, [issue.key]: 'error' }));
                              setTimeout(() => {
                                setPromoteState(prev => ({ ...prev, [issue.key]: 'idle' }));
                              }, 3000);
                            }
                          }}
                          disabled={pState === 'added'}
                          className="text-[10px] px-1.5 py-0.5 rounded border border-slate-600 text-slate-400 hover:text-slate-200 hover:border-slate-400 transition-colors shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          {pState === 'added' ? 'Added' : pState === 'error' ? "Couldn't add" : 'Add to my tasks'}
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
