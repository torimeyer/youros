import { useState, useEffect, useCallback, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import Icon from '../components/Icon';
import TopBar from '../components/TopBar';
import QuickAddTaskModal from '../components/QuickAddTaskModal';
import QuickSpawnAgentModal from '../components/QuickSpawnAgentModal';
import QuickCaptureIdeaModal from '../components/QuickCaptureIdeaModal';
import DashboardCustomizeModal from '../components/DashboardCustomizeModal';
import { api } from '../lib/api';
import { renderMarkdown } from '../lib/markdown';
import { useAppStore } from '../stores/app';

interface BriefingData {
  show: boolean;
  briefing: string | null;
}

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


interface CalendarEvent {
  id: string
  summary?: string
  start: { dateTime?: string; date?: string }
  end: { dateTime?: string; date?: string }
  location?: string
  hangoutLink?: string
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

export default function Dashboard() {
  const navigate = useNavigate();
  const toggleChat = useAppStore((s) => s.toggleChat);
  const osName = useAppStore((s) => s.osName);
  const dashboardWidgets = useAppStore((s) => s.dashboardWidgets);
  const setDashboardWidgets = useAppStore((s) => s.setDashboardWidgets);
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [summaryBullets, setSummaryBullets] = useState<string[]>([]);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [compounds, setCompounds] = useState<CompoundsData | null>(null);
  const [, setSessionDiff] = useState<SessionDiff | null>(null);
  const [briefing, setBriefing] = useState<BriefingData | null>(null);
  const [briefingLoading, setBriefingLoading] = useState(true);
  const [quickAddTaskOpen, setQuickAddTaskOpen] = useState(false);
  const [quickSpawnOpen, setQuickSpawnOpen] = useState(false);
  const [quickCaptureOpen, setQuickCaptureOpen] = useState(false);
  const [customizeOpen, setCustomizeOpen] = useState(false);

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
    // Auto-refresh every 15s so 'Today's Focus' stays current without a refresh button.
    const interval = setInterval(fetchData, 15000);
    return () => clearInterval(interval);
  }, [fetchData]);

  useEffect(() => {
    setBriefingLoading(true);
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    const fetchBriefing = () => {
      api.get<BriefingData>('/briefing')
        .then((res) => {
          if (res.show && !res.briefing) {
            // Generating in background, poll again in 2s
            pollTimer = setTimeout(fetchBriefing, 2000);
          } else {
            setBriefing(res);
            setBriefingLoading(false);
          }
        })
        .catch(() => {
          setBriefing({ show: false, briefing: null });
          setBriefingLoading(false);
        });
    };

    fetchBriefing();
    return () => { if (pollTimer) clearTimeout(pollTimer); };
  }, []);

  const handleDismissBriefing = () => {
    api.post('/briefing/dismiss', {}).catch(() => {});
    setBriefing({ show: false, briefing: null });
  };

  const focusTasks = (data?.focus ?? []).map((t, i) => ({
    id: t.id,
    icon: focusIcons[i % focusIcons.length],
    title: t.title,
    subtitle: `Priority: ${t.priority}`,
    color: focusColors[i % focusColors.length],
  }));

  const openFocusTask = (taskId: string) => {
    if (taskId) {
      navigate(`/tasks?focus=${encodeURIComponent(taskId)}`);
    } else {
      navigate('/tasks');
    }
  };

  const openCount = data?.counts?.open ?? 0;
  const closedCount = data?.counts?.closed ?? 0;

  const quickLaunchActions: Record<string, () => void> = {
    'New Task': () => setQuickAddTaskOpen(true),
    'Spawn Agent': () => setQuickSpawnOpen(true),
    'Capture Idea': () => setQuickCaptureOpen(true),
    'Open Chat': () => toggleChat(),
  };

  const quickLaunch = [
    { icon: 'add_task', label: 'New Task', color: 'text-blue-400', hoverBorder: 'hover:border-blue-500' },
    { icon: 'bolt', label: 'Spawn Agent', color: 'text-purple-400', hoverBorder: 'hover:border-purple-500' },
    { icon: 'note_add', label: 'Capture Idea', color: 'text-pink-400', hoverBorder: 'hover:border-pink-500' },
    { icon: 'chat', label: 'Open Chat', color: 'text-cyan-400', hoverBorder: 'hover:border-cyan-500' },
  ];

  const [nextMeeting, setNextMeeting] = useState<CalendarEvent | null | undefined>(undefined);

  useEffect(() => {
    api.get<{ events: CalendarEvent[] }>('/calendar/events')
      .then((res) => {
        const now = Date.now();
        const future = (res.events || []).filter((ev) => {
          const startStr = ev.start?.dateTime || ev.start?.date;
          if (!startStr) return false;
          return new Date(startStr).getTime() > now;
        });
        setNextMeeting(future[0] ?? null);
      })
      .catch(() => setNextMeeting(null));
  }, []);

  const cardClass = 'bg-slate-900/40 border border-slate-800 p-6 rounded-xl hover:border-slate-700 transition-colors';

  const hour = new Date().getHours();
  let greetingLabel: string;
  let greetingSubtitle: string;
  if (hour < 12) {
    greetingLabel = 'Good morning';
    greetingSubtitle = 'Ready to get started?';
  } else if (hour < 18) {
    greetingLabel = 'Good afternoon';
    greetingSubtitle = "What's on your plate?";
  } else {
    greetingLabel = 'Good evening';
    greetingSubtitle = 'Wrapping up for today?';
  }

  // Render the Briefing banner (or its skeleton while loading).
  // Returns null when the briefing is dismissed or unavailable.
  const renderBriefing = () => {
    if (briefingLoading) {
      return (
        <div
          key="briefing"
          data-testid="widget-briefing"
          className="mb-6 bg-slate-900/40 border border-slate-800 rounded-xl p-5 animate-pulse"
        >
          <div className="h-3 bg-slate-700 rounded w-1/4 mb-3" />
          <div className="h-3 bg-slate-700 rounded w-3/4 mb-2" />
          <div className="h-3 bg-slate-700 rounded w-2/3" />
        </div>
      );
    }
    if (briefing?.show && briefing.briefing) {
      return (
        <div
          key="briefing"
          data-testid="widget-briefing"
          className="mb-6 bg-gradient-to-r from-blue-500/10 to-cyan-500/10 border border-blue-500/30 rounded-xl p-5"
        >
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3 flex-1">
              <div className="w-8 h-8 rounded-full bg-blue-500/20 flex items-center justify-center shrink-0 mt-0.5">
                <Icon name="wb_sunny" className="text-blue-400" size={18} />
              </div>
              <div className="flex-1">
                <p className="text-xs font-medium text-blue-400 uppercase tracking-wide mb-1.5">{greetingLabel}</p>
                <div className="text-sm text-slate-200 leading-relaxed space-y-3">
                  {briefing.briefing.split(/\n\n+/).map((para, i) => (
                    <p key={i}>{renderMarkdown(para)}</p>
                  ))}
                </div>
              </div>
            </div>
            <button
              onClick={handleDismissBriefing}
              className="text-slate-500 hover:text-slate-300 transition-colors shrink-0 mt-0.5"
              aria-label="Dismiss briefing"
            >
              <Icon name="close" size={18} />
            </button>
          </div>
        </div>
      );
    }
    // Empty state. Without this branch, toggling the Briefing card on
    // in the customize modal does nothing visible whenever there is no
    // briefing yet, which is the exact bug Tori reported on April 8.
    // The card must always render when its toggle is on, even if there
    // is nothing to put in it. Same pattern as renderFocusFirst,
    // renderNextMeeting, renderDaySummary.
    return (
      <div
        key="briefing"
        data-testid="widget-briefing"
        className="mb-6 bg-slate-900/40 border border-slate-800 rounded-xl p-5"
      >
        <div className="flex items-start gap-3">
          <div className="w-8 h-8 rounded-full bg-slate-800 flex items-center justify-center shrink-0 mt-0.5">
            <Icon name="wb_sunny" className="text-slate-500" size={18} />
          </div>
          <div className="flex-1">
            <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1.5">{greetingLabel}</p>
            <p className="text-sm text-slate-400 leading-relaxed">
              No briefing yet. One will appear here the next time myOS generates one for you.
            </p>
          </div>
        </div>
      </div>
    );
  };

  const renderFocusFirst = () => {
    if (!compounds?.top) {
      return (
        <div
          key="focus_first"
          data-testid="widget-focus-first"
          className="mb-6 bg-slate-900/40 border border-slate-800 p-6 rounded-xl"
        >
          <div className="flex items-center gap-3 mb-2">
            <div className="w-10 h-10 rounded-full bg-pink-500/20 flex items-center justify-center">
              <Icon name="priority_high" className="text-pink-400" size={22} />
            </div>
            <div>
              <p className="text-xs font-medium text-pink-400 uppercase tracking-wide">Focus on this first</p>
              <h3 className="text-lg font-semibold text-white">Nothing blocking others right now</h3>
            </div>
          </div>
          <p className="text-sm text-slate-400 ml-[52px]">
            When a task blocks several others, it will show up here so you know to finish it first.
          </p>
        </div>
      );
    }
    return (
      <div
        key="focus_first"
        data-testid="widget-focus-first"
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
    );
  };

  const renderTodaysFocus = () => (
    <div key="todays_focus" data-testid="widget-todays-focus" className={cardClass}>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Icon name="target" className="text-pink-400" size={20} />
          <h2 className="text-lg font-semibold">Today's Focus</h2>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-pink-400">{openCount} open</span>
          <span className="text-blue-400">{closedCount} done</span>
        </div>
      </div>
      <div className="space-y-3">
        {loading && focusTasks.length === 0 ? (
          <p className="text-sm text-slate-500">Loading...</p>
        ) : focusTasks.length === 0 ? (
          <p className="text-sm text-slate-500">No focus tasks right now.</p>
        ) : (
          focusTasks.map((task) => (
            <div
              key={task.id || task.title}
              role="button"
              tabIndex={0}
              onClick={() => openFocusTask(task.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  openFocusTask(task.id);
                }
              }}
              aria-label={`Open task ${task.title}`}
              className="flex items-center gap-4 p-3 rounded-lg hover:bg-slate-800/50 transition-colors cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500/50"
            >
              <div className={`w-10 h-10 rounded-full flex items-center justify-center ${task.color}`}>
                <Icon name={task.icon} size={20} />
              </div>
              <div className="flex-1">
                <p className="font-medium">{task.title}</p>
                <p className="text-sm text-slate-400">{task.subtitle}</p>
              </div>
              <Icon name="open_in_new" className="text-slate-500" size={18} />
            </div>
          ))
        )}
      </div>
    </div>
  );

  const renderQuickLaunch = () => (
    <div key="quick_launch" data-testid="widget-quick-launch" className={cardClass}>
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
  );

  const renderNextMeeting = () => {
    if (!nextMeeting) {
      return (
        <div
          key="next_meeting"
          data-testid="widget-next-meeting"
          className={`${cardClass} lg:col-span-2`}
          onClick={() => navigate('/calendar')}
          style={{ cursor: 'pointer' }}
        >
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center shrink-0">
              <Icon name="calendar_month" className="text-blue-400" size={20} />
            </div>
            <div>
              <p className="text-xs font-medium text-blue-400 uppercase tracking-wide mb-0.5">Next meeting</p>
              <p className="text-sm text-slate-400">No upcoming meetings on your calendar.</p>
            </div>
          </div>
        </div>
      );
    }
    return (
      <div
        key="next_meeting"
        data-testid="widget-next-meeting"
        className={`${cardClass} lg:col-span-2`}
        onClick={() => navigate('/calendar')}
        style={{ cursor: 'pointer' }}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center shrink-0">
              <Icon name="calendar_month" className="text-blue-400" size={20} />
            </div>
            <div>
              <p className="text-xs font-medium text-blue-400 uppercase tracking-wide mb-0.5">Next meeting</p>
              <p className="font-semibold">{nextMeeting.summary || 'Untitled'}</p>
              {(() => {
                const startStr = nextMeeting.start?.dateTime || nextMeeting.start?.date;
                if (!startStr) return null;
                try {
                  const d = new Date(startStr);
                  const timeStr = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
                  const today = new Date();
                  const isToday = d.toDateString() === today.toDateString();
                  const tomorrow = new Date(today);
                  tomorrow.setDate(today.getDate() + 1);
                  const isTomorrow = d.toDateString() === tomorrow.toDateString();
                  const dayStr = isToday ? 'Today' : isTomorrow ? 'Tomorrow' : d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
                  return <p className="text-sm text-slate-400">{dayStr} at {timeStr}</p>;
                } catch {
                  return null;
                }
              })()}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {nextMeeting.hangoutLink && (
              <a
                href={nextMeeting.hangoutLink}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="flex items-center gap-1 px-3 py-1.5 bg-green-500/20 text-green-400 rounded-lg text-sm hover:bg-green-500/30 transition-colors"
              >
                <Icon name="video_call" size={16} />
                Join Meet
              </a>
            )}
            <Icon name="chevron_right" className="text-slate-500" size={20} />
          </div>
        </div>
      </div>
    );
  };

  const renderDaySummary = () => {
    // When a widget is in the user's dashboard list it should always
    // render. The briefing banner and Day Summary are now independent
    // cards the user controls via Customize.
    return (
      <div key="day_summary" data-testid="widget-day-summary" className={cardClass}>
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
    );
  };

  // Map of widget id to render function. Only widgets present in
  // dashboardWidgets render, and they render in that order. Widgets above
  // the grid (briefing and focus first) are full width banners,
  // the rest go inside the two column grid.
  const widgetRenderers: Record<string, () => ReactNode> = {
    briefing: renderBriefing,
    focus_first: renderFocusFirst,
    todays_focus: renderTodaysFocus,
    quick_launch: renderQuickLaunch,
    next_meeting: renderNextMeeting,
    day_summary: renderDaySummary,
  };

  const [widgetMenuOpen, setWidgetMenuOpen] = useState<string | null>(null);

  const removeWidget = (id: string) => {
    const next = dashboardWidgets.filter((w) => w !== id);
    setDashboardWidgets(next);
    setWidgetMenuOpen(null);
  };

  const bannerIds = new Set(['briefing', 'focus_first']);
  const visibleBanners = dashboardWidgets.filter((id) => bannerIds.has(id));
  const visibleGridCards = dashboardWidgets.filter(
    (id) => !bannerIds.has(id) && widgetRenderers[id],
  );

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="Home Dashboard" />

      <div className="pt-20 p-8">
        {visibleBanners.map((id) => widgetRenderers[id]?.())}

        {/* Greeting + Customize button */}
        <div className="mb-8 flex items-start justify-between gap-4">
          <div data-tour="dashboard">
            <h1 className="text-3xl font-bold mb-1">Welcome to {osName}</h1>
            <p className="text-slate-400">{greetingSubtitle}</p>
          </div>
          <button
            onClick={() => setCustomizeOpen(true)}
            aria-label="Customize dashboard"
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-900/60 hover:bg-slate-800 border border-slate-800 hover:border-slate-700 rounded-lg text-sm text-slate-300 transition-colors shrink-0"
          >
            <Icon name="dashboard_customize" size={16} />
            Customize
          </button>
        </div>

        {/* Widget Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 auto-rows-min">
          {visibleGridCards.map((id) => (
            <div key={`wrap-${id}`} className="relative group/widget">
              {widgetRenderers[id]?.()}
              <div className="absolute top-3 right-3 opacity-0 group-hover/widget:opacity-100 transition-opacity">
                <button
                  onClick={() => setWidgetMenuOpen(widgetMenuOpen === id ? null : id)}
                  className="p-1 rounded-md bg-slate-800/80 hover:bg-slate-700 text-slate-400 hover:text-white transition-colors"
                  aria-label={`Widget options for ${id}`}
                >
                  <Icon name="more_vert" size={16} />
                </button>
                {widgetMenuOpen === id && (
                  <div className="absolute right-0 mt-1 bg-slate-800 border border-slate-700 rounded-lg shadow-lg py-1 z-10 min-w-[140px]">
                    <button
                      onClick={() => removeWidget(id)}
                      className="w-full text-left px-3 py-1.5 text-sm text-red-400 hover:bg-slate-700 transition-colors flex items-center gap-2"
                    >
                      <Icon name="visibility_off" size={14} />
                      Hide widget
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Floating Action Button - Open Chat */}
      <button
        onClick={() => useAppStore.getState().toggleChat()}
        className="fixed bottom-8 right-8 w-14 h-14 bg-blue-500 hover:bg-blue-600 rounded-2xl flex items-center justify-center shadow-lg shadow-blue-500/25 transition-colors"
      >
        <Icon name="chat" className="text-white" size={28} />
      </button>

      <QuickAddTaskModal
        open={quickAddTaskOpen}
        onClose={() => setQuickAddTaskOpen(false)}
        onSuccess={fetchData}
      />
      <QuickSpawnAgentModal
        open={quickSpawnOpen}
        onClose={() => setQuickSpawnOpen(false)}
      />
      <QuickCaptureIdeaModal
        open={quickCaptureOpen}
        onClose={() => setQuickCaptureOpen(false)}
      />
      <DashboardCustomizeModal
        open={customizeOpen}
        onClose={() => setCustomizeOpen(false)}
        widgets={dashboardWidgets}
        onSave={(next) => setDashboardWidgets(next)}
      />
    </div>
  );
}
