import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import Icon from './Icon';
import { Card, SkeletonLine } from './ui';
import { api } from '../lib/api';

export const MENTIONS_CQL =
  'mention = currentUser() AND creator != currentUser() ORDER BY lastmodified DESC';

interface Task {
  id: string;
  text: string;
  due: string;
  page_id: string;
  url: string;
}

interface MentionRow {
  id: string;
  title: string;
  type: string;
  updated: string;
  url: string;
}

function dueLabel(due: string): string {
  const datePart = due.split('T')[0];
  const [y, m, d] = datePart.split('-').map(Number);
  const dueMs = new Date(y, m - 1, d).setHours(0, 0, 0, 0);
  const todayMs = new Date().setHours(0, 0, 0, 0);
  const diffDays = Math.round((dueMs - todayMs) / 86400000);
  if (diffDays === 0) return 'due today';
  if (diffDays === 1) return 'due tomorrow';
  if (diffDays > 1) return `due in ${diffDays} days`;
  return `overdue ${Math.abs(diffDays)} days`;
}

export default function ConfluenceWidget() {
  const navigate = useNavigate();

  const [tasks, setTasks] = useState<Task[]>([]);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [tasksError, setTasksError] = useState<string | null>(null);

  const [mentions, setMentions] = useState<MentionRow[]>([]);
  const [mentionsLoading, setMentionsLoading] = useState(true);
  const [mentionsError, setMentionsError] = useState<string | null>(null);

  const [hiddenTaskIds, setHiddenTaskIds] = useState<Set<string>>(new Set());
  const [taskErrors, setTaskErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    api
      .get<{ tasks: Task[] }>('/atlassian/confluence/my-tasks')
      .then((res) => {
        setTasks(res.tasks || []);
        setTasksLoading(false);
      })
      .catch(() => {
        setTasksError('Not connected or failed to load');
        setTasksLoading(false);
      });

    api
      .get<{ rows: MentionRow[] }>(
        `/atlassian/confluence/query?cql=${encodeURIComponent(MENTIONS_CQL)}&limit=5`
      )
      .then((res) => {
        setMentions(res.rows || []);
        setMentionsLoading(false);
      })
      .catch(() => {
        setMentionsError('Not connected or failed to load');
        setMentionsLoading(false);
      });
  }, []);

  function handleCheck(task: Task) {
    setHiddenTaskIds((prev) => new Set([...prev, task.id]));
    api
      .post<{ ok: boolean }>(`/atlassian/confluence/task/${task.id}/complete`)
      .catch(() => {
        setHiddenTaskIds((prev) => {
          const next = new Set(prev);
          next.delete(task.id);
          return next;
        });
        setTaskErrors((prev) => ({
          ...prev,
          [task.id]: "Couldn't check that off. It may have changed in Confluence.",
        }));
      });
  }

  const visibleTasks = tasks.filter((t) => !hiddenTaskIds.has(t.id));

  return (
    <div data-testid="widget-confluence">
      <Card hover padding="sm" className="sm:p-6" onClick={() => navigate('/confluence')}>
        <div className="flex items-center justify-between mb-4 pr-8">
          <div className="flex items-center gap-2">
            <Icon name="menu_book" className="text-emerald-600 dark:text-emerald-400" size={20} />
            <h2 className="text-lg font-semibold">Confluence</h2>
          </div>
          {tasks.length > 0 && (
            <span className="text-xs bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300 rounded-full px-2 py-0.5">
              {tasks.length} action items
            </span>
          )}
        </div>

        <div className="mb-4">
          <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
            Action items
          </h3>
          {tasksLoading ? (
            <SkeletonLine width="w-3/4" />
          ) : tasksError ? (
            <p className="text-sm text-slate-500">{tasksError}</p>
          ) : visibleTasks.length === 0 ? (
            <p className="text-sm text-slate-500">No action items assigned to you.</p>
          ) : (
            <div className="space-y-1">
              {visibleTasks.map((task) => (
                <div key={task.id}>
                  <div
                    data-testid={`task-row-${task.id}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      navigate(`/confluence/${task.page_id}`);
                    }}
                    className="flex items-center gap-2 p-1 rounded cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-800/50 transition-colors"
                  >
                    <input
                      type="checkbox"
                      aria-label={task.text}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleCheck(task);
                      }}
                      className="shrink-0 cursor-pointer"
                    />
                    <span className="flex-1 text-sm truncate">{task.text}</span>
                    {task.due && (
                      <span className="text-xs text-slate-400 shrink-0">{dueLabel(task.due)}</span>
                    )}
                  </div>
                  {taskErrors[task.id] && (
                    <p className="text-xs text-red-500 ml-6 mt-0.5">{taskErrors[task.id]}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div>
          <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
            Mentions
          </h3>
          {mentionsLoading ? (
            <SkeletonLine width="w-2/3" />
          ) : mentionsError ? (
            <p className="text-sm text-slate-500">{mentionsError}</p>
          ) : mentions.length === 0 ? (
            <p className="text-sm text-slate-500">No recent mentions.</p>
          ) : (
            <div className="space-y-1">
              {mentions.map((row) => (
                <div
                  key={row.id}
                  data-testid={`mention-row-${row.id}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(`/confluence/${row.id}`);
                  }}
                  className="flex items-center gap-2 p-1 rounded cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-800/50 transition-colors"
                >
                  <span className="flex-1 text-sm truncate">{row.title}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
