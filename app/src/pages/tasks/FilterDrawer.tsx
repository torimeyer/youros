
// Single-select status filter. "In progress" reflects live agent activity,
// not just stored status. "all" shows every non-shelved task.
export type StatusFilter = "all" | "open" | "in_progress" | "closed";

export type ClosedSortOrder = "newest" | "oldest";

export type SortBy = "date-desc" | "date-asc" | "status" | "label" | "wave";

interface FilterDrawerProps {
  open?: boolean;
  selectedStatus: StatusFilter;
  filterCounts: Partial<Record<Exclude<StatusFilter, "all">, number>>;
  sortBy?: SortBy;
  onStatusChange: (s: StatusFilter) => void;
  onSortByChange?: (s: SortBy) => void;
}

const STATUS_LABELS: Record<StatusFilter, string> = {
  all: "All",
  open: "Open",
  in_progress: "In progress",
  closed: "Closed",
};

export function FilterDrawer({
  open: _open,
  selectedStatus,
  filterCounts,
  sortBy = "date-desc",
  onStatusChange,
  onSortByChange,
}: FilterDrawerProps) {
  const pillClass = (active: boolean) =>
    active
      ? "px-3 py-1.5 rounded-md bg-slate-100 dark:bg-slate-800 text-white font-medium flex items-center gap-1.5 text-sm"
      : "px-3 py-1.5 rounded-md text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-700 dark:hover:text-slate-300 flex items-center gap-1.5 text-sm";

  return (
    <div
      data-testid="filter-drawer"
      className="mb-4 bg-slate-100 dark:bg-slate-900/80 border border-slate-200 dark:border-slate-800 rounded-xl p-4 space-y-4"
    >
      {/* Status row: four chips, single-select. */}
      <div
        data-testid="filter-drawer-status-row"
        className="flex flex-wrap items-center gap-2"
      >
        <span className="text-[11px] font-semibold text-slate-600 dark:text-slate-500 uppercase tracking-wide w-16 shrink-0">
          Status
        </span>
        <div className="flex items-center gap-1 flex-wrap">
          {(["all", "in_progress", "open", "closed"] as StatusFilter[]).map((f) => {
            const active = selectedStatus === f;
            const count = f !== "all" ? filterCounts[f] : undefined;
            return (
              <button
                key={f}
                data-testid={`status-filter-${f}`}
                aria-pressed={active}
                className={pillClass(active)}
                onClick={() => onStatusChange(f)}
              >
                {STATUS_LABELS[f]}
                {count !== undefined && (
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                      active
                        ? "bg-blue-500/30 text-blue-700 dark:text-blue-300"
                        : "bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-500"
                    }`}
                  >
                    {count}
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
          <span className="text-[11px] font-semibold text-slate-600 dark:text-slate-500 uppercase tracking-wide w-16 shrink-0">
            Sort by
          </span>
          <div className="flex items-center gap-1 flex-wrap">
            {(
              [
                { value: "date-desc", label: "Newest first" },
                { value: "date-asc", label: "Oldest first" },
                { value: "status", label: "Status" },
                { value: "label", label: "Label" },
                { value: "wave", label: "Wave" },
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

    </div>
  );
}
