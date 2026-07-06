import { useState, useEffect, useCallback } from "react";
import PageShell from "../components/PageShell";
import { api } from "../lib/api";
import { LoadingState, EmptyState } from "../components/ui";
import { TodayDigestPanel, type DigestData } from "./TodayDigestPanel";

interface SessionRow {
  id: string;
  name: string;
  label?: string;
  type: string;
  started_at: string | null;
  last_active_at: string;
  status: "active" | "idle";
  activity?: string;
  recent_files?: string[];
  stuck?: boolean;
}

interface LockRow {
  lock_name: string;
  held_by_session: string;
  started_at: string | null;
  paths: string[];
}

interface EventRow {
  from_session: string;
  to_session: string;
  message: string;
  timestamp: string;
  kind: string;
}

interface ConflictRow {
  path: string;
  session_ids: string[];
  last_write_times: Record<string, string>;
}

interface CoordinationData {
  sessions: SessionRow[];
  locks: LockRow[];
  events: EventRow[];
  conflicts?: ConflictRow[];
}

function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  try {
    const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    return `${Math.floor(diff / 3600)}h ago`;
  } catch {
    return iso;
  }
}

function typeLabel(type: string): string {
  switch (type) {
    case "claude-code": return "Claude Code";
    case "agent": return "Agent";
    case "chat": return "Chat";
    default: return "Session";
  }
}

function StatusDot({ status }: { status: "active" | "idle" }) {
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full ${
        status === "active" ? "bg-green-400" : "bg-slate-500"
      }`}
      aria-label={status}
    />
  );
}

function ConflictsStrip({ conflicts }: { conflicts: ConflictRow[] }) {
  if (!conflicts || conflicts.length === 0) return null;
  return (
    <div
      data-testid="conflicts-strip"
      className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3"
    >
      <p className="text-xs font-semibold text-amber-500 uppercase tracking-wider mb-2">
        File conflicts (informational)
      </p>
      {conflicts.map((c, i) => (
        <div
          key={`${c.path}-${i}`}
          data-testid="conflict-row"
          className="flex flex-wrap items-center gap-1.5 text-xs text-slate-300 mb-1 last:mb-0"
        >
          <span
            data-testid="conflict-path"
            className="font-mono text-amber-400 truncate max-w-[200px]"
            title={c.path}
          >
            {c.path.split("/").pop() || c.path}
          </span>
          <span className="text-slate-500">written by</span>
          <span data-testid="conflict-sessions">
            {c.session_ids.join(" and ")}
          </span>
        </div>
      ))}
    </div>
  );
}

function SessionsColumn({ sessions }: { sessions: SessionRow[] }) {
  return (
    <div data-testid="sessions-column" className="flex flex-col gap-2">
      <h2 className="text-xs font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wider mb-2">
        Active sessions
      </h2>
      {sessions.length === 0 ? (
        <p className="text-sm text-slate-500">No sessions right now.</p>
      ) : (
        sessions.map((s) => (
          <div
            key={s.id}
            data-testid="session-row"
            className="bg-white dark:bg-slate-900 rounded-lg px-3 py-2 flex items-start gap-2"
          >
            <StatusDot status={s.status} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5 flex-wrap">
                <p className="text-sm text-slate-800 dark:text-slate-200 truncate">
                  {s.label || s.name}
                </p>
                {s.stuck && (
                  <span
                    data-testid="stuck-badge"
                    className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-500"
                  >
                    quiet
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-500">
                {typeLabel(s.type)} · last active {relativeTime(s.last_active_at)}
              </p>
              {s.activity && (
                <p className="text-xs text-slate-600 dark:text-slate-400 truncate mt-0.5">
                  {s.activity}
                </p>
              )}
              {s.recent_files && s.recent_files.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1">
                  {s.recent_files.slice(0, 3).map((f) => (
                    <span
                      key={f}
                      className="text-[10px] font-mono px-1 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-500 truncate max-w-[120px]"
                      title={f}
                    >
                      {f.split("/").pop()}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  );
}

function LocksColumn({ locks }: { locks: LockRow[] }) {
  return (
    <div data-testid="locks-column" className="flex flex-col gap-2">
      <h2 className="text-xs font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wider mb-2">
        Locks held
      </h2>
      {locks.length === 0 ? (
        <p className="text-sm text-slate-500">No locks held.</p>
      ) : (
        locks.map((lk, i) => (
          <div
            key={`${lk.lock_name}-${i}`}
            data-testid="lock-row"
            className="bg-white dark:bg-slate-900 rounded-lg px-3 py-2"
          >
            <p className="text-sm text-slate-800 dark:text-slate-200 font-mono truncate">{lk.lock_name}</p>
            <p className="text-xs text-slate-500">
              Held by {lk.held_by_session || "unknown"} · {relativeTime(lk.started_at)}
            </p>
            {lk.paths.length > 0 && (
              <p className="text-xs text-slate-600 mt-0.5 truncate">
                {lk.paths.slice(0, 2).join(", ")}
              </p>
            )}
          </div>
        ))
      )}
    </div>
  );
}

function EventsColumn({ events }: { events: EventRow[] }) {
  return (
    <div data-testid="events-column" className="flex flex-col gap-2">
      <h2 className="text-xs font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wider mb-2">
        Recent coordination
      </h2>
      {events.length === 0 ? (
        <p className="text-sm text-slate-500">No recent messages between sessions.</p>
      ) : (
        events.map((ev, i) => (
          <div
            key={`${ev.timestamp}-${i}`}
            data-testid="event-row"
            className="bg-white dark:bg-slate-900 rounded-lg px-3 py-2"
          >
            <p className="text-xs text-slate-500 mb-0.5">
              {ev.from_session} → {ev.to_session} · {relativeTime(ev.timestamp)}
            </p>
            <p className="text-sm text-slate-700 dark:text-slate-300 line-clamp-2">{ev.message}</p>
          </div>
        ))
      )}
    </div>
  );
}

export default function Sessions() {
  const [data, setData] = useState<CoordinationData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [digest, setDigest] = useState<DigestData | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = await api.get<CoordinationData>("/sessions/coordination");
      setData(resp);
      setError(null);
    } catch {
      setError("Could not load session data.");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDigest = useCallback(async () => {
    try {
      const resp = await api.get<DigestData>("/sessions/digest");
      setDigest(resp);
    } catch {
      // Optional; ignore errors
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

  useEffect(() => {
    loadDigest();
    const id = setInterval(loadDigest, 30000);
    return () => clearInterval(id);
  }, [loadDigest]);

  return (
    <PageShell title="Sessions" fullHeight>
      <div className="flex-1 overflow-auto p-6">
        {loading && !data ? (
          <LoadingState message="Loading sessions..." />
        ) : error ? (
          <EmptyState icon="warning" title="Could not load" description={error} />
        ) : data ? (
          <>
            <TodayDigestPanel digest={digest} />
            <ConflictsStrip conflicts={data.conflicts || []} />
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <SessionsColumn sessions={data.sessions} />
              <LocksColumn locks={data.locks} />
              <EventsColumn events={data.events} />
            </div>
          </>
        ) : null}
      </div>
    </PageShell>
  );
}
