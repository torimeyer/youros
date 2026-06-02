import { useState, useEffect, useCallback } from "react";
import PageShell from "../components/PageShell";
import { api } from "../lib/api";
import { LoadingState, EmptyState } from "../components/ui";

interface SessionRow {
  id: string;
  name: string;
  type: string;
  started_at: string | null;
  last_active_at: string;
  status: "active" | "idle";
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

interface CoordinationData {
  sessions: SessionRow[];
  locks: LockRow[];
  events: EventRow[];
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
              <p className="text-sm text-slate-800 dark:text-slate-200 truncate">{s.name}</p>
              <p className="text-xs text-slate-500">
                {typeLabel(s.type)} · last active {relativeTime(s.last_active_at)}
              </p>
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

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

  return (
    <PageShell title="Sessions" fullHeight>
      <div className="flex-1 overflow-auto p-6">
        {loading && !data ? (
          <LoadingState message="Loading sessions..." />
        ) : error ? (
          <EmptyState icon="warning" title="Could not load" description={error} />
        ) : data ? (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <SessionsColumn sessions={data.sessions} />
            <LocksColumn locks={data.locks} />
            <EventsColumn events={data.events} />
          </div>
        ) : null}
      </div>
    </PageShell>
  );
}
