/**
 * activityStream.ts
 *
 * Curation layer that converts raw ostk audit events into plain-language
 * stream entries Tori cares about. Internal subagent noise, chat turns,
 * hook-source rows, and system boot/maintenance events are filtered out by
 * default. Technical details are preserved on every entry for the expand
 * drawer.
 */

// ─── Raw event shape from GET /api/activity ────────────────────────────────

export interface RawActivityEvent {
  timestamp: string;
  event: string;
  label: string;
  category: string;
  detail: string;
}

// ─── Curated stream entry ───────────────────────────────────────────────────

export type StreamEntryKind =
  | "task.closed"
  | "task.created"
  | "task.reopened"
  | "spec.promoted"
  | "spec.decomposed"
  | "agent.finished"
  | "agent.failed"
  | "fleet.completed"
  | "integration.connected"
  | "file.created"
  | "file.deleted"
  | "workflow.completed"
  | "decision.recorded"
  | "other";

export interface StreamEntry {
  /** Stable key for React rendering */
  key: string;
  kind: StreamEntryKind;
  /** Plain-language summary (shown in the default view) */
  summary: string;
  /** Material icon name for the leading icon */
  icon: string;
  /** Color palette token */
  palette: "blue" | "purple" | "amber" | "emerald" | "rose" | "slate" | "cyan";
  timestamp: string;
  /** Original raw event, always preserved for the technical expand drawer */
  raw: RawActivityEvent;
  /** Optional secondary context (e.g. "3 tasks" from a spec decompose) */
  meta?: string;
}

// ─── Events that are never user-facing ─────────────────────────────────────

/**
 * Raw event types that are always filtered out regardless of source.
 * These are internal plumbing events that carry no meaning for a PM.
 */
const ALWAYS_HIDDEN_EVENTS = new Set([
  "agent.spawned",       // We only surface agent.completed
  "chat.completion",
  "tool.bash",
  "heartbeat_injected",
  "tack.unknown",
  "tack.resolved",
  "bead.committed",
  "project.initialized",
  "session.shutdown",
  "session.start",
  "needle.activated",    // "Task activated" is an internal state change, not a user action
  "needle.linked",       // Internal linking, not user-visible
  "needle.refined",      // Internal refinement step
  "request.submitted",
  "request.denied",
  "draft.created",
  "hay.filed",             // Ideas feature removed
  "hay.converted",         // Ideas feature removed
]);

/**
 * Agent names that belong to the internal plumbing and should be hidden even
 * when their agent.completed event surfaces. Add slug prefixes here.
 */
const INTERNAL_AGENT_PREFIXES = [
  "claude-code-",        // inferred background CC sessions
  "hook-",               // hook-sourced agents
  "audit-",              // audit-synthesized rows
];

/**
 * Returns true when an agent name looks like an internal/background subagent
 * that Tori did not explicitly spawn by name.
 */
