import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import Icon from '../components/Icon';
import TopBar from '../components/TopBar';
import { api } from '../lib/api';
import { useAppStore } from '../stores/app';

interface FocusTask {
  title: string;
  id: string;
  priority: string;
}

interface RecentTask {
  id: string;
  title: string;
  priority: string;
}

interface CompoundTask {
  id: string;
  title: string;
  blocks_count: number;
}

interface CompoundsData {
  top: CompoundTask | null;
  all: CompoundTask[];
}

interface DashboardData {
  counts: { open: number; closed: number; p0: number; p1: number; p2: number };
  focus: FocusTask[];
  recent_tasks: RecentTask[];
  hay_count: number;
  ostk_status: string;
}


interface SessionDiff {
  files_changed: string[];
  needles_filed: { id: string; priority: string; title: string }[];
  audit_events: { count: number; event: string }[];
  audit_total: number;
}


const focusIcons = ['code', 'mail', 'smart_toy', 'target', 'bolt'];
const focusColors = [
  'bg-pink-500/20 text-pink-400',
  'bg-blue-500/20 text-blue-400',
  'bg-purple-500/20 text-purple-400',
  'bg-cyan-500/20 text-cyan-400',
  'bg-orange-500/20 text-orange-400',
];

const priorityColor = (p: string) => {
  if (p === 'P0') return 'bg-pink-500/20 text-pink-400';
  if (p === 'P1') return 'bg-orange-500/20 text-orange-400';
  return 'bg-blue-500/20 text-blue-400';
};

