import { useState, useEffect, useRef, useCallback } from "react";
import { formatRelative } from '../lib/time';
import { useNavigate } from "react-router-dom";
import TopBar from "../components/TopBar";
import { EmptyState } from "../components/ui";

interface TeamItem {
  key?: string;
  title?: string;
  id?: string;
  value?: string;
  content?: string;
  reason?: string;
  timestamp?: string;
  created_at?: string;
}

interface TeamResult {
  member_id: string;
  display_name: string;
  category: string;
  item: TeamItem;
}

interface TeamStatus {
  configured: boolean;
  config?: {
    team_repo: string;
    member_id: string;
    display_name: string;
    category_defaults?: Record<string, string>;
    auto_sync?: boolean;
  };
}

const CATEGORY_FILTERS = ["All", "Decisions", "Needles", "Notes"] as const;
type CategoryFilter = (typeof CATEGORY_FILTERS)[number];

const CATEGORY_COLORS: Record<string, string> = {
  decisions: "bg-blue-500/20 text-blue-300",
  tasks: "bg-green-500/20 text-green-300",
  notes: "bg-yellow-500/20 text-yellow-300",
  transcripts: "bg-purple-500/20 text-purple-300",
};



function CategoryBadge({ category }: { category: string }) {
  const colorClass =
    CATEGORY_COLORS[category.toLowerCase()] ?? "bg-slate-500/20 text-slate-300";
  return (
    <span
      className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${colorClass}`}
    >
      {category}
    </span>
  );
}

function ResultItem({ result }: { result: TeamResult }) {
  const { item, display_name, category } = result;
  const title = item.title ?? item.key ?? item.id ?? "";
  const body = item.reason ?? item.content ?? item.value ?? "";
  const ts = item.timestamp ?? item.created_at;
  return (
    <div className="bg-slate-900/50 rounded-xl p-4 border border-slate-800 hover:border-slate-700 transition-colors">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-sm font-semibold text-white">{display_name}</span>
        <CategoryBadge category={category} />
        {ts && (
          <span className="text-[10px] text-slate-500 ml-auto">
            {formatRelative(ts)}
          </span>
        )}
      </div>
      {title && <p className="text-sm text-slate-200 mb-0.5">{title}</p>}
      {body && (
        <p className="text-xs text-slate-400">
          {body.slice(0, 120)}
        </p>
      )}
    </div>
  );
}

export default function Team() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<TeamStatus | null>(null);
  const [query, setQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState<CategoryFilter>("All");
  const [results, setResults] = useState<TeamResult[]>([]);
  const [feed, setFeed] = useState<TeamResult[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [syncedMsg, setSyncedMsg] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    fetch("/api/team")
      .then((r) => r.json())
      .then((d: TeamStatus) => setStatus(d))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!status?.configured) return;
    fetch("/api/team/feed")
      .then((r) => r.json())
      .then((d: { items: TeamResult[]; count: number }) =>
        setFeed(d.items ?? [])
      )
      .catch(() => {});
  }, [status?.configured]);

  const runSearch = useCallback((q: string, filter: CategoryFilter) => {
    const params = new URLSearchParams({ q });
    if (filter !== "All") params.set("category", filter.toLowerCase());
    fetch(`/api/team/search?${params}`)
      .then((r) => r.json())
      .then((d: { results: TeamResult[]; count: number }) =>
        setResults(d.results ?? [])
      )
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!status?.configured) return;
    if (!query.trim()) {
      setResults([]);
      return;
    }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(
      () => runSearch(query, activeFilter),
      400
    );
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, activeFilter, status?.configured, runSearch]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      await fetch("/api/team/sync", { method: "POST" });
      setSyncedMsg(true);
      setTimeout(() => setSyncedMsg(false), 2000);
    } catch {
      // ignore
    } finally {
      setSyncing(false);
    }
  };

  if (status === null) {
    return (
      <div className="min-h-screen bg-slate-950">
        <TopBar title="Team" />
      </div>
    );
  }

  if (!status.configured) {
    return (
      <div className="min-h-screen bg-slate-950">
        <TopBar title="Team" />
        <div className="px-4 sm:px-8 max-w-2xl mx-auto mt-16">
          <EmptyState
            icon="group"
            title="Team mode isn't set up yet"
            description="Connect a shared folder to browse your team's decisions, tasks, and notes."
            action={{
              label: "Configure team",
              onClick: () => navigate("/team/settings"),
            }}
          />
        </div>
      </div>
    );
  }

  const showResults = query.trim().length > 0;

  return (
    <div className="min-h-screen bg-slate-950">
      <TopBar title="Team" />
      <div className="px-4 sm:px-8 max-w-4xl mx-auto py-6">
        <div className="mb-4 px-3 py-2 rounded-lg bg-amber-500/10 text-amber-400 text-sm">
          Team features are in beta. Some things may change.
        </div>
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-xl font-bold text-white">Team</h1>
          <button
            onClick={handleSync}
            disabled={syncing}
            className="flex items-center gap-2 px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 text-sm font-medium rounded-lg border border-slate-700 transition-colors disabled:opacity-50"
            data-testid="sync-now-button"
          >
            {syncedMsg ? "Synced" : syncing ? "Syncing..." : "Sync now"}
          </button>
        </div>

        <div className="mb-4">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && query.trim()) {
                if (debounceRef.current) clearTimeout(debounceRef.current);
                runSearch(query, activeFilter);
              }
            }}
            placeholder="Search your team's knowledge..."
            className="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
            data-testid="team-search-input"
          />
        </div>

        <div className="flex gap-2 mb-6">
          {CATEGORY_FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setActiveFilter(f)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                activeFilter === f
                  ? "bg-blue-600 text-white"
                  : "bg-slate-800 text-slate-400 hover:text-white hover:bg-slate-700"
              }`}
              data-testid={`filter-${f.toLowerCase()}`}
            >
              {f}
            </button>
          ))}
        </div>

        {showResults ? (
          <div className="space-y-3">
            {results.length === 0 ? (
              <p className="text-sm text-slate-500 text-center py-8">
                No results found.
              </p>
            ) : (
              results.map((r, i) => <ResultItem key={i} result={r} />)
            )}
          </div>
        ) : (
          <div>
            <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">
              Recent from team
            </h2>
            {feed.length === 0 ? (
              <p className="text-sm text-slate-500 text-center py-8">
                Nothing shared yet. Sync to pull team updates.
              </p>
            ) : (
              <div className="space-y-3">
                {feed.slice(0, 10).map((r, i) => (
                  <ResultItem key={i} result={r} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