export function isInternalAgent(agentName: string): boolean {
  if (!agentName) return false;
  const lower = agentName.toLowerCase();
  return INTERNAL_AGENT_PREFIXES.some((prefix) => lower.startsWith(prefix));
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function slugToTitle(slug: string): string {
  return slug
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Extract a field value from a detail string like `key="value"`.
 */
function extractField(detail: string, key: string): string {
  const m = detail.match(new RegExp(`${key}="([^"]+)"`));
  return m ? m[1] : "";
}

/**
 * Parse a task title from various detail string formats the backend emits:
 *   - "→087 Build activity timeline"
 *   - 'needle="→088"'
 *   - 'id="→123" title="Foo bar"'
 *   - raw id like "→087"
 */
function extractTaskTitle(detail: string): string {
  // "→NNN Title words" pattern
  const arrowTitle = detail.match(/→\d+\s+(.+)/);
  if (arrowTitle) return arrowTitle[1].trim();
  // title="..." field
  const titleField = extractField(detail, "title");
  if (titleField) return titleField;
  // Fall back to the raw detail (truncated)
  return detail.length > 60 ? detail.slice(0, 60) + "…" : detail;
}

// ─── Main curation function ─────────────────────────────────────────────────

/**
 * Convert a single raw event into a curated StreamEntry.
 * Returns null if the event should be filtered out.
 */
export function curateEvent(ev: RawActivityEvent, index: number): StreamEntry | null {
  const { event, detail, timestamp, label } = ev;

  // Always-hidden events
  if (ALWAYS_HIDDEN_EVENTS.has(event)) return null;

  const key = `${timestamp}-${index}-${event}`;

  switch (event) {
    // ── Tasks ──────────────────────────────────────────────────────────────
    case "task.closed": {
      const title = extractTaskTitle(detail) || label;
      return {
        key,
        kind: "task.closed",
        summary: `Closed: ${title}`,
        icon: "check_circle",
        palette: "emerald",
        timestamp,
        raw: ev,
      };
    }

    case "task.added": {
      const title = extractTaskTitle(detail) || label;
      return {
        key,
        kind: "task.created",
        summary: `New task: ${title}`,
        icon: "add_task",
        palette: "blue",
        timestamp,
        raw: ev,
      };
    }

    case "task.reopened": {
      const title = extractTaskTitle(detail) || label;
      return {
        key,
        kind: "task.reopened",
        summary: `Reopened: ${title}`,
        icon: "replay",
        palette: "amber",
        timestamp,
        raw: ev,
      };
    }

    // ── Specs ──────────────────────────────────────────────────────────────
    case "spec.promoted": {
      const from = extractField(detail, "from") || detail;
      const fileName = from.split("/").pop()?.replace(/\.md$/, "") || from;
      return {
        key,
        kind: "spec.promoted",
        summary: `Promoted spec: ${slugToTitle(fileName)}`,
        icon: "upgrade",
        palette: "purple",
        timestamp,
        raw: ev,
      };
    }

    case "needles.created": {
      // "N tasks from spec" event
      const count = extractField(detail, "count") || "";
      const spec = extractField(detail, "spec") || "";
      const specName = spec.split("/").pop()?.replace(/\.md$/, "") || spec;
      const countStr = count ? `${count} tasks` : "tasks";
      const fromStr = specName ? ` from ${slugToTitle(specName)}` : "";
      return {
        key,
        kind: "spec.decomposed",
        summary: `Broke spec into ${countStr}${fromStr}`,
        icon: "account_tree",
        palette: "purple",
        timestamp,
        raw: ev,
        meta: countStr,
      };
    }

    // ── Agents ────────────────────────────────────────────────────────────
    case "agent.completed": {
      const name = extractField(detail, "name") || detail;
      if (isInternalAgent(name)) return null;
      const title = slugToTitle(name);
      return {
        key,
        kind: "agent.finished",
        summary: `Finished: ${title}`,
        icon: "smart_toy",
        palette: "purple",
        timestamp,
        raw: ev,
      };
    }

    case "agent.failed": {
      const name = extractField(detail, "name") || detail;
      if (isInternalAgent(name)) return null;
      const title = slugToTitle(name);
      return {
        key,
        kind: "agent.failed",
        summary: `Agent failed: ${title}`,
        icon: "error_outline",
        palette: "rose",
        timestamp,
        raw: ev,
      };
    }

    case "agent.killed": {
      const name = extractField(detail, "name") || detail;
      if (isInternalAgent(name)) return null;
      const title = slugToTitle(name);
      return {
        key,
        kind: "agent.failed",
        summary: `Stopped: ${title}`,
        icon: "stop_circle",
        palette: "slate",
        timestamp,
        raw: ev,
      };
    }

    // ── Fleets ────────────────────────────────────────────────────────────
    case "fleet.completed": {
      const fleetName = extractField(detail, "fleet") || extractField(detail, "name") || detail;
      const count = extractField(detail, "count") || extractField(detail, "agents") || "";
      const agentStr = count ? ` (${count} agents)` : "";
      return {
        key,
        kind: "fleet.completed",
        summary: `Fleet done: ${slugToTitle(fleetName)}${agentStr}`,
        icon: "groups",
        palette: "purple",
        timestamp,
        raw: ev,
        meta: agentStr.trim(),
      };
    }

    // ── Decisions ────────────────────────────────────────────────────────
    case "decision.recorded": {
      const text = detail.replace(/^decision=/i, "").replace(/^"/, "").replace(/"$/, "").trim();
      const display = text.length > 70 ? text.slice(0, 70) + "…" : text;
      return {
        key,
        kind: "decision.recorded",
        summary: display ? `Decision: ${display}` : "Decision recorded",
        icon: "gavel",
        palette: "cyan",
        timestamp,
        raw: ev,
      };
    }

    // ── Workflow runs ─────────────────────────────────────────────────────
    case "workflow.completed": {
      const name = extractField(detail, "name") || detail;
      return {
        key,
        kind: "workflow.completed",
        summary: `Workflow done: ${slugToTitle(name)}`,
        icon: "play_circle",
        palette: "cyan",
        timestamp,
        raw: ev,
      };
    }

    default:
      // Unknown event type: surface it generically rather than hiding.
      // Keeps "other" items visible if they slip through ALWAYS_HIDDEN.
      return {
        key,
        kind: "other",
        summary: label || slugToTitle(event),
        icon: "info",
        palette: "slate",
        timestamp,
        raw: ev,
      };
  }
}

/**
 * Convert a list of raw events into curated stream entries.
 * Internal/filtered events are dropped (not returned).
 */
export function buildStream(rawEvents: RawActivityEvent[]): StreamEntry[] {
  const entries: StreamEntry[] = [];
  rawEvents.forEach((ev, i) => {
    const entry = curateEvent(ev, i);
    if (entry) entries.push(entry);
  });
  return entries;
}

// ─── Day grouping ─────────────────────────────────────────────────────────

export interface DayGroup {
  /** ISO date string "YYYY-MM-DD" */
  dateKey: string;
  /** Human label: "Today", "Yesterday", "Mon Apr 7", etc. */
  label: string;
  entries: StreamEntry[];
}

export function groupByDay(entries: StreamEntry[]): DayGroup[] {
  const now = new Date();
  const todayStr = now.toDateString();
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  const yesterdayStr = yesterday.toDateString();

  // Current week start (Sunday)
  const weekStart = new Date(now);
  weekStart.setDate(weekStart.getDate() - weekStart.getDay());

  const map = new Map<string, StreamEntry[]>();

  for (const entry of entries) {
    const dateKey = entry.timestamp.split("T")[0] || "unknown";
    if (!map.has(dateKey)) map.set(dateKey, []);
    map.get(dateKey)!.push(entry);
  }

  return Array.from(map.entries())
    .sort(([a], [b]) => b.localeCompare(a)) // newest day first
    .map(([dateKey, dayEntries]) => {
      const d = new Date(dateKey + "T12:00:00Z");
      let label: string;
      if (d.toDateString() === todayStr) {
        label = "Today";
      } else if (d.toDateString() === yesterdayStr) {
        label = "Yesterday";
      } else if (d >= weekStart) {
        label = d.toLocaleDateString([], { weekday: "long" });
      } else {
        label = d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
      }
      return { dateKey, label, entries: dayEntries };
    });
}

// ─── Bundling consecutive similar entries ─────────────────────────────────

/**
 * A bundle of 1+ consecutive stream entries that share the same kind,
 * summary, and minute bucket. Size 1 bundles render as normal rows.
 * Larger bundles render as a single collapsible row showing the count,
 * with individual entries hidden behind an expand chevron.
 */
export interface EntryBundle {
  /** Stable React key, derived from the lead entry's key */
  key: string;
  /** The first entry in the run, used for icon / palette / summary */
  lead: StreamEntry;
  /** All entries in the bundle, newest first (same order as input) */
  entries: StreamEntry[];
}

/**
 * Round an ISO timestamp down to the minute so that events within the
 * same minute bucket together (e.g. 22:38:00 and 22:38:45 share "22:38").
 * Falls back to the raw string if parsing fails so events without a
 * valid timestamp still group when they are byte-identical.
 */
function minuteBucket(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    // YYYY-MM-DDTHH:MM (ignore seconds and timezone offset past the minute)
    return d.toISOString().slice(0, 16);
  } catch {
    return iso;
  }
}

/**
 * Bundle consecutive entries that share the same kind, summary, and
 * minute bucket. Non-consecutive entries (anything of a different type
 * or bucket between them) break the run.
 */
export function bundleEntries(entries: StreamEntry[]): EntryBundle[] {
  const bundles: EntryBundle[] = [];
  for (const entry of entries) {
    const last = bundles[bundles.length - 1];
    if (
      last &&
      last.lead.kind === entry.kind &&
      last.lead.summary === entry.summary &&
      minuteBucket(last.lead.timestamp) === minuteBucket(entry.timestamp)
    ) {
      last.entries.push(entry);
    } else {
      bundles.push({ key: entry.key, lead: entry, entries: [entry] });
    }
  }
  return bundles;
}

// ─── Palette → Tailwind class mapping ────────────────────────────────────

export const paletteClasses: Record<StreamEntry["palette"], { bg: string; text: string }> = {
  blue:    { bg: "bg-blue-500/20",    text: "text-blue-400"    },
  purple:  { bg: "bg-purple-500/20",  text: "text-purple-400"  },
  amber:   { bg: "bg-amber-500/20",   text: "text-amber-400"   },
  emerald: { bg: "bg-emerald-500/20", text: "text-emerald-400" },
  rose:    { bg: "bg-rose-500/20",    text: "text-rose-400"    },
  slate:   { bg: "bg-slate-500/20",   text: "text-slate-400"   },
  cyan:    { bg: "bg-cyan-500/20",    text: "text-cyan-400"    },
};
