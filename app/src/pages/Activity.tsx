import { useState, useEffect, useCallback, lazy, Suspense, useRef } from "react";
import Icon from "../components/Icon";
import TopBar from "../components/TopBar";
import { api } from "../lib/api";
import TerminalPlayer from "../components/TerminalPlayer";

const Transcripts = lazy(() => import("./Transcripts"));

interface ActivityEvent {
  timestamp: string;
  event: string;
  label: string;
  category: string;
  detail: string;
}

interface ActivityResponse {
  events: ActivityEvent[];
  count: number;
}

type CategoryFilter = "all" | "task" | "agent" | "idea" | "decision" | "system";

const CATEGORY_FILTERS: { value: CategoryFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "task", label: "Tasks" },
  { value: "agent", label: "Agents" },
  { value: "idea", label: "Ideas" },
  { value: "decision", label: "Decisions" },
  { value: "system", label: "System" },
];

const categoryIcon: Record<string, string> = {
  task: "checklist",
  agent: "smart_toy",
  idea: "lightbulb",
  decision: "gavel",
  system: "settings",
  other: "info",
};

const categoryColor: Record<string, string> = {
  task: "text-blue-400 bg-blue-500/20",
  agent: "text-purple-400 bg-purple-500/20",
  idea: "text-amber-400 bg-amber-500/20",
  decision: "text-emerald-400 bg-emerald-500/20",
  system: "text-slate-400 bg-slate-500/20",
  other: "text-slate-400 bg-slate-500/20",
};

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    const today = new Date();
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);

    if (d.toDateString() === today.toDateString()) return "Today";
    if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
    return d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
  } catch {
    return iso;
  }
}

function groupByDate(events: ActivityEvent[]): Map<string, ActivityEvent[]> {
  const groups = new Map<string, ActivityEvent[]>();
  for (const ev of events) {
    const dateKey = ev.timestamp.split("T")[0] || "Unknown";
    if (!groups.has(dateKey)) groups.set(dateKey, []);
    groups.get(dateKey)!.push(ev);
  }
  return groups;
}

type Tab = "events" | "sessions" | "recordings";

interface Recording {
  id: string;
  title: string;
  started_at: number;
  width: number;
  height: number;
  size_bytes: number;
  status: string;
}

interface RecordingsResponse {
  recordings: Recording[];
}

