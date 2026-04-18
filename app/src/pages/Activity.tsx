import { useState, useEffect, useCallback, lazy, Suspense } from "react";
import Icon from "../components/Icon";
import TopBar from "../components/TopBar";
import { api } from "../lib/api";
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

type Tab = "events" | "transcripts";

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
        className="w-full flex items-start gap-3 px-4 py-2.5 rounded-lg hover:bg-slate-900/60 transition-colors text-left"
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
          <span className="text-sm font-medium text-slate-200">{entry.summary}</span>
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
          className="ml-11 mb-1 px-4 py-3 rounded-lg bg-slate-900/60 border border-slate-800 text-xs text-slate-400 space-y-1"
          data-testid="stream-row-detail"
        >
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span>
              <span className="text-slate-600">event</span>{" "}
              <span className="font-mono text-slate-300">{entry.raw.event}</span>
            </span>
            <span>
              <span className="text-slate-600">category</span>{" "}
              <span className="font-mono text-slate-300">{entry.raw.category}</span>
            </span>
            <span>
              <span className="text-slate-600">time</span>{" "}
              <span className="font-mono text-slate-300">{entry.timestamp}</span>
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
        className="w-full flex items-start gap-3 px-4 py-2.5 rounded-lg hover:bg-slate-900/60 transition-colors text-left"
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
          <span className="text-sm font-medium text-slate-200 truncate">{lead.summary}</span>
          <span
            className="text-xs font-medium bg-slate-800 text-slate-300 px-1.5 py-0.5 rounded"
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
          className="ml-11 mb-1 px-4 py-3 rounded-lg bg-slate-900/60 border border-slate-800 text-xs text-slate-400"
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
                <span className="text-slate-300 break-words">{entryDetailLabel(entry)}</span>
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
        <span className="text-sm font-semibold text-slate-300">{group.label}</span>
        <div className="flex-1 h-px bg-slate-800" />
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

export default function Activity() {
  const [tab, setTab] = useState<Tab>("events");
  const [rawEvents, setRawEvents] = useState<RawActivityEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [eventCount, setEventCount] = useState(100);
  const [showTechnical, setShowTechnical] = useState(false);
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());
  const [deduping, setDeduping] = useState(false);
  const [dedupeResult, setDedupeResult] = useState<string | null>(null);

  const fetchActivity = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get<ActivityResponse>(`/activity?last=${eventCount}`);
      setRawEvents(res.events ?? []);
    } catch (e) {
      console.error("Failed to fetch activity:", e);
    } finally {
      setLoading(false);
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
    <div className="min-h-screen bg-slate-950 text-white flex flex-col">
      <TopBar title="Activity" />

      <div data-tour="activity" className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8 flex-1 flex flex-col">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div className="flex flex-wrap items-center gap-4">
            <h1 data-testid="page-header" role="heading" className="text-xl sm:text-2xl font-bold">Activity</h1>
            <div className="flex items-center gap-1 border-b border-slate-800">
              <button
                onClick={() => setTab("events")}
                className={`px-3 py-2 text-sm font-medium transition-colors border-b-2 ${
                  tab === "events"
                    ? "border-blue-500 text-white"
                    : "border-transparent text-slate-400 hover:text-slate-200"
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
                    : "border-transparent text-slate-400 hover:text-slate-200"
                }`}
              >
                Transcripts
              </button>
            </div>
          </div>

          {tab === "events" && (
            <div className="flex items-center gap-3">
              {/* Show technical details toggle */}
              <label className="flex items-center gap-2 text-sm text-slate-400 cursor-pointer select-none">
                <input
                  type="checkbox"
                  className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-blue-500 focus:ring-blue-500"
                  checked={showTechnical}
                  onChange={(e) => setShowTechnical(e.target.checked)}
                  data-testid="show-technical-toggle"
                />
                Show technical details
              </label>

              <select
                value={eventCount}
                onChange={(e) => setEventCount(Number(e.target.value))}
                className="bg-slate-800 border border-slate-700 rounded-md px-2 py-1 text-sm text-slate-300"
              >
                <option value={50}>Last 50</option>
                <option value={100}>Last 100</option>
                <option value={200}>Last 200</option>
                <option value={500}>Last 500</option>
              </select>

              <button
                onClick={fetchActivity}
                className="text-sm text-slate-400 hover:text-white transition-colors flex items-center gap-1"
              >
                <Icon name="refresh" size={16} />
                Refresh
              </button>

              <button
                onClick={handleDedupe}
                disabled={deduping}
                title="Remove duplicate events from the log"
                className="text-sm text-slate-400 hover:text-amber-300 transition-colors flex items-center gap-1 disabled:opacity-50"
              >
                <Icon name="auto_fix_high" size={16} />
                {deduping ? "Cleaning..." : "Clean up"}
              </button>
            </div>
          )}
        </div>

        {dedupeResult && tab === "events" && (
          <div className="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded px-3 py-1.5 mb-4">
            {dedupeResult}
          </div>
        )}

        {/* Tab content */}
        {tab === "transcripts" ? (
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
                description="Tasks you close, specs you promote, and agents you run will all appear here."
              />
            ) : (
              <>
                {showTechnical && (
                  <div className="mb-4 text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded px-3 py-1.5">
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
    </div>
  );
}
