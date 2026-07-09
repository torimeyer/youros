import { useState, useEffect, useCallback, lazy, Suspense } from "react";
import PatternPanel from "../components/PatternPanel";
import Icon from "../components/Icon";
import PageShell from "../components/PageShell";
import { api } from "../lib/api";
import { useWebSocket } from "../hooks/useWebSocket";
import { reportError } from '../lib/reportError';
import { LoadingState, EmptyState } from "../components/ui";
import {
  buildStream,
  groupByDay,
  bundleEntries,
  paletteClasses,
  type RawActivityEvent,
  type StreamEntry,
  type DayGroup,
  type EntryBundle,
} from "../lib/activityStream";

// Re-export for tests that import from here
export { buildStream, groupByDay, bundleEntries } from "../lib/activityStream";

const Transcripts = lazy(() => import("./Transcripts"));

interface ActivityResponse {
  events: RawActivityEvent[];
  count: number;
}

type Tab = "events" | "transcripts" | "learned" | "reminders";

interface ReminderRow {
  id: string;
  text: string;
  fire_at_utc: string;
  created_at?: string;
  status: "scheduled" | "delivered" | "cancelled";
  channel?: string;
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

// ─── Single stream row ──────────────────────────────────────────────────────

interface StreamRowProps {
  entry: StreamEntry;
  expanded: boolean;
  onToggle: () => void;
}

function StreamRow({ entry, expanded, onToggle }: StreamRowProps) {
  const { bg, text } = paletteClasses[entry.palette];

  return (
    <div>
      <button
        type="button"
        className="w-full flex items-start gap-3 px-4 py-2.5 rounded-lg hover:bg-white/60 dark:hover:bg-slate-900/60 transition-colors text-left"
        onClick={onToggle}
        aria-expanded={expanded}
        data-testid="stream-row"
        data-kind={entry.kind}
      >
        {/* Leading icon */}
        <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5 ${bg} ${text}`}>
          <Icon name={entry.icon} size={16} />
        </div>

        {/* Summary */}
        <div className="flex-1 min-w-0">
          <span className="text-sm font-medium text-slate-800 dark:text-slate-200">{entry.summary}</span>
        </div>

        {/* Timestamp + expand chevron */}
        <div className="flex items-center gap-2 shrink-0 mt-1">
          <span className="text-xs text-slate-500 font-mono">{formatTime(entry.timestamp)}</span>
          <Icon
            name={expanded ? "expand_less" : "expand_more"}
            size={14}
            className="text-slate-600"
          />
        </div>
      </button>

      {/* Technical detail drawer */}
      {expanded && (
        <div
          className="ml-11 mb-1 px-4 py-3 rounded-lg bg-white/60 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 text-xs text-slate-600 dark:text-slate-400 space-y-1"
          data-testid="stream-row-detail"
        >
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span>
              <span className="text-slate-600">event</span>{" "}
              <span className="font-mono text-slate-700 dark:text-slate-300">{entry.raw.event}</span>
            </span>
            <span>
              <span className="text-slate-600">category</span>{" "}
              <span className="font-mono text-slate-700 dark:text-slate-300">{entry.raw.category}</span>
            </span>
            <span>
              <span className="text-slate-600">time</span>{" "}
              <span className="font-mono text-slate-700 dark:text-slate-300">{entry.timestamp}</span>
            </span>

          </div>
          {entry.raw.detail && (
            <p className="text-slate-500 break-words">{entry.raw.detail}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Bundled row (N consecutive similar entries) ──────────────────────────

interface BundleRowProps {
  bundle: EntryBundle;
  expanded: boolean;
  onToggle: () => void;
}

/**
 * Pull a short per-entry label for the bundle drawer. Prefers the agent
 * name when present, otherwise falls back to the first task-title-ish
 * field in the raw detail. Keeps things plain and non-empty.
 */
function entryDetailLabel(entry: StreamEntry): string {
  const detail = entry.raw.detail || "";
  const nameMatch = detail.match(/name="([^"]+)"/);
  if (nameMatch) return nameMatch[1];
  const titleMatch = detail.match(/title="([^"]+)"/);
  if (titleMatch) return titleMatch[1];
  const arrow = detail.match(/→\d+\s+(.+)/);
  if (arrow) return arrow[1].trim();
  return entry.raw.label || entry.summary;
}

function BundleRow({ bundle, expanded, onToggle }: BundleRowProps) {
  const { lead, entries } = bundle;
  const count = entries.length;
  const { bg, text } = paletteClasses[lead.palette];

  return (
    <div>
      <button
        type="button"
        className="w-full flex items-start gap-3 px-4 py-2.5 rounded-lg hover:bg-white/60 dark:hover:bg-slate-900/60 transition-colors text-left"
        onClick={onToggle}
        aria-expanded={expanded}
        data-testid="stream-bundle"
        data-kind={lead.kind}
        data-count={count}
      >
        {/* Leading icon */}
        <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5 ${bg} ${text}`}>
          <Icon name={lead.icon} size={16} />
        </div>

        {/* Summary with count badge */}
        <div className="flex-1 min-w-0 flex items-center gap-2">
          <span className="text-sm font-medium text-slate-800 dark:text-slate-200 truncate">{lead.summary}</span>
          <span
            className="text-xs font-medium bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 px-1.5 py-0.5 rounded"
            data-testid="bundle-count"
          >
            {"\u00d7"} {count}
          </span>
        </div>

        {/* Timestamp + expand chevron */}
        <div className="flex items-center gap-2 shrink-0 mt-1">
          <span className="text-xs text-slate-500 font-mono">{formatTime(lead.timestamp)}</span>
          <Icon
            name={expanded ? "expand_less" : "expand_more"}
            size={14}
            className="text-slate-600"
          />
        </div>
      </button>

      {/* Expanded list of individual entries */}
      {expanded && (
        <div
          className="ml-11 mb-1 px-4 py-3 rounded-lg bg-white/60 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 text-xs text-slate-600 dark:text-slate-400"
          data-testid="stream-bundle-detail"
        >
          <ul className="space-y-1">
            {entries.map((entry) => (
              <li
                key={entry.key}
                className="flex items-start gap-3"
                data-testid="bundle-entry"
              >
                <span className="font-mono text-slate-500 shrink-0">{formatTime(entry.timestamp)}</span>
                <span className="text-slate-700 dark:text-slate-300 break-words">{entryDetailLabel(entry)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ─── Day section ────────────────────────────────────────────────────────────

interface DaySectionProps {
  group: DayGroup;
  expandedKeys: Set<string>;
  onToggle: (key: string) => void;
  /** When true (technical view), skip bundling so every raw row is visible. */
  disableBundling?: boolean;
}

function DaySection({ group, expandedKeys, onToggle, disableBundling }: DaySectionProps) {
  const bundles = disableBundling
    ? group.entries.map((entry) => ({ key: entry.key, lead: entry, entries: [entry] }))
    : bundleEntries(group.entries);
  const itemCount = group.entries.length;
  const groupCount = bundles.length;
  const bundledDown = groupCount !== itemCount;

  return (
    <div className="mb-6">
      <div className="flex items-center gap-3 mb-2">
        <span className="text-sm font-semibold text-slate-700 dark:text-slate-300">{group.label}</span>
        <div className="flex-1 h-px bg-slate-100 dark:bg-slate-800" />
        <span className="text-xs text-slate-600" data-testid="day-count">
          {itemCount} {itemCount === 1 ? "item" : "items"}
          {bundledDown && (
            <>
              {" "}
              <span className="text-slate-700">
                ({groupCount} {groupCount === 1 ? "group" : "groups"})
              </span>
            </>
          )}
        </span>
      </div>
      <div className="space-y-0.5">
        {bundles.map((bundle) =>
          bundle.entries.length === 1 ? (
            <StreamRow
              key={bundle.key}
              entry={bundle.lead}
              expanded={expandedKeys.has(bundle.key)}
              onToggle={() => onToggle(bundle.key)}
            />
          ) : (
            <BundleRow
              key={bundle.key}
              bundle={bundle}
              expanded={expandedKeys.has(bundle.key)}
              onToggle={() => onToggle(bundle.key)}
            />
          )
        )}
      </div>
    </div>
  );
}

// ─── Main page ──────────────────────────────────────────────────────────────

function formatDateTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

const STATUS_LABELS: Record<string, string> = {
  scheduled: "Pending",
  delivered: "Delivered",
  cancelled: "Cancelled",
};

const SNOOZE_OPTIONS = [
  { label: "15 min", minutes: 15, testSuffix: "15m" },
  { label: "1 hour", minutes: 60, testSuffix: "1h" },
  { label: "1 day", minutes: 1440, testSuffix: "1d" },
] as const;

interface RemindersTabProps {
  reminders: ReminderRow[];
  loading: boolean;
  onSnooze: (id: string, minutes: number) => Promise<void>;
  snoozingId: string | null;
}

function RemindersTab({ reminders, loading, onSnooze, snoozingId }: RemindersTabProps) {
  if (loading) {
    return <LoadingState variant="spinner" message="Loading reminders..." />;
  }
  if (reminders.length === 0) {
    return (
      <div data-testid="reminders-empty" className="py-16 text-center text-slate-500 text-sm">
        No reminders yet. Say "remind me to..." in chat to create one.
      </div>
    );
  }
  return (
    <ul data-testid="reminders-list" className="space-y-2">
      {reminders.map((r) => {
        const isPending = r.status === "scheduled";
        return (
          <li
            key={r.id}
            data-testid={`reminder-row-${r.id}`}
            data-status={r.status}
            className="flex flex-col sm:flex-row sm:items-center gap-3 px-4 py-3 rounded-lg bg-white/60 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800"
          >
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-slate-800 dark:text-slate-200 truncate">{r.text}</p>
              <div className="flex flex-wrap gap-x-4 gap-y-0.5 mt-1 text-xs text-slate-500">
                {r.created_at && (
                  <span>Created {formatDateTime(r.created_at)}</span>
                )}
                <span>Fires {formatDateTime(r.fire_at_utc)}</span>
                <span
                  className={`font-medium ${
                    r.status === "scheduled"
                      ? "text-amber-500"
                      : r.status === "delivered"
                      ? "text-emerald-500"
                      : "text-slate-400"
                  }`}
                >
                  {STATUS_LABELS[r.status] ?? r.status}
                </span>
              </div>
            </div>
            {isPending && (
              <div className="flex items-center gap-1.5 shrink-0">
                <span className="text-xs text-slate-500 mr-1">Snooze:</span>
                {SNOOZE_OPTIONS.map(({ label, minutes, testSuffix }) => (
                  <button
                    key={testSuffix}
                    data-testid={`snooze-${testSuffix}-${r.id}`}
                    onClick={() => onSnooze(r.id, minutes)}
                    disabled={snoozingId === r.id}
                    className="text-xs px-2 py-1 rounded bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-blue-100 dark:hover:bg-blue-900/40 hover:text-blue-700 dark:hover:text-blue-300 transition-colors disabled:opacity-50"
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export default function Activity() {
  const [tab, setTab] = useState<Tab>("events");
  const [rawEvents, setRawEvents] = useState<RawActivityEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [eventCount, setEventCount] = useState(100);
  const [showTechnical, setShowTechnical] = useState(false);
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());
  const [deduping, setDeduping] = useState(false);
  const [dedupeResult, setDedupeResult] = useState<string | null>(null);
  const [reminders, setReminders] = useState<ReminderRow[]>([]);
  const [remindersLoading, setRemindersLoading] = useState(false);
  const [snoozingId, setSnoozingId] = useState<string | null>(null);

  const fetchReminders = useCallback(async () => {
    setRemindersLoading(true);
    try {
      const data = await api.get<ReminderRow[]>("/reminders?upcoming_only=false");
      setReminders(data ?? []);
    } catch (e) {
      reportError('Failed to fetch reminders', e);
    } finally {
      setRemindersLoading(false);
    }
  }, []);

  const handleSnooze = useCallback(async (id: string, minutes: number) => {
    setSnoozingId(id);
    try {
      await api.post(`/reminders/${id}/snooze`, { minutes });
      await fetchReminders();
    } catch (e) {
      reportError('Failed to snooze reminder', e);
    } finally {
      setSnoozingId(null);
    }
  }, [fetchReminders]);

  const fetchActivity = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await api.get<ActivityResponse>(`/activity?last=${eventCount}`);
      setRawEvents(res.events ?? []);
    } catch (e) {
      reportError('Failed to fetch activity', e);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [eventCount]);

  const handleDedupe = async () => {
    setDeduping(true);
    setDedupeResult(null);
    try {
      const res = await api.post<{ removed: number; message: string }>("/activity/dedupe-audit", {});
      setDedupeResult(res.message ?? `Removed ${res.removed} duplicates`);
      if ((res.removed ?? 0) > 0) await fetchActivity();
    } catch {
      setDedupeResult("Cleanup failed. Try again.");
    } finally {
      setDeduping(false);
    }
  };

  useEffect(() => {
    fetchActivity();
  }, [fetchActivity]);

  // Fetch reminders when the reminders tab is activated.
  useEffect(() => {
    if (tab === "reminders") fetchReminders();
  }, [tab, fetchReminders]);

  // Keep the events tab live with a 10-second refresh.
  // Pass silent=true so the background poll doesn't flip the page back
  // to the loading spinner every 10 seconds.
  useEffect(() => {
    if (tab !== "events") return;
    const id = setInterval(() => fetchActivity(true), 10_000);
    return () => clearInterval(id);
  }, [tab, fetchActivity]);

  // Re-fetch immediately when the agent WS delivers a terminal event so
  // new agent.completed / agent.failed rows appear in under 2 seconds.
  const { lastMessage } = useWebSocket("/api/ws/agents/state", true);
  useEffect(() => {
    if (!lastMessage) return;
    const frame = lastMessage as any;
    if (frame.type === "delta" && frame.changed?.terminal === true) {
      fetchActivity(true);
    }
  }, [lastMessage, fetchActivity]);

  const toggleKey = (key: string) => {
    setExpandedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  // When "Show technical details" is on, every row is auto-expanded and
  // the stream includes all raw events (not just curated ones). When off,
  // only user-facing entries show, and expand is per-row.
  const streamEntries = buildStream(rawEvents);
  const dayGroups = groupByDay(streamEntries);

  // For the technical view, build groups from ALL raw events, surfacing
  // them as generic "other" entries.
  const allEntries = showTechnical
    ? rawEvents.map((ev, i) => ({
        key: `raw-${ev.timestamp}-${i}`,
        kind: "other" as const,
        summary: ev.label || ev.event,
        icon: "code",
        palette: "slate" as const,
        timestamp: ev.timestamp,
        raw: ev,
      }))
    : [];

  const allDayGroups = groupByDay(allEntries);
  const displayGroups: DayGroup[] = showTechnical ? allDayGroups : dayGroups;

  // In technical mode, all rows start expanded
  const effectiveExpandedKeys: Set<string> = showTechnical
    ? new Set(allEntries.map((e) => e.key))
    : expandedKeys;

  const totalVisible = displayGroups.reduce((n, g) => n + g.entries.length, 0);

  return (
    <PageShell title="Activity" fullHeight>
      <div data-tour="activity" className="px-4 pb-4 sm:px-8 sm:pb-8 flex-1 flex flex-col">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div className="flex flex-wrap items-center gap-4">
            <h1 data-testid="page-header" role="heading" className="text-xl sm:text-2xl font-bold">Activity</h1>
            <div className="flex items-center gap-1 border-b border-slate-200 dark:border-slate-800">
              <button
                onClick={() => setTab("events")}
                className={`px-3 py-2 text-sm font-medium transition-colors border-b-2 ${
                  tab === "events"
                    ? "border-blue-500 text-white"
                    : "border-transparent text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
                }`}
              >
                Events
              </button>
              <button
                data-testid="transcripts-tab"
                onClick={() => setTab("transcripts")}
                className={`px-3 py-2 text-sm font-medium transition-colors border-b-2 ${
                  tab === "transcripts"
                    ? "border-blue-500 text-white"
                    : "border-transparent text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
                }`}
              >
                Transcripts
              </button>
              <button
                data-testid="learned-tab"
                onClick={() => setTab("learned")}
                className={`px-3 py-2 text-sm font-medium transition-colors border-b-2 ${
                  tab === "learned"
                    ? "border-blue-500 text-white"
                    : "border-transparent text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
                }`}
              >
                What I learned
              </button>
              <button
                data-testid="reminders-tab"
                onClick={() => setTab("reminders")}
                className={`px-3 py-2 text-sm font-medium transition-colors border-b-2 ${
                  tab === "reminders"
                    ? "border-blue-500 text-white"
                    : "border-transparent text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
                }`}
              >
                Reminders
              </button>
            </div>
          </div>

          {tab === "events" && (
            <div className="flex items-center gap-3">
              {/* Show technical details toggle */}
              <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400 cursor-pointer select-none">
                <input
                  type="checkbox"
                  className="w-4 h-4 rounded border-slate-600 bg-slate-100 dark:bg-slate-800 text-blue-500 focus:ring-blue-500"
                  checked={showTechnical}
                  onChange={(e) => setShowTechnical(e.target.checked)}
                  data-testid="show-technical-toggle"
                />
                Show technical details
              </label>



              <select
                value={eventCount}
                onChange={(e) => setEventCount(Number(e.target.value))}
                className="bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-md px-2 py-1 text-sm text-slate-700 dark:text-slate-300"
              >
                <option value={50}>Last 50</option>
                <option value={100}>Last 100</option>
                <option value={200}>Last 200</option>
                <option value={500}>Last 500</option>
              </select>

              <button
                onClick={() => fetchActivity()}
                className="text-sm text-slate-600 dark:text-slate-400 hover:text-white transition-colors flex items-center gap-1"
              >
                <Icon name="refresh" size={16} />
                Refresh
              </button>

              <button
                onClick={handleDedupe}
                disabled={deduping}
                title="Remove duplicate events from the log"
                className="text-sm text-slate-600 dark:text-slate-400 hover:text-amber-700 dark:hover:text-amber-300 transition-colors flex items-center gap-1 disabled:opacity-50"
              >
                <Icon name="auto_fix_high" size={16} />
                {deduping ? "Cleaning..." : "Clean up"}
              </button>
            </div>
          )}
        </div>

        {dedupeResult && tab === "events" && (
          <div className="text-xs text-amber-600 dark:text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded px-3 py-1.5 mb-4">
            {dedupeResult}
          </div>
        )}



        {/* Tab content */}
        {tab === "reminders" ? (
          <RemindersTab
            reminders={reminders}
            loading={remindersLoading}
            onSnooze={handleSnooze}
            snoozingId={snoozingId}
          />
        ) : tab === "learned" ? (
          <PatternPanel />
        ) : tab === "transcripts" ? (
          <Suspense fallback={<div className="text-slate-500 text-center py-12">Loading transcripts...</div>}>
            <Transcripts embedded />
          </Suspense>
        ) : (
          <div className="flex-1">
            {loading && rawEvents.length === 0 ? (
              <LoadingState variant="spinner" message="Loading activity..." />
            ) : totalVisible === 0 ? (
              <EmptyState
                icon="history"
                title="Your activity will show up here as you work"
                description="Needles you close, specs you promote, and agents you run will all appear here."
              />
            ) : (
              <>
                {showTechnical && (
                  <div className="mb-4 text-xs text-amber-600 dark:text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded px-3 py-1.5">
                    Showing all {rawEvents.length} raw events including internal system activity.
                  </div>
                )}
                {displayGroups.map((group) => (
                  <DaySection
                    key={group.dateKey}
                    group={group}
                    expandedKeys={effectiveExpandedKeys}
                    onToggle={showTechnical ? () => {} : toggleKey}
                    disableBundling={showTechnical}
                  />
                ))}
              </>
            )}
          </div>
        )}
      </div>
    </PageShell>
  );
}
