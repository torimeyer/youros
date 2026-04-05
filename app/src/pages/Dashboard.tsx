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

interface DashboardData {
  counts: { open: number; closed: number; p0: number; p1: number; p2: number };
  focus: FocusTask[];
  recent_tasks: RecentTask[];
  hay_count: number;
  ostk_status: string;
}

interface CostSummary {
  total_budget: number;
  agent_count: number;
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
  const setShowTour = useAppStore((s) => s.setShowTour);

  const [tourDismissed, setTourDismissed] = useState(
    () => localStorage.getItem('youros-tour-complete') === 'true',
  );

  const startTour = () => {
    setShowTour(true);
    setTourDismissed(true);
    localStorage.setItem('youros-tour-complete', 'true');
  };

  const dismissTour = () => {
    setTourDismissed(true);
    localStorage.setItem('youros-tour-complete', 'true');
  };

  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [costData, setCostData] = useState<CostSummary | null>(null);
  const [activeAgents, setActiveAgents] = useState<{ name: string; status: string }[]>([]);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [dashRes, costRes, agentsRes] = await Promise.all([
        api.get<DashboardData>('/dashboard'),
        api.get<CostSummary>('/costs?period=all').catch(() => null),
        api.get<{ active: { name: string; status: string; source?: string }[] }>('/agents').catch(() => null),
      ]);
      setData(dashRes);
      if (costRes) setCostData(costRes);
      if (agentsRes?.active) setActiveAgents(agentsRes.active);
    } catch (e) {
      console.error('Failed to fetch dashboard:', e);
    } finally {
      setLoading(false);
    }
  }, []);

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
  const ostkStatus = data?.ostk_status ?? 'loading...';

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

  const goals: { name: string; percent: number; barColor: string }[] = [];

  const cardClass = 'bg-slate-900/40 border border-slate-800 p-6 rounded-xl hover:border-slate-700 transition-colors';

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="Home Dashboard" />

      <div className="pt-16 p-8">
        {/* Greeting */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold mb-1">Welcome to {osName}</h1>
          <p className="text-slate-400">Ready for your morning deep work session?</p>
        </div>

        {/* Tour Banner */}
        {!tourDismissed && (
          <div className="mb-6 bg-blue-500/10 border border-blue-500/30 rounded-xl p-4 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Icon name="explore" className="text-blue-400" size={24} />
              <div>
                <p className="text-sm font-medium text-blue-300">New here?</p>
                <p className="text-xs text-slate-400">Take a quick tour to learn your way around.</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button onClick={startTour} className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium transition-colors">
                Start Tour
              </button>
              <button onClick={dismissTour} className="p-1 text-slate-500 hover:text-white transition-colors">
                <Icon name="close" size={18} />
              </button>
            </div>
          </div>
        )}

        {/* Widget Grid */}
        <div className="grid grid-cols-3 gap-6">

          {/* Today's Focus - col-span-2 */}
          <div className={`${cardClass} col-span-2`}>
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

          {/* Goals */}
          <div className={cardClass}>
            <h2 className="text-lg font-semibold mb-4">Goals</h2>
            <div className="space-y-4">
              {goals.length === 0 ? (
                <div className="text-center py-6">
                  <Icon name="flag" className="text-slate-700 mb-2" size={32} />
                  <p className="text-sm text-slate-500">No goals yet.</p>
                  <p className="text-xs text-slate-600 mt-1">Goals will appear here as you set them up.</p>
                </div>
              ) : (
                goals.map((goal) => (
                  <div key={goal.name}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm text-slate-300">{goal.name}</span>
                      <span className="text-sm text-slate-400">{goal.percent}%</span>
                    </div>
                    <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
                      <div className={`h-full rounded-full ${goal.barColor}`} style={{ width: `${goal.percent}%` }} />
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* System Status */}
          <div className={cardClass}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">System</h2>
              <span className={`flex items-center gap-1.5 text-xs font-medium px-2 py-1 rounded-full ${
                ostkStatus === 'no daemon running'
                  ? 'text-slate-400 bg-slate-500/10'
                  : 'text-green-400 bg-green-500/10'
              }`}>
                <span className={`w-2 h-2 rounded-full ${
                  ostkStatus === 'no daemon running' ? 'bg-slate-400' : 'bg-green-400 animate-pulse'
                }`} />
                {ostkStatus === 'no daemon running' ? 'Ready' : 'Active'}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="bg-slate-800/50 rounded-lg p-3">
                <p className="text-xs text-slate-400">Open Tasks</p>
                <p className="text-lg font-semibold">{openCount}</p>
              </div>
              <div className="bg-slate-800/50 rounded-lg p-3">
                <p className="text-xs text-slate-400">Ideas</p>
                <p className="text-lg font-semibold">{data?.hay_count ?? 0}</p>
              </div>
            </div>
          </div>

          {/* Session Activity */}
          <div className={cardClass}>
            <h2 className="text-lg font-semibold mb-4">Session Activity</h2>
            <div className="space-y-4">
              {activeAgents.length === 0 ? (
                <div className="text-center py-6">
                  <Icon name="smart_toy" className="text-slate-700 mb-2" size={32} />
                  <p className="text-sm text-slate-500">No active agents.</p>
                  <p className="text-xs text-slate-600 mt-1">Agents you spawn will appear here.</p>
                </div>
              ) : (
                activeAgents.map((agent) => (
                  <div key={agent.name} className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-full bg-purple-500/20 flex items-center justify-center">
                      <Icon name="smart_toy" className="text-purple-400" size={20} />
                    </div>
                    <div className="flex-1">
                      <p className="text-sm font-medium">{agent.name}</p>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-xs font-medium text-green-400 bg-green-500/10 px-2 py-0.5 rounded-full">
                          {agent.status}
                        </span>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Cost Tracker */}
          <div className={cardClass} onClick={() => navigate('/costs')} style={{ cursor: 'pointer' }}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Cost Tracker</h2>
              <Icon name="chevron_right" className="text-slate-500" size={20} />
            </div>
            <div className="space-y-4">
              <div>
                <p className="text-xs text-slate-400 mb-1">Total Budget Allocated</p>
                <span className="text-2xl font-bold">
                  ${costData ? costData.total_budget.toFixed(2) : '0.00'}
                </span>
              </div>
              <div>
                <p className="text-xs text-slate-400 mb-1">Agents Spawned</p>
                <span className="text-lg font-semibold">
                  {costData ? costData.agent_count : 0}
                </span>
              </div>
              <p className="text-xs text-slate-500">Click for detailed breakdown</p>
            </div>
          </div>

        </div>
      </div>

      {/* Floating Action Button */}
      <button
        onClick={() => navigate('/tasks')}
        className="fixed bottom-8 right-8 w-14 h-14 bg-pink-500 hover:bg-pink-600 rounded-2xl flex items-center justify-center shadow-lg shadow-pink-500/25 transition-colors"
      >
        <Icon name="add" className="text-white" size={28} />
      </button>
    </div>
  );
}
