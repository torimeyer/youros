import { useState, useEffect } from "react";
import TopBar from "../components/TopBar";
import Icon from "../components/Icon";
import { api } from "../lib/api";

interface MessageCounts {
  user: number;
  assistant: number;
  tool_calls: number;
}

interface TranscriptSummary {
  session_id: string;
  name: string;
  project: string;
  cwd: string;
  started_at: string;
  kind: string;
  entrypoint: string;
  first_message: string;
  message_counts: MessageCounts;
  file_size_bytes: number;
}

interface TranscriptMessage {
  role: "user" | "assistant";
  text: string;
  tool_uses?: string[];
  timestamp: string;
}

interface TranscriptDetail {
  session_id: string;
  name: string;
  cwd: string;
  started_at: string;
  kind: string;
  total_messages: number;
  messages: TranscriptMessage[];
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

type DateRange = "all" | "today" | "week" | "month";

const DATE_RANGE_OPTIONS: { value: DateRange; label: string }[] = [
  { value: "all", label: "All time" },
  { value: "today", label: "Today" },
  { value: "week", label: "This week" },
  { value: "month", label: "This month" },
];

interface TranscriptListResponse {
  transcripts: TranscriptSummary[];
  total: number;
}

export default function Transcripts() {
  const [transcripts, setTranscripts] = useState<TranscriptSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TranscriptDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Search and filter state
  const [searchQuery, setSearchQuery] = useState("");
  const [dateRange, setDateRange] = useState<DateRange>("all");
  const [kindFilter, setKindFilter] = useState<string>("all");
  const [searchInput, setSearchInput] = useState("");
  const [resultCount, setResultCount] = useState(0);

  // Track all known kinds (from the initial unfiltered load) so the dropdown
  // stays populated even when filters are active.
  const [allKnownKinds, setAllKnownKinds] = useState<string[]>([]);

  const fetchTranscripts = async (search?: string, range?: DateRange, kind?: string) => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (search) params.set("search", search);
      if (range && range !== "all") params.set("date_range", range);
      if (kind && kind !== "all") params.set("kind", kind);
      const query = params.toString();
      const url = `/transcripts${query ? `?${query}` : ""}`;
      const data = await api.get<TranscriptListResponse>(url);
      const items = data.transcripts || [];
      setTranscripts(items);
      setResultCount(data.total ?? items.length);

      // On unfiltered fetches, update the known kinds list
      if (!search && (!range || range === "all") && (!kind || kind === "all")) {
        const kinds = Array.from(new Set(items.map((t) => t.kind).filter(Boolean)));
        setAllKnownKinds(kinds);
      }
    } catch {
      setError("Could not load transcripts.");
    } finally {
      setLoading(false);
    }
  };

  // Initial load (no filters)
  useEffect(() => {
    fetchTranscripts();
  }, []);

  // Re-fetch when date range or kind filter changes
  useEffect(() => {
    fetchTranscripts(searchQuery, dateRange, kindFilter);
  }, [dateRange, kindFilter]);

  const handleSearch = () => {
    setSearchQuery(searchInput);
    fetchTranscripts(searchInput, dateRange, kindFilter);
  };

  const handleSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      handleSearch();
    }
  };

  const clearSearch = () => {
    setSearchInput("");
    setSearchQuery("");
    fetchTranscripts("", dateRange, kindFilter);
  };

  const openTranscript = async (sessionId: string) => {
    setSelectedId(sessionId);
    setDetailLoading(true);
    setDetail(null);
    try {
      const data = await api.get<TranscriptDetail>(`/transcripts/${sessionId}?limit=200`);
      setDetail(data);
    } catch {
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const goBack = () => {
    setSelectedId(null);
    setDetail(null);
  };

  // Detail view: show the conversation
  if (selectedId) {
    const summary = transcripts.find((t) => t.session_id === selectedId);
    return (
      <>
        <TopBar title="Transcripts" />
        <div className="pt-20 p-8">
          {/* Back button and header */}
          <div className="flex items-center gap-4 mb-6">
            <button
              onClick={goBack}
              className="p-2 text-slate-400 hover:text-white transition-colors rounded-lg hover:bg-slate-800"
            >
              <Icon name="arrow_back" size={20} />
            </button>
            <div>
              <h1 className="text-xl font-bold text-white">
                {summary?.name || detail?.name || "Loading..."}
              </h1>
              <div className="flex items-center gap-4 text-sm text-slate-400 mt-1">
                {(detail?.started_at || summary?.started_at) && (
                  <span>{detail?.started_at || summary?.started_at}</span>
                )}
                {detail?.total_messages !== undefined && (
                  <span>{detail.total_messages} messages</span>
                )}
                {(detail?.cwd || summary?.cwd) && (
                  <span className="font-mono text-xs">{detail?.cwd || summary?.cwd}</span>
                )}
              </div>
            </div>
          </div>

          {detailLoading && (
            <div className="text-slate-400 text-center py-12">Loading conversation...</div>
          )}

          {!detailLoading && detail && detail.messages.length === 0 && (
            <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400">
              This transcript has no readable messages. It may contain only system or tool data.
            </div>
          )}

          {!detailLoading && detail && detail.messages.length > 0 && (
            <div className="flex flex-col gap-4 max-w-6xl">
              {detail.messages.map((msg, i) => (
                <div
                  key={i}
                  className={`rounded-xl p-4 ${
                    msg.role === "user"
                      ? "bg-blue-500/10 border border-blue-500/20"
                      : "bg-slate-900/40 border border-slate-800"
                  }`}
                >
                  <div className="flex items-center gap-2 mb-2">
                    <Icon
                      name={msg.role === "user" ? "person" : "smart_toy"}
                      className={`text-sm ${msg.role === "user" ? "text-blue-400" : "text-purple-400"}`}
                      size={16}
                    />
                    <span
                      className={`text-xs font-semibold uppercase ${
                        msg.role === "user" ? "text-blue-400" : "text-purple-400"
                      }`}
                    >
                      {msg.role === "user" ? "You" : "myOS"}
                    </span>
                    {msg.timestamp && (
                      <span className="text-xs text-slate-500 ml-auto">{msg.timestamp}</span>
                    )}
                  </div>

                  {msg.text && (
                    <div className="text-slate-200 text-sm whitespace-pre-wrap leading-relaxed">
                      {msg.text}
                    </div>
                  )}

                  {msg.tool_uses && msg.tool_uses.length > 0 && (
                    <div className="mt-2 flex flex-col gap-1">
                      {msg.tool_uses.map((tool, j) => (
                        <div
                          key={j}
                          className="text-xs text-slate-400 bg-slate-800/60 rounded px-3 py-1.5 font-mono"
                        >
                          {tool}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}

              {detail.total_messages > detail.messages.length && (
                <div className="text-center text-slate-500 text-sm py-4">
                  Showing {detail.messages.length} of {detail.total_messages} messages.
                </div>
              )}
            </div>
          )}
        </div>
      </>
    );
  }

  // List view
  const hasActiveFilters = searchQuery || dateRange !== "all" || kindFilter !== "all";

  return (
    <>
      <TopBar title="Transcripts" />
      <div className="pt-20 p-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-white">Transcripts</h1>
            <p className="text-slate-400 text-sm mt-1">
              Conversation history from your AI sessions
            </p>
          </div>
          <div className="text-sm text-slate-400">
            {hasActiveFilters
              ? `${resultCount} result${resultCount !== 1 ? "s" : ""} found`
              : `${transcripts.length} session${transcripts.length !== 1 ? "s" : ""}`}
          </div>
        </div>

        {/* Search and filters */}
        <div className="flex flex-wrap items-center gap-3 mb-6">
          {/* Search input */}
          <div className="relative flex-1 min-w-[240px] max-w-md">
            <Icon
              name="search"
              className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500"
              size={18}
            />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              placeholder="Search messages..."
              className="w-full bg-slate-900/60 border border-slate-700 rounded-lg pl-10 pr-10 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-blue-500/50 transition-colors"
            />
            {searchInput && (
              <button
                onClick={clearSearch}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-white transition-colors"
              >
                <Icon name="close" size={16} />
              </button>
            )}
          </div>

          {/* Search button */}
          <button
            onClick={handleSearch}
            disabled={loading}
            className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
          >
            Search
          </button>

          {/* Date range filter */}
          <select
            value={dateRange}
            onChange={(e) => setDateRange(e.target.value as DateRange)}
            className="bg-slate-900/60 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500/50 transition-colors appearance-none cursor-pointer"
          >
            {DATE_RANGE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>

          {/* Kind filter */}
          {allKnownKinds.length > 1 && (
            <select
              value={kindFilter}
              onChange={(e) => setKindFilter(e.target.value)}
              className="bg-slate-900/60 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500/50 transition-colors appearance-none cursor-pointer"
            >
              <option value="all">All types</option>
              {allKnownKinds.map((k) => (
                <option key={k} value={k}>
                  {k === "interactive" ? "Conversations" : k === "task" ? "Agent sessions" : k}
                </option>
              ))}
            </select>
          )}

          {/* Clear filters */}
          {hasActiveFilters && (
            <button
              onClick={() => {
                setSearchInput("");
                setSearchQuery("");
                setDateRange("all");
                setKindFilter("all");
                fetchTranscripts("", "all", "all");
              }}
              className="text-sm text-slate-400 hover:text-white transition-colors flex items-center gap-1"
            >
              <Icon name="filter_list_off" size={16} />
              Clear filters
            </button>
          )}
        </div>

        {loading && (
          <div className="text-slate-400 text-center py-12">Loading transcripts...</div>
        )}

        {error && (
          <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4 text-red-400 text-sm">
            {error}
          </div>
        )}

        {!loading && !error && transcripts.length === 0 && (
          <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400">
            {hasActiveFilters
              ? "No transcripts match your search. Try different terms or clear the filters."
              : "No transcripts found. Conversations you have with AI will appear here."}
          </div>
        )}

        {!loading && !error && transcripts.length > 0 && (
          <div className="flex flex-col gap-3">
            {transcripts.map((t) => (
              <button
                key={t.session_id}
                onClick={() => openTranscript(t.session_id)}
                className="bg-slate-900/40 border border-slate-800 rounded-xl p-5 text-left hover:border-blue-500/40 transition-colors group w-full"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3 mb-2">
                      <Icon
                        name="description"
                        className="text-slate-500 group-hover:text-blue-400 transition-colors"
                        size={20}
                      />
                      <span className="text-white font-semibold truncate">{t.name}</span>
                      {t.kind && t.kind !== "unknown" && (
                        <span className="text-xs bg-slate-700/50 text-slate-400 px-2 py-0.5 rounded">
                          {t.kind === "interactive" ? "conversation" : t.kind === "task" ? "agent" : t.kind}
                        </span>
                      )}
                    </div>
                    {t.first_message && t.first_message !== t.name && (
                      <p className="text-slate-400 text-sm truncate ml-8">{t.first_message}</p>
                    )}
                  </div>

                  <div className="flex flex-col items-end gap-1 shrink-0 text-right">
                    {t.started_at && (
                      <span className="text-xs text-slate-500">{t.started_at}</span>
                    )}
                    <div className="flex items-center gap-3 text-xs text-slate-500">
                      <span>
                        {t.message_counts.user + t.message_counts.assistant} messages
                      </span>
                      {t.message_counts.tool_calls > 0 && (
                        <span>{t.message_counts.tool_calls} tool calls</span>
                      )}
                      <span>{formatSize(t.file_size_bytes)}</span>
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
