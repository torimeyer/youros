import { useState, useEffect, useCallback, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import Icon from '../components/Icon';
import TopBar from '../components/TopBar';
import QuickAddTaskModal from '../components/QuickAddTaskModal';
import QuickSpawnAgentModal from '../components/QuickSpawnAgentModal';
import DashboardCustomizeModal from '../components/DashboardCustomizeModal';
import RecentSpecsWidget from '../components/RecentSpecsWidget';
import { Card, SkeletonLine } from '../components/ui';
import { api } from '../lib/api';
import { renderMarkdown } from '../lib/markdown';
import { useAppStore } from '../stores/app';
import { ADVENTURE_DISMISSED_KEY, type AdventureTemplate } from '../lib/adventures';

interface ActionItem {
  type: 'reply_email' | 'close_task' | 'prep_meeting' | 'review_agent';
  label: string;
  action_url: string;
  context: string;
}

interface BriefingData {
  show: boolean;
  briefing: string | null;
  action_items?: ActionItem[];
  // Frontend-only marker. Set to true by the fetch catch block so the
  // empty-state UI knows the API call failed and should NOT lie that
  // the user "dismissed" the briefing. Before this field, a network
  // blip or zombie proxy hanging for 5s would fall through to the
  // "dismissed" copy even though the user never touched Dismiss.
  // See needle 281.
  unavailable?: boolean;
}

// localStorage key used to paint the last-known briefing within the
// 300ms primary-rows budget. Value shape: { ts, data: BriefingData }.
// Expires after 24 hours so a stale briefing from yesterday is never
// reused. See CLAUDE.md "300ms primary rows from localStorage" rule.
const BRIEFING_SEED_KEY = 'myos.briefing.last';
const BRIEFING_SEED_MAX_AGE_MS = 24 * 60 * 60 * 1000;

function readBriefingSeed(): BriefingData | null {
  try {
    if (typeof window === 'undefined') return null;
    const raw = localStorage.getItem(BRIEFING_SEED_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { ts?: number; data?: BriefingData };
    if (!parsed || typeof parsed.ts !== 'number' || !parsed.data) return null;
    if (Date.now() - parsed.ts > BRIEFING_SEED_MAX_AGE_MS) return null;
    return parsed.data;
  } catch {
    return null;
  }
}

function writeBriefingSeed(data: BriefingData): void {
  try {
    if (typeof window === 'undefined') return;
    // Only cache briefings with actual content. A null briefing
    // (generating) or a dismissed/unavailable state is not worth
    // painting on the next mount.
    if (!data.show || !data.briefing) return;
    const payload = JSON.stringify({ ts: Date.now(), data });
    localStorage.setItem(BRIEFING_SEED_KEY, payload);
  } catch {
    // ignore quota errors
  }
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

interface LiveSession {
  session_id: string;
  last_active: string;
  status: 'active' | 'idle';
  recent_events: { type: string; timestamp: string }[];
}

interface LiveSessionsData {
  sessions: LiveSession[];
  count: number;
  active_count: number;
  idle_count: number;
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
  const displayOsName = useAppStore((s) => s.displayOsName());
  const dashboardWidgets = useAppStore((s) => s.dashboardWidgets);
  const setDashboardWidgets = useAppStore((s) => s.setDashboardWidgets);
  const dashboardLayout = useAppStore((s) => s.dashboardLayout);
  const greetingStyle = useAppStore((s) => s.greetingStyle);
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [summaryBullets, setSummaryBullets] = useState<string[]>([]);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [compounds, setCompounds] = useState<CompoundsData | null>(null);
  const [, setSessionDiff] = useState<SessionDiff | null>(null);
  // Seed from localStorage so the Briefing card paints within the 300ms
  // primary-rows budget, before any network fetch completes. The fresh
  // fetch runs in the effect below and replaces this seed once it lands.
  const initialBriefingSeed = readBriefingSeed();
  const [briefing, setBriefing] = useState<BriefingData | null>(initialBriefingSeed);
  const [briefingLoading, setBriefingLoading] = useState(initialBriefingSeed === null);
  // True while a fresh briefing fetch is in flight on top of a seeded
  // briefing. Drives the small "Refreshing..." hint so the user knows
  // the card on screen is last-known and a newer one is on the way.
  const [briefingRefreshing, setBriefingRefreshing] = useState(initialBriefingSeed !== null);
  const [liveSessions, setLiveSessions] = useState<LiveSessionsData | null>(null);
  const [quickAddTaskOpen, setQuickAddTaskOpen] = useState(false);
  const [quickSpawnOpen, setQuickSpawnOpen] = useState(false);
  const [customizeOpen, setCustomizeOpen] = useState(false);

  // Adventure card state
  const [adventureTemplates, setAdventureTemplates] = useState<AdventureTemplate[]>([]);
  const [adventureDismissed, setAdventureDismissed] = useState(() =>
    typeof window !== 'undefined' && localStorage.getItem(ADVENTURE_DISMISSED_KEY) === 'true'
  );
  const [adventureSelected, setAdventureSelected] = useState<AdventureTemplate | null>(null);
  const [adventureDescription, setAdventureDescription] = useState('');
  const [adventureLoading, setAdventureLoading] = useState(false);
  const [adventureSpawned, setAdventureSpawned] = useState(false);


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
      const [dashRes, compoundsRes, diffRes, calRes] = await Promise.all([
        api.get<DashboardData>('/dashboard'),
        api.get<CompoundsData>('/dashboard/compounds').catch(() => null),
        api.get<SessionDiff>('/dashboard/diff').catch(() => null),
        api.get<{ events: CalendarEvent[] }>('/calendar/events').catch(() => null),
      ]);
      setData(dashRes);
      if (compoundsRes) setCompounds(compoundsRes);
      if (diffRes) setSessionDiff(diffRes);
      if (calRes) {
        const now = Date.now();
        const future = (calRes.events || []).filter((ev) => {
          const startStr = ev.start?.dateTime || ev.start?.date;
          if (!startStr) return false;
          return new Date(startStr).getTime() > now;
        });
        setNextMeeting(future[0] ?? null);
      } else {
        setNextMeeting(null);
      }
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
    const fetchSessions = async () => {
      try {
        const res = await api.get<LiveSessionsData>('/sessions/active');
        setLiveSessions(res);
      } catch {
        // ignore
      }
    };
    fetchSessions();
    const interval = setInterval(fetchSessions, 10000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    // If we have a seeded briefing on screen from localStorage, keep
    // showing it and flip the refreshing hint instead of blowing back
    // to the loading skeleton. The fetch below will replace the seed
    // once the fresh data lands.
    const hasSeed = readBriefingSeed() !== null;
    if (hasSeed) {
      setBriefingRefreshing(true);
    } else {
      setBriefingLoading(true);
    }
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let retryCount = 0;
    // The backend may still be starting when the page first loads
    // (needle 315). Retry up to 3 times before giving up so a
    // short startup race does not permanently show "unavailable".
    const MAX_RETRIES = 3;

    const fetchBriefing = () => {
      api.get<BriefingData>('/briefing')
        .then((res) => {
          if (res.show && !res.briefing) {
            // Generating in background, poll again in 2s
            pollTimer = setTimeout(fetchBriefing, 2000);
          } else {
            setBriefing(res);
            setBriefingLoading(false);
            setBriefingRefreshing(false);
            writeBriefingSeed(res);
          }
        })
        .catch(() => {
          retryCount += 1;
          if (retryCount <= MAX_RETRIES) {
            // Backend may still be starting. Retry before giving up.
            pollTimer = setTimeout(fetchBriefing, 2000);
            return;
          }
          // The fetch failed after retries (network issue, backend
          // hang, vite proxy zombie, etc). If we have a seeded briefing
          // on screen already, keep it and just stop the refresh hint.
          // If we had nothing, fall through to the unavailable state.
          setBriefingRefreshing(false);
          setBriefing((current) => {
            if (current && current.show && current.briefing) {
              return current;
            }
            return { show: false, briefing: null, unavailable: true };
          });
          setBriefingLoading(false);
        });
    };

    // If this load is a browser reload (Cmd+R or Cmd+Shift+R), clear
    // any previous dismissal so the briefing shows fresh. Tori's
    // mental model: a refresh means "start over with today's state".
    // See needle: briefing-hard-refresh-reload.
    let isReload = false;
    try {
      const navEntries = performance.getEntriesByType('navigation') as PerformanceNavigationTiming[];
      if (navEntries.length > 0 && navEntries[0].type === 'reload') {
        isReload = true;
      }
    } catch {
      // Performance API unavailable (older browser, jsdom without stub).
      // Default to not-reload so we do not repeatedly un-dismiss.
    }

    if (isReload) {
      api.post('/briefing/undismiss', {})
        .catch(() => {})
        .finally(() => { fetchBriefing(); });
    } else {
      fetchBriefing();
    }
    return () => { if (pollTimer) clearTimeout(pollTimer); };
  }, []);

  // Load adventure templates once (only if the card is not dismissed)
  useEffect(() => {
    if (adventureDismissed) return;
    api.get<{ adventures: AdventureTemplate[] }>('/adventures/templates')
      .then((data) => setAdventureTemplates(data.adventures || []))
      .catch(() => {});
  }, [adventureDismissed]);


  const handleDismissAdventure = () => {
    localStorage.setItem(ADVENTURE_DISMISSED_KEY, 'true');
    setAdventureDismissed(true);
  };

  const handleSpawnAdventure = async () => {
    if (!adventureSelected && !adventureDescription.trim()) return;
    setAdventureLoading(true);
    try {
      const payload = adventureSelected
        ? { adventure_id: adventureSelected.id, description: adventureDescription || adventureSelected.tagline }
        : { adventure_id: 'custom', description: adventureDescription };
      await api.post('/adventures/start', payload);
      setAdventureSpawned(true);
    } catch {
      // silently ignore
    } finally {
      setAdventureLoading(false);
    }
  };

  const handleDismissBriefing = () => {
    api.post('/briefing/dismiss', {}).catch(() => {});
    setBriefing({ show: false, briefing: null });
  };

  const handleShowBriefing = () => {
    api.post('/briefing/undismiss', {}).catch(() => {});
    setBriefingLoading(true);
    const poll = () => {
      api.get<BriefingData>('/briefing')
        .then((res) => {
          if (res.show && !res.briefing) {
            setTimeout(poll, 2000);
          } else {
            setBriefing(res);
            setBriefingLoading(false);
            writeBriefingSeed(res);
          }
        })
        .catch(() => setBriefingLoading(false));
    };
    poll();
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
    'Open Chat': () => toggleChat(),
  };

  const quickLaunch = [
    { icon: 'add_task', label: 'New Task', color: 'text-blue-400', hoverBorder: 'hover:border-blue-500' },
    { icon: 'bolt', label: 'Spawn Agent', color: 'text-purple-400', hoverBorder: 'hover:border-purple-500' },
    { icon: 'chat', label: 'Open Chat', color: 'text-cyan-400', hoverBorder: 'hover:border-cyan-500' },
  ];

  const [nextMeeting, setNextMeeting] = useState<CalendarEvent | null | undefined>(undefined);


  // cardClass replaced by Card component. Use: <Card hover padding="sm" className="sm:p-6">

  const hour = new Date().getHours();

  // Motivational quotes for the "quote" greeting style
  const motivationalQuotes = [
    'Small steps every day lead to big results.',
    'Focus on progress, not perfection.',
    'You are closer than you think.',
    'One thing at a time.',
    'Make today count.',
    'Trust the process.',
    'Keep building.',
  ];
  const quoteOfDay = motivationalQuotes[new Date().getDay()];

  let greetingLabel: string;
  let greetingSubtitle: string;
  if (greetingStyle === 'none') {
    greetingLabel = '';
    greetingSubtitle = '';
  } else if (greetingStyle === 'quote') {
    greetingLabel = '';
    greetingSubtitle = quoteOfDay;
  } else {
    // Default: time-based
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
  }

  // Render the Briefing banner (or its skeleton while loading).
  // Returns null when the briefing is dismissed or unavailable.
  const renderBriefing = () => {
    if (briefingLoading) {
      return (
        <div key="briefing" data-testid="widget-briefing" className="mb-6">
          <Card padding="sm" className="p-5">
            <div className="flex flex-col gap-2">
              <SkeletonLine width="w-1/4" />
              <SkeletonLine width="w-3/4" />
              <SkeletonLine width="w-2/3" />
            </div>
          </Card>
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
                <div className="flex items-center gap-2 mb-1.5">
                  <p className="text-xs font-medium text-blue-400 uppercase tracking-wide">{greetingLabel}</p>
                  {briefingRefreshing && (
                    <span
                      data-testid="briefing-refreshing"
                      className="text-[10px] font-medium text-blue-300/70 uppercase tracking-wide"
                    >
                      Refreshing...
                    </span>
                  )}
                </div>
                <div className="text-sm text-slate-200 leading-relaxed space-y-3">
                  {briefing.briefing.split(/\n\n+/).map((para, i) => (
                    <p key={i}>{renderMarkdown(para)}</p>
                  ))}
                </div>
                {briefing.action_items && briefing.action_items.length > 0 && (
                  <div className="mt-4 flex flex-wrap gap-2" data-testid="briefing-action-items">
                    {briefing.action_items.map((item, i) => {
                      const iconMap: Record<string, string> = {
                        reply_email: 'reply',
                        close_task: 'task_alt',
                        prep_meeting: 'event_note',
                        review_agent: 'smart_toy',
                      };
                      const colorMap: Record<string, string> = {
                        reply_email: 'bg-blue-500/20 text-blue-300 hover:bg-blue-500/30',
                        close_task: 'bg-emerald-500/20 text-emerald-300 hover:bg-emerald-500/30',
                        prep_meeting: 'bg-purple-500/20 text-purple-300 hover:bg-purple-500/30',
                        review_agent: 'bg-orange-500/20 text-orange-300 hover:bg-orange-500/30',
                      };
                      return (
                        <button
                          key={i}
                          onClick={() => {
                            const url = item.action_url;
                            if (url.startsWith('/api/')) {
                              // API action: navigate to the task/resource view
                              navigate(url.replace('/api/tasks/', '/tasks?focus='));
                            } else {
                              navigate(url);
                            }
                          }}
                          title={item.context}
                          className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${colorMap[item.type] || 'bg-slate-700/50 text-slate-300 hover:bg-slate-700'}`}
                        >
                          <Icon name={iconMap[item.type] || 'bolt'} size={14} />
                          {item.label}
                        </button>
                      );
                    })}
                  </div>
                )}
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
    //
    // Copy rule: only say "dismissed" when the user actually dismissed
    // it today. If the fetch failed (network, backend down, stuck
    // proxy) we mark it unavailable instead. Saying "dismissed" after
    // a failed fetch confused Tori into thinking she had clicked X
    // when she had not. See needle 281.
    const briefingUnavailable = briefing?.unavailable === true;
    const emptyCopy = briefingUnavailable
      ? "Briefing is temporarily unavailable. Check your connection and try again."
      : "Your briefing was dismissed for today.";
    const emptyAction = briefingUnavailable ? "Retry" : "Show briefing";
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
            <p className="text-sm text-slate-400 leading-relaxed mb-2">
              {emptyCopy}
            </p>
            <button
              onClick={handleShowBriefing}
              className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
            >
              {emptyAction}
            </button>
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
          className="mb-6 bg-slate-900/40 border border-slate-800 p-4 sm:p-6 rounded-xl"
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
        onClick={() => navigate(`/tasks?focus=${encodeURIComponent(compounds.top!.id)}`)}
        className="mb-6 bg-gradient-to-r from-pink-500/10 to-purple-500/10 border border-pink-500/30 p-4 sm:p-6 rounded-xl hover:border-pink-500/50 transition-colors cursor-pointer"
      >
        <div className="flex items-center gap-3 mb-2">
          <div className="w-10 h-10 rounded-full bg-pink-500/20 flex items-center justify-center">
            <Icon name="priority_high" className="text-pink-400" size={22} />
          </div>
          <div className="flex items-center gap-3">
            <div>
              <p className="text-xs font-medium text-pink-400 uppercase tracking-wide">Focus on this first</p>
              <h3 className="text-lg font-semibold text-white">{compounds.top.title}</h3>
            </div>
            <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-pink-500/20 text-pink-300 text-xs font-medium rounded-full whitespace-nowrap">
              <Icon name="lock_open" size={12} />
              Unblocks {compounds.top.blocks_count}
            </span>
          </div>
        </div>
        <p className="text-sm text-slate-400 ml-[52px]">
          Finishing this unblocks {compounds.top.blocks_count} other {compounds.top.blocks_count === 1 ? 'task' : 'tasks'}.
          Getting it done first lets everything else move forward.
        </p>
        {compounds.all.length > 1 && (
          <div className="mt-3 ml-[52px] space-y-1.5">
            {compounds.all.slice(1, 4).map((task) => (
              <div
                key={task.id}
                onClick={(e) => {
                  e.stopPropagation();
                  navigate(`/tasks?focus=${encodeURIComponent(task.id)}`);
                }}
                className="flex items-center gap-2 text-sm text-slate-400 hover:text-slate-200 cursor-pointer transition-colors"
              >
                <span className="truncate">{task.title}</span>
                <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 bg-slate-800 text-slate-400 text-[10px] font-medium rounded-full whitespace-nowrap shrink-0">
                  <Icon name="lock_open" size={10} />
                  {task.blocks_count}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  const renderTodaysFocus = () => (
    <div key="todays_focus" data-testid="widget-todays-focus">
    <Card hover padding="sm" className="sm:p-6">
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
    </Card>
    </div>
  );

  const renderQuickLaunch = () => (
    <div key="quick_launch" data-testid="widget-quick-launch">
    <Card hover padding="sm" className="sm:p-6">
      <h2 className="text-lg font-semibold mb-4">Quick Launch</h2>
      <div className="grid grid-cols-2 gap-2 sm:gap-3">
        {quickLaunch.map((item) => (
          <button
            key={item.label}
            onClick={quickLaunchActions[item.label]}
            className={`flex flex-col items-center p-3 sm:p-4 min-h-[44px] bg-slate-900 rounded-lg border border-slate-800 ${item.hoverBorder} transition-colors`}
          >
            <Icon name={item.icon} className={item.color} size={24} />
            <span className="text-sm text-slate-300 mt-2">{item.label}</span>
          </button>
        ))}
      </div>
    </Card>
    </div>
  );

  const renderNextMeeting = () => {
    if (!nextMeeting) {
      return (
        <div key="next_meeting" data-testid="widget-next-meeting" className="lg:col-span-2">
        <Card
          hover
          padding="sm"
          className="sm:p-6"
          onClick={() => navigate('/calendar')}
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
        </Card>
        </div>
      );
    }
    return (
      <div key="next_meeting" data-testid="widget-next-meeting" className="lg:col-span-2">
      <Card
        hover
        padding="sm"
        className="sm:p-6"
        onClick={() => navigate('/calendar')}
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
      </Card>
      </div>
    );
  };

  const renderDaySummary = () => {
    // When a widget is in the user's dashboard list it should always
    // render. The briefing banner and Day Summary are now independent
    // cards the user controls via Customize.
    return (
      <div key="day_summary" data-testid="widget-day-summary">
      <Card hover padding="sm" className="sm:p-6">
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
            <p className="text-sm text-slate-500">Nothing to summarize yet. Once you start using myOS, a daily recap will appear here.</p>
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
      </Card>
      </div>
    );
  };

  const renderLiveSessions = () => {
    const sessions = liveSessions?.sessions ?? [];
    const activeCount = liveSessions?.active_count ?? 0;
    const idleCount = liveSessions?.idle_count ?? 0;
    const total = activeCount + idleCount;

    const lastToolLabel = (session: LiveSession) => {
      const events = session.recent_events;
      if (!events.length) return 'no recent activity';
      const last = events[events.length - 1];
      return last.type || 'event';
    };

    const timeAgo = (isoStr: string) => {
      try {
        const diff = Date.now() - new Date(isoStr).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return 'just now';
        if (mins === 1) return '1 min ago';
        return `${mins} min ago`;
      } catch {
        return '';
      }
    };

    return (
      <div key="live_sessions" data-testid="widget-live-sessions">
      <Card hover padding="sm" className="sm:p-6">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Icon name="terminal" className="text-emerald-400" size={20} />
            <h2 className="text-lg font-semibold">Live Sessions</h2>
          </div>
          <div className="flex items-center gap-2 text-xs">
            {activeCount > 0 && (
              <span className="flex items-center gap-1 text-green-400">
                <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
                {activeCount} active
              </span>
            )}
            {idleCount > 0 && (
              <span className="flex items-center gap-1 text-amber-400">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
                {idleCount} idle
              </span>
            )}
          </div>
        </div>
        {total === 0 ? (
          <p className="text-sm text-slate-500">No active sessions right now.</p>
        ) : (
          <div className="space-y-2">
            {sessions.map((s) => (
              <div
                key={s.session_id}
                className="flex items-center gap-3 px-3 py-2 rounded-lg bg-slate-800/40"
              >
                <span
                  className={`w-2 h-2 rounded-full shrink-0 ${
                    s.status === 'active' ? 'bg-green-400 animate-pulse' : 'bg-amber-400'
                  }`}
                />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-200 truncate">{s.session_id}</p>
                  <p className="text-xs text-slate-500 truncate">
                    {lastToolLabel(s)} . {timeAgo(s.last_active)}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
      </div>
    );
  };

  const renderAdventure = () => {
    if (adventureDismissed) return null;
    return (
      <div key="adventure" data-testid="widget-adventure" className="lg:col-span-2">
      <Card hover padding="sm" className="sm:p-6">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-indigo-500/20 flex items-center justify-center shrink-0">
              <Icon name="rocket_launch" className="text-indigo-400" size={20} />
            </div>
            <div>
              <p className="text-xs font-medium text-indigo-400 uppercase tracking-wide mb-0.5">Try an Adventure</p>
              <p className="font-semibold">Pick something to get started on</p>
            </div>
          </div>
          <button
            onClick={handleDismissAdventure}
            aria-label="Dismiss adventure card"
            data-testid="adventure-dismiss"
            className="text-slate-500 hover:text-slate-300 transition-colors shrink-0 mt-0.5"
          >
            <Icon name="close" size={18} />
          </button>
        </div>

        {adventureSpawned ? (
          <div data-testid="adventure-spawned-banner" className="flex items-center gap-2 px-4 py-3 bg-green-500/10 border border-green-500/30 rounded-lg text-sm text-green-300">
            <Icon name="check_circle" size={16} />
            <span>Agent started.</span>
            <button
              onClick={() => navigate('/agents')}
              className="ml-1 underline hover:text-green-200 transition-colors"
              data-testid="adventure-agents-link"
            >
              See the Agents page
            </button>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4" data-testid="adventure-cards">
              {adventureTemplates.map((adv) => (
                <button
                  key={adv.id}
                  onClick={() => setAdventureSelected(adventureSelected?.id === adv.id ? null : adv)}
                  data-testid={`adventure-card-${adv.id}`}
                  className={`text-left p-3 rounded-lg border transition-colors ${
                    adventureSelected?.id === adv.id
                      ? 'border-indigo-500 bg-indigo-500/10'
                      : 'border-slate-700 bg-slate-900/50 hover:border-slate-600'
                  }`}
                >
                  <Icon name={adv.icon} size={16} className="text-indigo-400 mb-1" />
                  <p className="text-xs font-medium text-slate-200 leading-snug">{adv.title}</p>
                </button>
              ))}
            </div>

            <div className="flex gap-2">
              <input
                type="text"
                value={adventureDescription}
                onChange={(e) => setAdventureDescription(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && !adventureLoading) handleSpawnAdventure(); }}
                placeholder={adventureSelected ? adventureSelected.placeholder : 'Describe what you want to do...'}
                data-testid="adventure-description-input"
                className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 transition-colors"
              />
              <button
                onClick={handleSpawnAdventure}
                disabled={!adventureSelected && !adventureDescription.trim() || adventureLoading}
                data-testid="adventure-spawn-button"
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/40 disabled:cursor-not-allowed rounded-lg text-sm font-medium text-white transition-colors whitespace-nowrap"
              >
                {adventureLoading ? 'Starting...' : 'Spawn agent'}
              </button>
            </div>
          </>
        )}
      </Card>
      </div>
    );
  };

  const renderRecentSpecs = () => (
    <div key="recent_specs" data-testid="widget-recent-specs">
      <RecentSpecsWidget />
    </div>
  );

  // Map of widget id to render function. Only widgets present in
  // dashboardWidgets render, and they render in that order. Widgets above
  // the grid (briefing and focus first) are full width banners,
  // the rest go inside the two column grid.
  const widgetRenderers: Record<string, () => ReactNode> = {
    briefing: renderBriefing,
    focus_first: renderFocusFirst,
    adventure: renderAdventure,
    todays_focus: renderTodaysFocus,
    quick_launch: renderQuickLaunch,
    next_meeting: renderNextMeeting,
    day_summary: renderDaySummary,
    live_sessions: renderLiveSessions,
    recent_specs: renderRecentSpecs,
  };

  const [widgetMenuOpen, setWidgetMenuOpen] = useState<string | null>(null);

  const removeWidget = (id: string) => {
    const next = dashboardWidgets.filter((w) => w !== id);
    setDashboardWidgets(next);
    setWidgetMenuOpen(null);
  };

  const bannerIds = new Set(['briefing', 'focus_first']);
  // In focus mode, only show the first 3 widgets total (banners + grid cards)
  const focusLimit = dashboardLayout === 'focus' ? 3 : Infinity;
  let focusCount = 0;
  const visibleBanners = dashboardWidgets.filter((id) => {
    if (!bannerIds.has(id)) return false;
    if (focusCount >= focusLimit) return false;
    focusCount++;
    return true;
  });
  const visibleGridCards = dashboardWidgets.filter((id) => {
    if (bannerIds.has(id) || !widgetRenderers[id]) return false;
    if (focusCount >= focusLimit) return false;
    focusCount++;
    return true;
  });

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <TopBar title="Home" />

      <div className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8">
        {visibleBanners.map((id) => widgetRenderers[id]?.())}

        {/* Greeting + Customize button */}
        <div className="mb-6 sm:mb-8 flex items-start justify-between gap-4">
          <div data-tour="dashboard">
            <h1 className="text-2xl sm:text-3xl font-bold mb-1">Welcome to {displayOsName}</h1>
            {greetingSubtitle && <p className="text-slate-400">{greetingSubtitle}</p>}
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
                  className="p-1 rounded-md text-slate-400 hover:text-slate-600 dark:hover:text-white transition-colors"
                  aria-label={`Widget options for ${id}`}
                >
                  <Icon name="more_vert" size={16} />
                </button>
                {widgetMenuOpen === id && (
                  <div className="absolute right-0 mt-1 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg shadow-lg py-1 z-10 min-w-[140px]">
                    <button
                      onClick={() => removeWidget(id)}
                      className="w-full text-left px-3 py-1.5 text-sm text-red-500 dark:text-red-400 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors flex items-center gap-2"
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
        className="fixed bottom-4 right-4 sm:bottom-8 sm:right-8 w-14 h-14 bg-blue-500 hover:bg-blue-600 rounded-2xl flex items-center justify-center shadow-lg shadow-blue-500/25 transition-colors"
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
      <DashboardCustomizeModal
        open={customizeOpen}
        onClose={() => setCustomizeOpen(false)}
        widgets={dashboardWidgets}
        onSave={(next) => setDashboardWidgets(next)}
      />
    </div>
  );
}