export default function Dashboard() {
  const navigate = useNavigate();
  const toggleChat = useAppStore((s) => s.toggleChat);
  const osName = useAppStore((s) => s.osName);
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [summaryBullets, setSummaryBullets] = useState<string[]>([]);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [compounds, setCompounds] = useState<CompoundsData | null>(null);
  const [sessionDiff, setSessionDiff] = useState<SessionDiff | null>(null);

  const fetchSummary = useCallback(async () => {
    setSummaryLoading(true);
    try {
      const res = await api.get<{ bullets: string[] }>('/dashboard/summary');
      setSummaryBullets(res.bullets);
    } catch (e) {
      console.error('Failed to fetch summary:', e);
    } finally {
      setSummaryLoading(false);
    }
  }, []);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [dashRes, compoundsRes, diffRes] = await Promise.all([
        api.get<DashboardData>('/dashboard'),
        api.get<CompoundsData>('/dashboard/compounds').catch(() => null),
        api.get<SessionDiff>('/dashboard/diff').catch(() => null),
      ]);
      setData(dashRes);
      if (compoundsRes) setCompounds(compoundsRes);
      if (diffRes) setSessionDiff(diffRes);
    } catch (e) {
      console.error('Failed to fetch dashboard:', e);
    } finally {
      setLoading(false);
    }
    fetchSummary();
  }, [fetchSummary]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const focusTasks = (data?.focus ?? []).map((t, i) => ({
    icon: focusIcons[i % focusIcons.length],
    title: t.title,
    subtitle: `Priority: ${t.priority}`,
    color: focusColors[i % focusColors.length],
  }));

  const tasks = (data?.recent_tasks ?? []).map((t) => ({
    title: t.title,
    priority: t.priority,
    color: priorityColor(t.priority),
  }));

  const openCount = data?.counts.open ?? 0;
  const closedCount = data?.counts.closed ?? 0;

  const quickLaunchActions: Record<string, () => void> = {
    'New Task': () => navigate('/tasks'),
    'Spawn Agent': () => navigate('/agents'),
    'Quick Note': () => navigate('/ideas'),
    'Open Chat': () => toggleChat(),
  };

  const quickLaunch = [
    { icon: 'add_task', label: 'New Task', color: 'text-blue-400', hoverBorder: 'hover:border-blue-500' },
    { icon: 'bolt', label: 'Spawn Agent', color: 'text-purple-400', hoverBorder: 'hover:border-purple-500' },
    { icon: 'note_add', label: 'Quick Note', color: 'text-pink-400', hoverBorder: 'hover:border-pink-500' },
    { icon: 'chat', label: 'Open Chat', color: 'text-cyan-400', hoverBorder: 'hover:border-cyan-500' },
  ];

  const [labels, setLabels] = useState<{ id: string; name: string; color: string; task_count: number }[]>([]);

  useEffect(() => {
    api.get<{ labels: { id: string; name: string; color: string; task_count: number }[] }>('/labels')
      .then((res) => setLabels(res.labels ?? []))
      .catch(() => {});
  }, []);

  const cardClass = 'bg-slate-900/40 border border-slate-800 p-6 rounded-xl hover:border-slate-700 transition-colors';

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="Home Dashboard" />

      <div className="pt-16 p-8">
        {/* Greeting */}
        <div data-tour="dashboard" className="mb-8">
          <h1 className="text-3xl font-bold mb-1">Welcome to {osName}</h1>
          <p className="text-slate-400">Ready for your morning deep work session?</p>
        </div>


        {/* Focus on this first */}
        {compounds?.top && (
          <div
            onClick={() => navigate('/tasks')}
            className="mb-6 bg-gradient-to-r from-pink-500/10 to-purple-500/10 border border-pink-500/30 p-6 rounded-xl hover:border-pink-500/50 transition-colors cursor-pointer"
          >
            <div className="flex items-center gap-3 mb-2">
              <div className="w-10 h-10 rounded-full bg-pink-500/20 flex items-center justify-center">
                <Icon name="priority_high" className="text-pink-400" size={22} />
              </div>
              <div>
                <p className="text-xs font-medium text-pink-400 uppercase tracking-wide">Focus on this first</p>
                <h3 className="text-lg font-semibold text-white">{compounds.top.title}</h3>
              </div>
            </div>
            <p className="text-sm text-slate-400 ml-[52px]">
              Finishing this unblocks {compounds.top.blocks_count} other {compounds.top.blocks_count === 1 ? 'task' : 'tasks'}.
              Getting it done first lets everything else move forward.
            </p>
          </div>
        )}

        {/* Widget Grid */}
        <div className="grid grid-cols-2 gap-6 auto-rows-min">

          {/* Today's Focus */}
          <div className={cardClass}>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Icon name="target" className="text-pink-400" size={20} />
                <h2 className="text-lg font-semibold">Today's Focus</h2>
              </div>
              <button
                onClick={fetchData}
                className="text-sm text-slate-400 hover:text-white transition-colors flex items-center gap-1"
              >
                <Icon name="refresh" size={16} />
                Refresh
              </button>
            </div>
            <div className="space-y-3">
              {loading && focusTasks.length === 0 ? (
                <p className="text-sm text-slate-500">Loading...</p>
              ) : focusTasks.length === 0 ? (
                <p className="text-sm text-slate-500">No focus tasks right now.</p>
              ) : (
                focusTasks.map((task) => (
                  <div
                    key={task.title}
                    onClick={() => navigate('/tasks')}
                    className="flex items-center gap-4 p-3 rounded-lg hover:bg-slate-800/50 transition-colors cursor-pointer"
                  >
                    <div className={`w-10 h-10 rounded-full flex items-center justify-center ${task.color}`}>
                      <Icon name={task.icon} size={20} />
                    </div>
                    <div className="flex-1">
                      <p className="font-medium">{task.title}</p>
                      <p className="text-sm text-slate-400">{task.subtitle}</p>
                    </div>
                    <Icon name="chevron_right" className="text-slate-500" size={20} />
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Quick Launch */}
          <div className={cardClass}>
            <h2 className="text-lg font-semibold mb-4">Quick Launch</h2>
            <div className="grid grid-cols-2 gap-3">
              {quickLaunch.map((item) => (
                <button
                  key={item.label}
                  onClick={quickLaunchActions[item.label]}
                  className={`flex flex-col items-center p-4 bg-slate-900 rounded-lg border border-slate-800 ${item.hoverBorder} transition-colors`}
                >
                  <Icon name={item.icon} className={item.color} size={24} />
                  <span className="text-sm text-slate-300 mt-2">{item.label}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Day Summary */}
          <div className={cardClass}>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Icon name="calendar_today" className="text-cyan-400" size={20} />
                <h2 className="text-lg font-semibold">Day Summary</h2>
              </div>
              <button
                onClick={fetchSummary}
                disabled={summaryLoading}
                className="text-sm text-slate-400 hover:text-white transition-colors flex items-center gap-1 disabled:opacity-50"
              >
                <Icon name="refresh" size={16} className={summaryLoading ? 'animate-spin' : ''} />
                Refresh
              </button>
            </div>
            <div className="space-y-2">
              {summaryLoading && summaryBullets.length === 0 ? (
                <p className="text-sm text-slate-500">Loading summary...</p>
              ) : summaryBullets.length === 0 ? (
                <p className="text-sm text-slate-500">No activity to summarize yet.</p>
              ) : (
                <ul className="space-y-2">
                  {summaryBullets.map((bullet, i) => (
                    <li key={i} className="flex items-start gap-3 text-sm text-slate-300">
                      <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-cyan-400 shrink-0" />
                      {bullet}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          {/* What Changed This Session */}
          <div className={cardClass}>
            <div className="flex items-center gap-2 mb-4">
              <Icon name="difference" className="text-emerald-400" size={20} />
              <h2 className="text-lg font-semibold">What Changed</h2>
              {sessionDiff && sessionDiff.audit_total > 0 && (
                <span className="text-xs text-slate-400 ml-auto">
                  {sessionDiff.audit_total} total {sessionDiff.audit_total === 1 ? 'event' : 'events'} this session
                </span>
              )}
            </div>
            {!sessionDiff || (sessionDiff.files_changed.length === 0 && sessionDiff.needles_filed.length === 0 && sessionDiff.audit_events.length === 0) ? (
              <p className="text-sm text-slate-500">Nothing has changed yet this session.</p>
            ) : (
              <div className="space-y-4">
                {/* Files modified */}
                {sessionDiff.files_changed.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-slate-400 uppercase tracking-wide mb-2">Files modified</p>
                    <div className="space-y-1">
                      {sessionDiff.files_changed.slice(0, 5).map((file) => (
                        <div key={file} className="flex items-center gap-2 text-sm text-slate-300">
                          <Icon name="description" className="text-slate-500" size={14} />
                          <span className="truncate">{file}</span>
                        </div>
                      ))}
                      {sessionDiff.files_changed.length > 5 && (
                        <p className="text-xs text-slate-500 ml-5">
                          and {sessionDiff.files_changed.length - 5} more
                        </p>
                      )}
                    </div>
                  </div>
                )}

                {/* Tasks filed */}
                {sessionDiff.needles_filed.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-slate-400 uppercase tracking-wide mb-2">Tasks filed</p>
                    <div className="space-y-1">
                      {sessionDiff.needles_filed.slice(0, 5).map((needle) => (
                        <div key={needle.id} className="flex items-center gap-2 text-sm">
                          <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${priorityColor(needle.priority)}`}>
                            {needle.priority}
                          </span>
                          <span className="text-slate-300 truncate">{needle.title}</span>
                        </div>
                      ))}
                      {sessionDiff.needles_filed.length > 5 && (
                        <p className="text-xs text-slate-500 ml-5">
                          and {sessionDiff.needles_filed.length - 5} more
                        </p>
                      )}
                    </div>
                  </div>
                )}

                {/* Activity breakdown */}
                {sessionDiff.audit_events.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-slate-400 uppercase tracking-wide mb-2">Activity breakdown</p>
                    <div className="flex flex-wrap gap-2">
                      {sessionDiff.audit_events.map((ev) => (
                        <span
                          key={ev.event}
                          className="text-xs px-2.5 py-1 rounded-full bg-blue-500/10 text-blue-600 dark:bg-slate-800/80 dark:text-slate-300 border border-blue-500/20 dark:border-transparent"
                        >
                          {ev.count} {ev.event.replace('.', ' ')}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Tasks */}
          <div className={cardClass}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Tasks</h2>
              <div className="flex items-center gap-3 text-sm">
                <span className="text-pink-400">{openCount} Open</span>
                <span className="text-blue-400">{closedCount} Closed</span>
              </div>
            </div>
            <div className="space-y-2">
              {tasks.length === 0 && !loading && (
                <p className="text-sm text-slate-500">No recent tasks.</p>
              )}
              {tasks.map((task) => (
                <div key={task.title} className="flex items-center gap-3 py-2">
                  <div className={`w-2 h-2 rounded-full ${task.priority === 'P0' ? 'bg-pink-400' : task.priority === 'P1' ? 'bg-orange-400' : 'bg-blue-400'}`} />
                  <span className="flex-1 text-sm text-slate-300">{task.title}</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${task.color}`}>
                    {task.priority}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Labels */}
          <div className={cardClass}>
            <h2 className="text-lg font-semibold mb-4">Labels</h2>
            <div className="space-y-3">
              {labels.length === 0 ? (
                <div className="text-center py-6">
                  <Icon name="label" className="text-slate-700 mb-2" size={32} />
                  <p className="text-sm text-slate-500">No labels yet.</p>
                  <p className="text-xs text-slate-600 mt-1">Create labels in Tasks to organize your work.</p>
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {labels.map((label) => (
                    <button
                      key={label.id}
                      onClick={() => navigate('/tasks')}
                      className="flex items-center gap-2 px-3 py-1.5 rounded-full text-sm transition-colors hover:opacity-80"
                      style={{
                        backgroundColor: label.color + '20',
                        color: label.color,
                        border: `1px solid ${label.color}40`,
                      }}
                    >
                      <span className="w-2 h-2 rounded-full" style={{ backgroundColor: label.color }} />
                      {label.name}
                      <span className="text-[10px] opacity-70">{label.task_count}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>



        </div>
      </div>

      {/* Floating Action Button - Open Chat */}
      <button
        onClick={() => useAppStore.getState().toggleChat()}
        className="fixed bottom-8 right-8 w-14 h-14 bg-blue-500 hover:bg-blue-600 rounded-2xl flex items-center justify-center shadow-lg shadow-blue-500/25 transition-colors"
      >
        <Icon name="chat" className="text-white" size={28} />
      </button>
    </div>
  );
}
