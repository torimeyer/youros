import { useState, useEffect, useCallback } from "react";
import Icon from "../components/Icon";
import TopBar from "../components/TopBar";
import { api } from "../lib/api";

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

type CategoryFilter = "all" | "task" | "agent" | "idea" | "system";

const CATEGORY_FILTERS: { value: CategoryFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "task", label: "Tasks" },
  { value: "agent", label: "Agents" },
  { value: "idea", label: "Ideas" },
  { value: "system", label: "System" },
];

const categoryIcon: Record<string, string> = {
  task: "checklist",
  agent: "smart_toy",
  idea: "lightbulb",
  system: "settings",
  other: "info",
};

const categoryColor: Record<string, string> = {
  task: "text-blue-400 bg-blue-500/20",
  agent: "text-purple-400 bg-purple-500/20",
  idea: "text-amber-400 bg-amber-500/20",
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

export default function Activity() {
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

      <div data-tour="activity" className="pt-20 p-8 flex-1 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-4">
            <h1 className="text-2xl font-bold">Activity</h1>
            <span className="text-sm text-slate-500">{filtered.length} events</span>
          </div>
          <div className="flex items-center gap-3">
            {/* Category filter pills */}
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
            {/* Load more */}
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
        </div>

        {/* Timeline feed */}
        <div className="flex-1">
          {loading && events.length === 0 ? (
            <div className="text-center py-12 text-slate-500">
              <Icon name="hourglass_empty" size={32} className="mb-2" />
              <p>Loading activity...</p>
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-12 text-slate-500">
              <Icon name="history" size={32} className="mb-2" />
              <p>No activity to show yet.</p>
              <p className="text-xs mt-1">Events will appear here as you create tasks, run agents, and save ideas.</p>
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
      </div>
    </div>
  );
}