function RecordingsTab() {
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [loading, setLoading] = useState(true);
  const [recording, setRecording] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [cmd, setCmd] = useState("");
  const [output, setOutput] = useState<string[]>([]);
  const [runningCmd, setRunningCmd] = useState(false);
  const [playbackId, setPlaybackId] = useState<string | null>(null);
  const cmdInputRef = useRef<HTMLInputElement>(null);

  const fetchRecordings = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get<RecordingsResponse>("/recordings");
      setRecordings(data.recordings ?? []);
    } catch {
      setRecordings([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRecordings();
  }, [fetchRecordings]);

  const startRecording = async () => {
    try {
      const data = await api.post<{ id: string; status: string; label: string }>(
        "/recordings/start",
        { label: label || undefined }
      );
      setActiveId(data.id);
      setRecording(true);
      setOutput([`Recording started: ${data.label}`]);
      setTimeout(() => cmdInputRef.current?.focus(), 100);
    } catch {
      setOutput(["Could not start recording."]);
    }
  };

  const stopRecording = async () => {
    if (!activeId) return;
    try {
      await api.post(`/recordings/${activeId}/stop`, {});
      setRecording(false);
      setOutput((prev) => [...prev, "Recording stopped."]);
      fetchRecordings();
      setLabel("");
    } catch {
      setOutput((prev) => [...prev, "Could not stop recording."]);
    }
  };

  const runCommand = async () => {
    if (!activeId || !cmd.trim() || runningCmd) return;
    const command = cmd.trim();
    setCmd("");
    setRunningCmd(true);
    setOutput((prev) => [...prev, `$ ${command}`]);
    try {
      const data = await api.post<{ output: string; returncode: number }>(
        `/recordings/${activeId}/command`,
        { cmd: command }
      );
      const lines = data.output.split("\n").filter(Boolean);
      setOutput((prev) => [...prev, ...lines]);
    } catch {
      setOutput((prev) => [...prev, "[error running command]"]);
    } finally {
      setRunningCmd(false);
      cmdInputRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") runCommand();
  };

  const castUrl = (id: string) =>
    `${window.location.protocol}//${window.location.hostname}:8000/api/recordings/${id}/cast`;

  const formatDate = (ts: number) => {
    if (!ts) return "";
    return new Date(ts * 1000).toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="flex flex-col gap-6">
      {/* Record controls */}
      <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-5">
        <div className="flex items-center gap-3 mb-4">
          <div
            className={`w-3 h-3 rounded-full ${recording ? "bg-red-500 animate-pulse" : "bg-slate-600"}`}
          />
          <span className="text-sm font-medium text-slate-200">
            {recording ? "Recording in progress" : "Terminal recorder"}
          </span>
        </div>

        {!recording && (
          <div className="flex items-center gap-3">
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Session label (optional)"
              className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500/50"
            />
            <button
              onClick={startRecording}
              aria-label="Start recording"
              className="flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-500 text-white text-sm font-medium rounded-lg transition-colors"
            >
              <Icon name="fiber_manual_record" size={16} />
              Record
            </button>
          </div>
        )}

        {recording && (
          <>
            {/* Output console */}
            <div className="bg-slate-950 rounded-lg p-3 font-mono text-xs text-green-400 h-40 overflow-y-auto mb-3 space-y-0.5">
              {output.map((line, i) => (
                <div key={i}>{line || "\u00a0"}</div>
              ))}
            </div>

            {/* Command input */}
            <div className="flex items-center gap-2">
              <span className="text-green-400 font-mono text-sm">$</span>
              <input
                ref={cmdInputRef}
                type="text"
                value={cmd}
                onChange={(e) => setCmd(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Run a command..."
                disabled={runningCmd}
                aria-label="Shell command"
                className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-sm text-white font-mono placeholder-slate-500 focus:outline-none focus:border-green-500/50 disabled:opacity-50"
              />
              <button
                onClick={runCommand}
                disabled={runningCmd || !cmd.trim()}
                aria-label="Run command"
                className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-40 text-white text-sm rounded-lg transition-colors"
              >
                Run
              </button>
              <button
                onClick={stopRecording}
                aria-label="Stop recording"
                className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-700 hover:bg-red-700 text-white text-sm rounded-lg transition-colors"
              >
                <Icon name="stop" size={16} />
                Stop
              </button>
            </div>
          </>
        )}
      </div>

      {/* Recordings list */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-300">Past recordings</h2>
          <button
            onClick={fetchRecordings}
            className="text-xs text-slate-500 hover:text-slate-300 flex items-center gap-1 transition-colors"
          >
            <Icon name="refresh" size={14} />
            Refresh
          </button>
        </div>

        {loading && (
          <div className="text-slate-500 text-sm text-center py-8">Loading recordings...</div>
        )}

        {!loading && recordings.length === 0 && (
          <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-500 text-sm">
            No recordings yet. Click Record to capture a terminal session.
          </div>
        )}

        {!loading && recordings.length > 0 && (
          <div className="flex flex-col gap-2">
            {recordings.map((rec) => (
              <div
                key={rec.id}
                className="bg-slate-900/40 border border-slate-800 rounded-xl overflow-hidden"
              >
                <div
                  className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-800/40 transition-colors"
                  onClick={() =>
                    setPlaybackId(playbackId === rec.id ? null : rec.id)
                  }
                  role="button"
                  aria-label={`Play recording: ${rec.title}`}
                  aria-expanded={playbackId === rec.id}
                >
                  <div className="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center shrink-0">
                    <Icon
                      name={playbackId === rec.id ? "pause" : "play_arrow"}
                      size={18}
                      className="text-slate-300"
                    />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-white truncate">{rec.title}</p>
                    <p className="text-xs text-slate-500">
                      {formatDate(rec.started_at)} &middot; {formatSize(rec.size_bytes)}
                    </p>
                  </div>
                  <Icon
                    name={playbackId === rec.id ? "expand_less" : "expand_more"}
                    size={18}
                    className="text-slate-500 shrink-0"
                  />
                </div>

                {playbackId === rec.id && (
                  <div className="border-t border-slate-800 p-3 bg-slate-950/50">
                    <TerminalPlayer
                      castUrl={castUrl(rec.id)}
                      width={rec.width}
                      height={rec.height}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Activity() {
  const [tab, setTab] = useState<Tab>("events");
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<CategoryFilter>("all");
  const [eventCount, setEventCount] = useState(50);

  const fetchActivity = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get<ActivityResponse>(`/activity?last=${eventCount}`);
      setEvents(res.events ?? []);
    } catch (e) {
      console.error("Failed to fetch activity:", e);
    } finally {
      setLoading(false);
    }
  }, [eventCount]);

  useEffect(() => {
    fetchActivity();
  }, [fetchActivity]);

  const filtered = filter === "all" ? events : events.filter((e) => e.category === filter);
  const grouped = groupByDate(filtered);

  return (
    <div className="min-h-screen bg-slate-950 text-white flex flex-col">
      <TopBar title="Activity" />

      <div data-tour="activity" className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8 flex-1 flex flex-col">
        {/* Header with tabs */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div className="flex flex-wrap items-center gap-4">
            <h1 className="text-xl sm:text-2xl font-bold">Activity</h1>
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
                onClick={() => setTab("sessions")}
                className={`px-3 py-2 text-sm font-medium transition-colors border-b-2 ${
                  tab === "sessions"
                    ? "border-blue-500 text-white"
                    : "border-transparent text-slate-400 hover:text-slate-200"
                }`}
              >
                Sessions
              </button>
              <button
                onClick={() => setTab("recordings")}
                className={`px-3 py-2 text-sm font-medium transition-colors border-b-2 ${
                  tab === "recordings"
                    ? "border-blue-500 text-white"
                    : "border-transparent text-slate-400 hover:text-slate-200"
                }`}
              >
                Recordings
              </button>
            </div>
          </div>
          {tab === "events" && (
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1 text-sm">
                {CATEGORY_FILTERS.map((cf) => (
                  <button
                    key={cf.value}
                    onClick={() => setFilter(cf.value)}
                    className={`px-3 py-1 rounded-full transition-colors ${
                      filter === cf.value
                        ? "accent-bg !text-white font-medium"
                        : "text-slate-400 hover:text-slate-200 hover:bg-slate-800"
                    }`}
                  >
                    {cf.label}
                  </button>
                ))}
              </div>
              <select
                value={eventCount}
                onChange={(e) => setEventCount(Number(e.target.value))}
                className="bg-slate-800 border border-slate-700 rounded-md px-2 py-1 text-sm text-slate-300"
              >
                <option value={25}>Last 25</option>
                <option value={50}>Last 50</option>
                <option value={100}>Last 100</option>
                <option value={200}>Last 200</option>
              </select>
              <button
                onClick={fetchActivity}
                className="text-sm text-slate-400 hover:text-white transition-colors flex items-center gap-1"
              >
                <Icon name="refresh" size={16} />
                Refresh
              </button>
            </div>
          )}
        </div>

        {/* Tab content */}
        {tab === "recordings" ? (
          <RecordingsTab />
        ) : tab === "sessions" ? (
          <Suspense fallback={<div className="text-slate-500 text-center py-12">Loading sessions...</div>}>
            <Transcripts embedded />
          </Suspense>
        ) : (
        <div className="flex-1">
          {loading && events.length === 0 ? (
            <div className="text-center py-12 text-slate-500">
              <Icon name="hourglass_empty" size={32} className="mb-2" />
              <p>Loading activity...</p>
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-12 text-slate-500">
              <Icon name="history" size={32} className="mb-2" />
              <p>Nothing has happened yet.</p>
              <p className="text-xs mt-1">Tasks, agents, and ideas will show up here as you use myOS.</p>
            </div>
          ) : (
            Array.from(grouped.entries()).map(([dateKey, dayEvents]) => (
              <div key={dateKey} className="mb-6">
                {/* Date header */}
                <div className="flex items-center gap-3 mb-3">
                  <span className="text-sm font-semibold text-slate-300">
                    {formatDate(dateKey + "T00:00:00Z")}
                  </span>
                  <div className="flex-1 h-px bg-slate-800" />
                  <span className="text-xs text-slate-600">{dayEvents.length} events</span>
                </div>

                {/* Events for this day */}
                <div className="space-y-1">
                  {dayEvents.map((ev, i) => {
                    const colors = categoryColor[ev.category] || categoryColor.other;
                    const icon = categoryIcon[ev.category] || categoryIcon.other;
                    return (
                      <div
                        key={`${ev.timestamp}-${i}`}
                        className="flex items-start gap-3 px-4 py-2.5 rounded-lg hover:bg-slate-900/60 transition-colors group"
                      >
                        {/* Icon */}
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5 ${colors}`}>
                          <Icon name={icon} size={16} />
                        </div>

                        {/* Content */}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-slate-200">{ev.label}</span>
                            <span className="text-xs text-slate-600 font-mono">{ev.event}</span>
                          </div>
                          {ev.detail && (
                            <p className="text-sm text-slate-400 mt-0.5 truncate">{ev.detail}</p>
                          )}
                        </div>

                        {/* Time */}
                        <span className="text-xs text-slate-600 font-mono shrink-0 mt-1">
                          {formatTime(ev.timestamp)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </div>
        )}
      </div>
    </div>
  );
}
