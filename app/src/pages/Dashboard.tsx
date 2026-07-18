import { useState, useEffect, useCallback, type MouseEvent, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import Icon from '../components/Icon';
import PageShell from '../components/PageShell';
import QuickAddTaskModal from '../components/QuickAddTaskModal';
import QuickSpawnAgentModal from '../components/QuickSpawnAgentModal';
import DashboardCustomizeModal from '../components/DashboardCustomizeModal';
import RecentSpecsWidget from '../components/RecentSpecsWidget';
import JiraWidget from '../components/JiraWidget';
import ConfluenceWidget from '../components/ConfluenceWidget';
import CompetitiveIntelWidget from '../components/CompetitiveIntelWidget';
import BlockersWidget from '../components/BlockersWidget';
import DependencyMapWidget from '../components/DependencyMapWidget';
import QueryWidget from '../components/QueryWidget';
import { QUERY_PRESETS } from '../components/queryPresets';
import { Card, SkeletonLine } from '../components/ui';
import { api } from '../lib/api';
import { reportError } from '../lib/reportError';
import { renderMarkdown } from '../lib/markdown';
import { useAppStore } from '../stores/app';
import { useDashboardStore } from '../stores/dashboardStore';
import { useDashboardFeed } from '../hooks/useDashboardFeed';
import { ADVENTURE_DISMISSED_KEY, pickTryNowSuggestion, type AdventureTemplate, type TryNowSuggestion } from '../lib/adventures';
import { ClampedDescription } from '../components/ClampedDescription';
import CalendarGridWidget from '../components/CalendarGridWidget';

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
  colorId?: string
}

interface SessionDiff {
  files_changed: string[];
  needles_filed: { id: string; priority: string; title: string }[];
  audit_events: { count: number; event: string }[];
  audit_total: number;
}




const focusIcons = ['code', 'mail', 'smart_toy', 'target', 'bolt'];
const focusColors = [
  'bg-pink-500/20 text-pink-600 dark:text-pink-400',
  'bg-blue-500/20 text-blue-600 dark:text-blue-400',
  'bg-purple-500/20 text-purple-600 dark:text-purple-400',
  'bg-cyan-500/20 text-cyan-600 dark:text-cyan-400',
  'bg-orange-500/20 text-orange-600 dark:text-orange-400',
];

// Calendar widget range. Drives the day/week/month selector on the
// dashboard's calendar card. Stored as a string in localStorage so
// the user's choice survives a reload.
type CalendarRange = 'day' | 'week' | 'month';

const CALENDAR_RANGE_KEY = 'myos.calendar_widget_range';

// How many days each range covers. Matches the values the backend
// /calendar/events route accepts on the ?days= query param.
const CALENDAR_RANGE_DAYS: Record<CalendarRange, number> = {
  day: 1,
  week: 7,
  month: 30,
};

// Plain-language label shown in the widget's selector. No jargon, no
// abbreviations, so the meaning is obvious at a glance.
const CALENDAR_RANGE_LABEL: Record<CalendarRange, string> = {
  day: 'Day',
  week: 'Week',
  month: 'Month',
};

function readCalendarRange(): CalendarRange {
  if (typeof window === 'undefined') return 'month';
  try {
    const raw = localStorage.getItem(CALENDAR_RANGE_KEY);
    if (raw === 'day' || raw === 'week' || raw === 'month') return raw;
  } catch {
    // localStorage may throw in private windows. Fall back to default.
  }
  return 'month';
}

