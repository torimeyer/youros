import Icon from "../../components/Icon";

export type StatusFilter = "open" | "all" | "closed" | "week" | "recurring" | "shelved" | "in_progress";

interface Thread {
  id: string;
  name: string;
}

export type ClosedSortOrder = "newest" | "oldest";

export type SortBy = "date-desc" | "date-asc" | "status" | "label";

interface FilterDrawerProps {
  open?: boolean;
  statusFilter: StatusFilter;
  threadFilter: string | null;
  threads: Thread[];
  filterCounts: Partial<Record<StatusFilter, number>>;
  closedSortOrder?: ClosedSortOrder;
  sortBy?: SortBy;
  onStatusChange: (f: StatusFilter) => void;
  onThreadChange: (id: string | null) => void;
  onClosedSortOrderChange?: (order: ClosedSortOrder) => void;
  onSortByChange?: (s: SortBy) => void;
}

export function FilterDrawer({
  open: _open,
  statusFilter,
  threadFilter,
  threads,
  filterCounts,
  closedSortOrder = "newest",
  sortBy = "date-desc",
  onStatusChange,
  onThreadChange,
  onClosedSortOrderChange,
  onSortByChange,
}: FilterDrawerProps) {
  const statusFilterClass = (f: StatusFilter) =>
    statusFilter === f
      ? "px-3 py-1.5 rounded-md bg-slate-800 text-white font-medium flex items-center gap-1.5 text-sm"
      : "px-3 py-1.5 rounded-md text-slate-400 hover:text-slate-300 flex items-center gap-1.5 text-sm";

  return (
    <div
      data-testid="filter-drawer"
      className="mb-4 bg-slate-900/80 border border-slate-800 rounded-xl p-4 space-y-4"
    >
      {/* Status row */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide w-16 shrink-0">
          Status
        </span>
        <div className="flex items-center gap-1 flex-wrap">
          {(["open", "in_progress", "all", "closed", "shelved", "week"] as StatusFilter[]).map((f) => (
            <button
              key={f}
              data-testid={`status-filter-${f}`}
              className={statusFilterClass(f)}
              onClick={() => onStatusChange(f)}
            >
              {f === "week"
                ? "This week"
                : f === "shelved"
                ? "Paused"
                : f === "in_progress"
                ? "In progress"
                : f === "all"
                ? "All tasks"
                : f === "open"
                ? "Open only"
                : f.charAt(0).toUpperCase() + f.slice(1)}
              {filterCounts[f] !== undefined && (
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                    statusFilter === f
                      ? "bg-blue-500/30 text-blue-300"
                      : "bg-slate-700 text-slate-500"
                  }`}
                >
                  {filterCounts[f]}
                </span>
              )}
            </button>
          ))}
          <button
            className={statusFilterClass("recurring")}
            data-testid="status-filter-recurring"
            onClick={() => onStatusChange("recurring")}
          >
            Recurring
          </button>
        </div>
      </div>

      {/* Sort by row */}
      {onSortByChange && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide w-16 shrink-0">
            Sort by
          </span>
          <div className="flex items-center gap-1 flex-wrap">
            {(
              [
                { value: "date-desc", label: "Newest first" },
                { value: "date-asc", label: "Oldest first" },
                { value: "status", label: "Status" },
                { value: "label", label: "Label" },
              ] as { value: SortBy; label: string }[]
            ).map(({ value, label }) => (
              <button
                key={value}
                data-testid={`sort-by-${value}`}
                onClick={() => onSortByChange(value)}
                className={
                  sortBy === value
                    ? "px-3 py-1.5 rounded-md bg-slate-800 text-white font-medium flex items-center gap-1.5 text-sm"
                    : "px-3 py-1.5 rounded-md text-slate-400 hover:text-slate-300 flex items-center gap-1.5 text-sm"
                }
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Sort order row — only shown for closed tasks */}
      {statusFilter === "closed" && onClosedSortOrderChange && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide w-16 shrink-0">
            Sort
          </span>
          <div className="flex items-center gap-1">
            <button
              data-testid="closed-sort-newest"
              onClick={() => onClosedSortOrderChange("newest")}
              className={closedSortOrder === "newest"
                ? "px-3 py-1.5 rounded-md bg-slate-800 text-white font-medium flex items-center gap-1.5 text-sm"
                : "px-3 py-1.5 rounded-md text-slate-400 hover:text-slate-300 flex items-center gap-1.5 text-sm"}
            >
              Newest first
            </button>
            <button
              data-testid="closed-sort-oldest"
              onClick={() => onClosedSortOrderChange("oldest")}
              className={closedSortOrder === "oldest"
                ? "px-3 py-1.5 rounded-md bg-slate-800 text-white font-medium flex items-center gap-1.5 text-sm"
                : "px-3 py-1.5 rounded-md text-slate-400 hover:text-slate-300 flex items-center gap-1.5 text-sm"}
            >
              Oldest first
            </button>
          </div>
        </div>
      )}

      {/* Groups row */}
      {threads.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide w-16 shrink-0">
            Groups
          </span>
          <div className="flex items-center gap-1.5 flex-wrap">
            {threads.map((thread) => (
              <button
                key={thread.id}
                onClick={() => onThreadChange(threadFilter === thread.id ? null : thread.id)}
                className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium transition-all bg-teal-500/20 text-teal-400 ${
                  threadFilter === thread.id ? "ring-1 ring-white/30" : "opacity-60 hover:opacity-100"
                }`}
              >
                <Icon name="folder" className="text-[10px]" />
                {thread.name}
              </button>
            ))}
            {threadFilter && (
              <button
                onClick={() => onThreadChange(null)}
                className="text-xs text-slate-500 hover:text-slate-300 px-1"
                title="Clear group filter"
              >
                <Icon name="close" className="text-sm" />
              </button>
            )}
          </div>
        </div>
      )}

    </div>
  );
}
