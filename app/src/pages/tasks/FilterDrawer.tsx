import Icon from "../../components/Icon";

// Tri-state status set. A task is shown when its effective status is in the
// selected set. "In progress" reflects live agent activity, not just stored
// status.
export type StatusFilter = "open" | "in_progress" | "closed";

interface Thread {
  id: string;
  name: string;
}

export type ClosedSortOrder = "newest" | "oldest";

export type SortBy = "date-desc" | "date-asc" | "status" | "label";

interface FilterDrawerProps {
  open?: boolean;
  selectedStatuses: Set<StatusFilter>;
  threadFilter: string | null;
  threads: Thread[];
  filterCounts: Partial<Record<StatusFilter, number>>;
  sortBy?: SortBy;
  onStatusToggle: (s: StatusFilter) => void;
  onThreadChange: (id: string | null) => void;
  onSortByChange?: (s: SortBy) => void;
}

const STATUS_LABELS: Record<StatusFilter, string> = {
  open: "Open",
  in_progress: "In progress",
  closed: "Closed",
};

export function FilterDrawer({
  open: _open,
  selectedStatuses,
  threadFilter,
  threads,
  filterCounts,
  sortBy = "date-desc",
  onStatusToggle,
  onThreadChange,
  onSortByChange,
}: FilterDrawerProps) {
  const pillClass = (active: boolean) =>
    active
      ? "px-3 py-1.5 rounded-md bg-slate-800 text-white font-medium flex items-center gap-1.5 text-sm"
      : "px-3 py-1.5 rounded-md text-slate-400 hover:text-slate-300 flex items-center gap-1.5 text-sm";

  return (
    <div
      data-testid="filter-drawer"
      className="mb-4 bg-slate-900/80 border border-slate-800 rounded-xl p-4 space-y-4"
    >
      {/* Status row: three pills, multi-select. At least one must remain selected. */}
      <div
        data-testid="filter-drawer-status-row"
        className="flex flex-wrap items-center gap-2"
      >
        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide w-16 shrink-0">
          Status
        </span>
        <div className="flex items-center gap-1 flex-wrap">
          {(["open", "in_progress", "closed"] as StatusFilter[]).map((f) => {
            const active = selectedStatuses.has(f);
            return (
              <button
                key={f}
                data-testid={`status-filter-${f}`}
                aria-pressed={active}
                className={pillClass(active)}
                onClick={() => onStatusToggle(f)}
              >
                {STATUS_LABELS[f]}
                {filterCounts[f] !== undefined && (
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                      active
                        ? "bg-blue-500/30 text-blue-300"
                        : "bg-slate-700 text-slate-500"
                    }`}
                  >
                    {filterCounts[f]}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Sort by row */}
      {onSortByChange && (
        <div
          data-testid="filter-drawer-sort-row"
          className="flex flex-wrap items-center gap-2"
        >
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
                className={pillClass(sortBy === value)}
              >
                {label}
              </button>
            ))}
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