export default function Dashboard() {
  useDashboardFeed();
  const navigate = useNavigate();
  const toggleChat = useAppStore((s) => s.toggleChat);
  const setChatPrefill = useAppStore((s) => s.setChatPrefill);
  const setChatOpen = useAppStore((s) => s.setChatOpen);
  const liveAgentsCount = useDashboardStore((s) => s.agentsCount);
  const liveTasksCount = useDashboardStore((s) => s.tasksCount);
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
  // UAT item 1: collapse a long calendar list in the briefing to the first 5,
  // with a "View all" toggle, so a busy day does not run off the banner.
  const [briefingEventsExpanded, setBriefingEventsExpanded] = useState(false);
  // Calendar widget range selector. Persisted to localStorage so a
  // reload restores the user's choice. Default is Week to match the
  // historical behavior from before the selector landed.
  const [calendarRange, setCalendarRange] = useState<CalendarRange>(() => readCalendarRange());
  // Events filtered to the chosen range, capped so the widget stays
  // compact. Undefined while the first fetch is in flight, [] when
  // the fetch returns nothing in the window.
  const [calendarEvents, setCalendarEvents] = useState<CalendarEvent[] | undefined>(undefined);
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
  // →2920: "One thing to try right now" suggestion, moved here from the
  // onboarding wizard. Null until the connection checks resolve.
  const [tryNow, setTryNow] = useState<TryNowSuggestion | null>(null);


  const fetchSummary = useCallback(async () => {
    setSummaryLoading(true);
    try {
      const res = await api.get<{ bullets: string[] }>('/dashboard/summary');
      setSummaryBullets(res.bullets);
    } catch (e) {
      reportError('Failed to fetch summary', e);
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
      reportError('Failed to fetch dashboard', e);
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

  // Calendar widget fetch. Re-runs whenever the user changes the range
  // selector. Also re-runs every 60s to stay fresh.
  // We pass ?days= only when the range is not the default Week, so the
  // existing 7-day cache hit path stays warm.
  // Events are filtered to the current period window (start of
  // day/week/month through end of the window). No hard cap — the
  // grid views manage their own display limits.
  const fetchCalendarEvents = useCallback(async (range: CalendarRange) => {
    const days = CALENDAR_RANGE_DAYS[range];
    const path = days === 7 ? '/calendar/events' : `/calendar/events?days=${days}`;
    let res: { events: CalendarEvent[] } | null = null;
    try {
      res = await api.get<{ events: CalendarEvent[] }>(path);
    } catch {
      res = null;
    }
    if (!res) {
      // UAT item 3: a transient fetch error must NOT blank a calendar that was
      // already populated. The 60s refetch hitting one network blip used to
      // clear everything. Keep the last good events; only fall back to empty on
      // the very first load (when there is nothing to preserve yet).
      setCalendarEvents((prev) => (prev && prev.length > 0 ? prev : []));
      return;
    }
    const now = new Date();
    let periodStart: Date;
    if (range === 'day') {
      periodStart = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0);
    } else if (range === 'week') {
      const d = new Date(now);
      d.setDate(d.getDate() - d.getDay());
      d.setHours(0, 0, 0, 0);
      periodStart = d;
    } else {
      periodStart = new Date(now.getFullYear(), now.getMonth(), 1, 0, 0, 0, 0);
    }
    const cutoff = periodStart.getTime() + days * 24 * 60 * 60 * 1000;
    const filtered = (res.events || []).filter((ev) => {
      const dateTime = ev.start?.dateTime;
      const dateOnly = ev.start?.date;
      let t: number;
      if (dateTime) {
        t = new Date(dateTime).getTime();
      } else if (dateOnly) {
        // UAT item 3: all-day events carry a date string with no time. Parsing
        // "2026-06-04" with new Date() treats it as UTC midnight, which in a
        // western timezone lands on the previous local day and drops today's
        // all-day events. Parse as LOCAL midnight so they stay visible.
        const [y, m, d] = dateOnly.split('-').map(Number);
        t = new Date(y, (m || 1) - 1, d || 1).getTime();
      } else {
        return false;
      }
      return t >= periodStart.getTime() && t <= cutoff;
    });
    setCalendarEvents(filtered);
  }, []);

  useEffect(() => {
    fetchCalendarEvents(calendarRange);
    const interval = setInterval(() => fetchCalendarEvents(calendarRange), 60000);
    return () => clearInterval(interval);
  }, [fetchCalendarEvents, calendarRange]);


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

  // →2920: pick the try-right-now suggestion from what's connected. A
  // failed check counts as not connected, so the card still shows the
  // default suggestion when the backend is unreachable.
  useEffect(() => {
    if (adventureDismissed) return;
    Promise.all([
      api.get<{ google_connected?: boolean }>('/secrets/key-status').catch(() => ({}) as { google_connected?: boolean }),
      api.get<{ connected?: boolean }>('/atlassian/status').catch(() => ({}) as { connected?: boolean }),
    ]).then(([keyStatus, atlStatus]) => {
      setTryNow(pickTryNowSuggestion(keyStatus.google_connected ?? false, atlStatus.connected ?? false));
    });
  }, [adventureDismissed]);


  const handleDismissAdventure = () => {
    localStorage.setItem(ADVENTURE_DISMISSED_KEY, 'true');
    setAdventureDismissed(true);
  };

  // →2920: clicking the try-right-now suggestion pre-fills the chat input
  // and opens the chat panel (same mechanism the wizard's card used).
  const handleTryNow = () => {
    if (!tryNow) return;
    setChatPrefill(tryNow.prompt);
    setChatOpen(true);
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
    { icon: 'add_task', label: 'New Task', color: 'text-blue-600 dark:text-blue-400', hoverBorder: 'hover:border-blue-500' },
    { icon: 'bolt', label: 'Spawn Agent', color: 'text-purple-600 dark:text-purple-400', hoverBorder: 'hover:border-purple-500' },
    { icon: 'chat', label: 'Open Chat', color: 'text-cyan-600 dark:text-cyan-400', hoverBorder: 'hover:border-cyan-500' },
  ];



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
                <Icon name="wb_sunny" className="text-blue-600 dark:text-blue-400" size={18} />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1.5">
                  <p className="text-xs font-medium text-blue-600 dark:text-blue-400 uppercase tracking-wide">{greetingLabel}</p>
                  {briefingRefreshing && (
                    <span
                      data-testid="briefing-refreshing"
                      className="text-[10px] font-medium text-blue-700 dark:text-blue-300/70 uppercase tracking-wide"
                    >
                      Refreshing...
                    </span>
                  )}
                </div>
                <div className="text-sm text-slate-800 dark:text-slate-200 leading-relaxed space-y-3">
                  {briefing.briefing.split(/\n\n+/).map((para, i) => {
                    // UAT item 1: the calendar block lists one bullet per event
                    // and can run off the card on a busy day. Show the first 5
                    // and a "View all N events" toggle for the rest.
                    if (para.includes('on the calendar today:')) {
                      const lines = para.split('\n');
                      const introLine = lines[0];
                      const bullets = lines.slice(1).filter((l) => l.trim().startsWith('-'));
                      const CAP = 5;
                      const shown = briefingEventsExpanded ? bullets : bullets.slice(0, CAP);
                      return (
                        <div key={i}>
                          {renderMarkdown(introLine)}
                          <div className="mt-1 space-y-1" data-testid="briefing-events-list">
                            {shown.map((b, bi) => (
                              <div key={bi} className="flex items-start gap-2">
                                <span className="mt-1.5 w-1 h-1 rounded-full bg-blue-400 shrink-0" />
                                <span className="min-w-0">{b.replace(/^[-\s]+/, '')}</span>
                              </div>
                            ))}
                          </div>
                          {bullets.length > CAP && (
                            <button
                              type="button"
                              onClick={() => setBriefingEventsExpanded((v) => !v)}
                              data-testid="briefing-events-toggle"
                              className="mt-1.5 text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline"
                            >
                              {briefingEventsExpanded ? 'Show fewer' : `View all ${bullets.length} events`}
                            </button>
                          )}
                        </div>
                      );
                    }
                    return <div key={i}>{renderMarkdown(para)}</div>;
                  })}
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
                        reply_email: 'bg-blue-500/20 text-blue-700 dark:text-blue-300 hover:bg-blue-500/30',
                        close_task: 'bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-500/30',
                        prep_meeting: 'bg-purple-500/20 text-purple-700 dark:text-purple-300 hover:bg-purple-500/30',
                        review_agent: 'bg-orange-500/20 text-orange-700 dark:text-orange-300 hover:bg-orange-500/30',
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
                          className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${colorMap[item.type] || 'bg-slate-500/20 text-slate-700 dark:text-slate-300 hover:bg-slate-500/30'}`}
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
              className="text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors shrink-0 mt-0.5"
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
        className="mb-6 bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 rounded-xl p-5"
      >
        <div className="flex items-start gap-3">
          <div className="w-8 h-8 rounded-full bg-amber-500/20 flex items-center justify-center shrink-0 mt-0.5">
            <Icon name="wb_sunny" className="text-amber-500 dark:text-amber-400" size={18} />
          </div>
          <div className="flex-1">
            <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1.5">{greetingLabel}</p>
            <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed mb-2">
              {emptyCopy}
            </p>
            <button
              onClick={handleShowBriefing}
              className="text-xs text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 transition-colors"
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
          className="mb-6 bg-white/40 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 p-4 sm:p-6 rounded-xl"
        >
          <div className="flex items-center gap-3 mb-2">
            <div className="w-10 h-10 rounded-full bg-pink-500/20 flex items-center justify-center">
              <Icon name="priority_high" className="text-pink-600 dark:text-pink-400" size={22} />
            </div>
            <div>
              <p className="text-xs font-medium text-pink-600 dark:text-pink-400 uppercase tracking-wide">Focus on this first</p>
              <h3 className="text-lg font-semibold text-white">Nothing blocking others right now</h3>
            </div>
          </div>
          <p className="text-sm text-slate-600 dark:text-slate-400 ml-[52px]">
            When a needle blocks several others, it will show up here so you know to finish it first.
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
            <Icon name="priority_high" className="text-pink-600 dark:text-pink-400" size={22} />
          </div>
          <div className="flex items-center gap-3">
            <div>
              <p className="text-xs font-medium text-pink-600 dark:text-pink-400 uppercase tracking-wide">Focus on this first</p>
              <h3 className="text-lg font-semibold text-white">{compounds.top.title}</h3>
            </div>
            <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-pink-500/20 text-pink-700 dark:text-pink-300 text-xs font-medium rounded-full whitespace-nowrap">
              <Icon name="lock_open" size={12} />
              Unblocks {compounds.top.blocks_count}
            </span>
          </div>
        </div>
        <p className="text-sm text-slate-600 dark:text-slate-400 ml-[52px]">
          Finishing this unblocks {compounds.top.blocks_count} other {compounds.top.blocks_count === 1 ? 'needle' : 'needles'}.
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
                className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 cursor-pointer transition-colors"
              >
                <span className="truncate">{task.title}</span>
                <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 text-[10px] font-medium rounded-full whitespace-nowrap shrink-0">
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
      <div className="flex items-center justify-between mb-4 pr-8">
        <div className="flex items-center gap-2">
          <Icon name="target" className="text-pink-600 dark:text-pink-400" size={20} />
          <h2 className="text-lg font-semibold">Today's Focus</h2>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-pink-600 dark:text-pink-400">{openCount} open</span>
          <span className="text-blue-600 dark:text-blue-400">{closedCount} done</span>
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
              className="flex items-center gap-4 p-3 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800/50 transition-colors cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500/50"
            >
              <div className={`w-10 h-10 rounded-full flex items-center justify-center ${task.color}`}>
                <Icon name={task.icon} size={20} />
              </div>
              <div className="flex-1 min-w-0">
                <ClampedDescription
                  text={task.title}
                  lines={2}
                  textClassName="font-medium"
                  testId="focus-task-expand"
                />
                <p className="text-sm text-slate-600 dark:text-slate-400">{task.subtitle}</p>
              </div>
              <Icon name="open_in_new" className="text-slate-500" size={18} />
            </div>
          ))
        )}
      </div>
      <hr className="border-slate-200 dark:border-slate-700 my-4" />
      <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-2">Day Summary</p>
      <div className="space-y-2">
        {summaryLoading && summaryBullets.length === 0 ? (
          <p className="text-sm text-slate-500">Loading summary...</p>
        ) : summaryBullets.length === 0 ? (
          <p className="text-sm text-slate-500">Nothing to summarize yet. Once you start using yourOS, a daily recap will appear here.</p>
        ) : (
          <ul className="space-y-2">
            {summaryBullets.map((bullet, i) => (
              <li key={i} className="flex items-start gap-3 text-sm text-slate-700 dark:text-slate-300">
                <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-cyan-400 shrink-0" />
                <span className="min-w-0 line-clamp-2 leading-snug">{bullet}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
    </div>
  );

  const renderQuickLaunch = () => (
    <div key="quick_launch" data-testid="widget-quick-launch">
    <Card hover padding="sm" className="sm:p-6">
      <h2 className="text-lg font-semibold mb-4 pr-8">Quick Launch</h2>
      <div className="grid grid-cols-2 gap-2 sm:gap-3">
        {quickLaunch.map((item) => (
          <button
            key={item.label}
            onClick={quickLaunchActions[item.label]}
            className={`flex flex-col items-center p-3 sm:p-4 min-h-[44px] bg-white dark:bg-slate-900 rounded-lg border border-slate-200 dark:border-slate-800 ${item.hoverBorder} transition-colors`}
          >
            <Icon name={item.icon} className={item.color} size={24} />
            <span className="text-sm text-slate-700 dark:text-slate-300 mt-2">{item.label}</span>
          </button>
        ))}
      </div>
    </Card>
    </div>
  );

  const renderNextMeeting = () => {
    const events = calendarEvents ?? [];
    const handleRangeClick = (range: CalendarRange) => (e: MouseEvent) => {
      // Stop propagation so clicking the selector does not also
      // navigate the user to the Calendar page (the wrapping Card
      // has an onClick that routes there).
      e.stopPropagation();
      if (range === calendarRange) return;
      setCalendarRange(range);
      try {
        localStorage.setItem(CALENDAR_RANGE_KEY, range);
      } catch {
        // localStorage write failures are non-fatal. The selection
        // will still apply for this session.
      }
    };
    const rangeSelector = (
      <div
        role="group"
        aria-label="Calendar range"
        className="inline-flex rounded-lg bg-slate-50/60 dark:bg-slate-800/60 p-0.5 text-xs"
        data-testid="calendar-range-selector"
        onClick={(e) => e.stopPropagation()}
      >
        {(['day', 'week', 'month'] as CalendarRange[]).map((range) => {
          const active = range === calendarRange;
          return (
            <button
              key={range}
              type="button"
              onClick={handleRangeClick(range)}
              aria-pressed={active}
              data-testid={`calendar-range-${range}`}
              className={`px-2.5 py-1 rounded-md transition-colors ${active ? 'bg-blue-500/30 text-blue-200' : 'text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200'}`}
            >
              {CALENDAR_RANGE_LABEL[range]}
            </button>
          );
        })}
      </div>
    );

    const header = (
      <div className="flex items-center justify-between mb-3 pr-8">
        <div className="flex items-center gap-2">
          <Icon name="calendar_month" className="text-blue-600 dark:text-blue-400" size={20} />
          <h2 className="text-lg font-semibold">Upcoming events</h2>
        </div>
        {rangeSelector}
      </div>
    );

    return (
      <div key="next_meeting" data-testid="widget-next-meeting" className="lg:col-span-2">
        <Card hover padding="sm" className="sm:p-6" onClick={() => navigate('/calendar')}>
          {header}
          <CalendarGridWidget
            events={events}
            range={calendarRange}
            loading={calendarEvents === undefined}
          />
        </Card>
      </div>
    );
  };

  const renderAdventure = () => {
    if (adventureDismissed) return null;
    return (
      <div key="adventure" data-testid="widget-adventure" className="lg:col-span-2">
      <Card hover padding="sm" className="sm:p-6">
        <div className="flex items-start gap-4 mb-4 pr-8">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-indigo-500/20 flex items-center justify-center shrink-0">
              <Icon name="rocket_launch" className="text-indigo-600 dark:text-indigo-400" size={20} />
            </div>
            <div>
              <p className="text-xs font-medium text-indigo-600 dark:text-indigo-400 uppercase tracking-wide mb-0.5">Try an Adventure</p>
              <p className="font-semibold">Pick something to get started on</p>
            </div>
          </div>
        </div>

        {adventureSpawned ? (
          <div data-testid="adventure-spawned-banner" className="flex items-center gap-2 px-4 py-3 bg-green-500/10 border border-green-500/30 rounded-lg text-sm text-green-700 dark:text-green-300">
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
            {tryNow && (
              <div className="mb-4" data-testid="adventure-try-now">
                <p className="text-xs font-medium text-slate-500 dark:text-slate-400 mb-1.5">One thing to try right now</p>
                <button
                  onClick={handleTryNow}
                  data-testid="adventure-try-now-btn"
                  className="w-full text-left p-3 rounded-lg border border-indigo-300 dark:border-indigo-700 bg-indigo-500/5 hover:bg-indigo-500/10 transition-colors"
                >
                  <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{tryNow.label}</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400 mb-1">{tryNow.description}</p>
                  <p className="text-xs text-indigo-600 dark:text-indigo-400 italic" data-testid="adventure-try-now-text">&ldquo;{tryNow.prompt}&rdquo;</p>
                </button>
              </div>
            )}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4" data-testid="adventure-cards">
              {adventureTemplates.map((adv) => (
                <button
                  key={adv.id}
                  onClick={() => setAdventureSelected(adventureSelected?.id === adv.id ? null : adv)}
                  data-testid={`adventure-card-${adv.id}`}
                  className={`text-left p-3 rounded-lg border transition-colors ${
                    adventureSelected?.id === adv.id
                      ? 'border-indigo-500 bg-indigo-500/10'
                      : 'border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900/50 hover:border-slate-600'
                  }`}
                >
                  <Icon name={adv.icon} size={16} className="text-indigo-600 dark:text-indigo-400 mb-1" />
                  <p className="text-xs font-medium text-slate-800 dark:text-slate-200 leading-snug">{adv.title}</p>
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
                className="flex-1 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-900 dark:text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 transition-colors"
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

  const renderJira = () => (
    <div key="jira" data-testid="widget-jira">
      <JiraWidget />
    </div>
  );

  const renderConfluence = () => (
    <div key="confluence" data-testid="widget-confluence">
      <ConfluenceWidget />
    </div>
  );

  const renderCompetitiveIntel = () => (
    <div key="competitive_intel" data-testid="widget-competitive-intel-wrapper">
      <CompetitiveIntelWidget />
    </div>
  );

  const renderBlockersWidget = () => (
    <div key="blockers_widget" data-testid="widget-blockers-widget">
      <BlockersWidget />
    </div>
  );

  const renderDependencyMapWidget = () => (
    <div key="dependency_map_widget" data-testid="widget-dependency-map-widget">
      <DependencyMapWidget />
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
    recent_specs: renderRecentSpecs,
    jira: renderJira,
    confluence: renderConfluence,
    competitive_intel: renderCompetitiveIntel,
    blockers_widget: renderBlockersWidget,
    dependency_map_widget: renderDependencyMapWidget,
    // Saved-view preset cards all render through the one QueryWidget.
    ...Object.fromEntries(
      QUERY_PRESETS.map((p) => [p.id, () => <QueryWidget key={p.id} preset={p} />]),
    ),
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
    <PageShell title="Home"
      data-live-agents={String(liveAgentsCount)}
      data-live-tasks={String(liveTasksCount)}
    >
        {visibleBanners.map((id) => widgetRenderers[id]?.())}

        {/* Greeting + Customize button */}
        <div className="mb-6 sm:mb-8 flex items-start justify-between gap-4">
          <div data-tour="dashboard">
            <h1 className="text-2xl sm:text-3xl font-bold mb-1">Welcome to {displayOsName}</h1>
            {greetingSubtitle && <p className="text-slate-600 dark:text-slate-400">{greetingSubtitle}</p>}
          </div>
          <button
            onClick={() => setCustomizeOpen(true)}
            aria-label="Customize dashboard"
            className="flex items-center gap-1.5 px-3 py-1.5 bg-white/60 dark:bg-slate-900/60 hover:bg-slate-100 dark:hover:bg-slate-800 border border-slate-200 dark:border-slate-800 hover:border-slate-200 dark:hover:border-slate-700 rounded-lg text-sm text-slate-700 dark:text-slate-300 transition-colors shrink-0"
          >
            <Icon name="dashboard_customize" size={16} />
            Customize
          </button>
        </div>

        {/* Widget Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {visibleGridCards.map((id) => (
            // UAT item 2: full-width widgets (calendar, adventure) put
            // lg:col-span-2 on their inner div, but the grid's direct child is
            // THIS wrapper, so the span never applied and the calendar rendered
            // at half width. Apply the span here, on the actual grid child.
            <div
              key={`wrap-${id}`}
              className={`relative group/widget [&>*:first-child]:h-full [&>*:first-child>*:first-child]:h-full ${id === 'next_meeting' || id === 'adventure' ? 'lg:col-span-2' : ''}`}
            >
              {widgetRenderers[id]?.()}
              <div className="absolute top-3 right-3 opacity-0 group-hover/widget:opacity-100 transition-opacity">
                <button
                  onClick={() => setWidgetMenuOpen(widgetMenuOpen === id ? null : id)}
                  className="p-1 rounded-md text-slate-600 dark:text-slate-400 hover:text-slate-600 dark:hover:text-white transition-colors"
                  aria-label={`Widget options for ${id}`}
                  data-testid={`widget-menu-trigger-${id}`}
                >
                  <Icon name="more_vert" size={16} />
                </button>
                {widgetMenuOpen === id && (
                  <div className="absolute right-0 mt-1 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg shadow-lg py-1 z-10 min-w-[140px]">
                    {id === 'adventure' && (
                      <button
                        onClick={() => { handleDismissAdventure(); setWidgetMenuOpen(null); }}
                        data-testid="widget-menu-adventure-dismiss"
                        className="w-full text-left px-3 py-1.5 text-sm text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors flex items-center gap-2"
                      >
                        <Icon name="close" size={14} />
                        Dismiss
                      </button>
                    )}
                    {id === 'todays_focus' && (
                      <button
                        onClick={() => { fetchSummary(); setWidgetMenuOpen(null); }}
                        disabled={summaryLoading}
                        data-testid="widget-menu-todays-focus-refresh"
                        className="w-full text-left px-3 py-1.5 text-sm text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-200 dark:hover:bg-slate-700 disabled:opacity-50 transition-colors flex items-center gap-2"
                      >
                        <Icon name="refresh" size={14} className={summaryLoading ? 'animate-spin' : ''} />
                        Refresh summary
                      </button>
                    )}
                    <button
                      onClick={() => removeWidget(id)}
                      className="w-full text-left px-3 py-1.5 text-sm text-red-500 dark:text-red-400 hover:bg-slate-100 dark:hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors flex items-center gap-2"
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
    </PageShell>
  );
}
