/**
 * Cross-session digest panel (→2455 phase D).
 * Shows what each active session did today and what tasks were closed.
 * Used in both Agents page (Active Sessions section) and Sessions page.
 */
import { useState } from "react";

export interface DigestSession {
  session_id: string;
  label: string;
  activity_count: number;
  files_touched: string[];
  recent_activity: string;
}

export interface DigestClosedTask {
  id: string;
  title: string;
  closed_at: string;
}

export interface DigestData {
  sessions: DigestSession[];
  closed_tasks_today: DigestClosedTask[];
  generated_at: string;
}

interface Props {
  digest: DigestData | null;
}

export function TodayDigestPanel({ digest }: Props) {
  const [expanded, setExpanded] = useState(false);

  if (!digest || !Array.isArray(digest.sessions) || !Array.isArray(digest.closed_tasks_today)) return null;
  const { sessions, closed_tasks_today } = digest;
  if (sessions.length === 0 && closed_tasks_today.length === 0) return null;

  const totalActions = sessions.reduce((sum, s) => sum + s.activity_count, 0);

  return (
    <div
      data-testid="today-digest-panel"
      className="mb-4 rounded-lg border border-blue-500/20 bg-blue-500/5 overflow-hidden"
    >
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-2.5 text-left hover:bg-blue-500/10 transition-colors"
        aria-label="Today across sessions"
      >
        <span className="text-xs font-semibold text-blue-400 uppercase tracking-wider">
          Today across sessions
        </span>
        <span className="text-xs text-slate-500 flex items-center gap-2">
          {sessions.length > 0 && (
            <span>{sessions.length} seat{sessions.length !== 1 ? "s" : ""}, {totalActions} actions</span>
          )}
          {closed_tasks_today.length > 0 && (
            <span>{closed_tasks_today.length} task{closed_tasks_today.length !== 1 ? "s" : ""} done</span>
          )}
          <span className={`material-symbols-outlined text-sm transition-transform ${expanded ? "rotate-180" : ""}`}>
            expand_more
          </span>
        </span>
      </button>

      <div
        style={{ display: expanded ? "block" : "none" }}
        data-testid="digest-details"
      >
        {sessions.length > 0 && (
          <div className="px-4 pb-3 pt-1 flex flex-col gap-1.5">
            {sessions.map((s) => (
              <div
                key={s.session_id}
                data-testid="digest-session-row"
                className="text-xs"
              >
                <div className="flex items-baseline gap-1.5 flex-wrap">
                  <span className="text-slate-200 font-medium truncate max-w-[200px]">
                    {s.label}
                  </span>
                  <span className="text-slate-500">{s.activity_count} actions</span>
                  {s.recent_activity && (
                    <span className="text-slate-600 truncate max-w-[180px]">
                      · {s.recent_activity}
                    </span>
                  )}
                </div>
                {s.files_touched.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-0.5">
                    {s.files_touched.slice(0, 4).map((f) => (
                      <span
                        key={f}
                        className="font-mono text-[10px] px-1 py-0.5 rounded bg-slate-800 text-slate-400 truncate max-w-[140px]"
                        title={f}
                      >
                        {f.split("/").pop()}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {closed_tasks_today.length > 0 && (
          <div className="px-4 pb-3 border-t border-blue-500/10 pt-2">
            <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1">
              Completed today
            </p>
            <div className="flex flex-col gap-0.5">
              {closed_tasks_today.map((t) => (
                <div key={t.id} className="flex items-center gap-1.5 text-xs text-slate-400">
                  <span className="text-green-500 material-symbols-outlined text-sm">check_circle</span>
                  <span className="truncate">{t.title}</span>
                  <span className="text-slate-600 text-[10px]">{t.id}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
