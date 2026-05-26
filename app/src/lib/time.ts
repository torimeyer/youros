/**
 * Shared date/time formatters for display throughout the app.
 * All functions accept ISO strings, Date objects, null, or undefined and
 * return a plain-language string safe to render directly.
 */

/** Format elapsed seconds as "5s" or "7m 14s" for live status lines. */
export function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}m ${s}s`
}

/** Format a token count as "↓ 42 tokens" or "↓ 24.6k tokens". */
export function formatTokenCount(count: number): string {
  if (count >= 1000) return `↓ ${(count / 1000).toFixed(1)}k tokens`
  return `↓ ${count} tokens`
}

type DateInput = string | Date | null | undefined;

function toDate(input: DateInput): Date | null {
  if (!input) return null;
  const d = input instanceof Date ? input : new Date(input);
  return isNaN(d.getTime()) ? null : d;
}

/**
 * Relative time: "just now", "5m ago", "2h ago", "yesterday", "3d ago"
 */
export function formatRelative(input: DateInput): string {
  const d = toDate(input);
  if (!d) return '';
  const diff = Date.now() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return 'yesterday';
  return `${days}d ago`;
}

/**
 * Short date: "May 13"
 */
export function formatDate(input: DateInput): string {
  const d = toDate(input);
  if (!d) return '';
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

/**
 * Time of day: "2:30 PM"
 */
export function formatTime(input: DateInput): string {
  const d = toDate(input);
  if (!d) return '';
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

/**
 * Date + time: "May 13, 2:30 PM"
 */
export function formatDateTime(input: DateInput): string {
  const d = toDate(input);
  if (!d) return '';
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

/**
 * Precise timestamp with seconds (24h): "May 13, 14:30:09"
 * Used where second-level precision matters, e.g. rule activity logs.
 */
export function formatTimestamp(input: DateInput): string {
  const d = toDate(input);
  if (!d) return typeof input === 'string' ? input : '';
  const month = d.toLocaleString(undefined, { month: 'short' });
  const day = d.getDate();
  const time = d.toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
  return `${month} ${day}, ${time}`;
}

/**
 * Smart date for message lists: today → "2:30 PM", yesterday → "Yesterday",
 * older → "May 13". Matches Gmail and iMessage list conventions.
 */
export function formatSmartDate(input: DateInput): string {
  const d = toDate(input);
  if (!d) return '';
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return formatTime(d);
  }
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) {
    return 'Yesterday';
  }
  return formatDate(d);
}
