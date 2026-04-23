import Icon from "../../components/Icon";
import type { Label } from "../../components/LabelsView";

export type StatusFilter = "open" | "all" | "closed" | "week" | "recurring" | "shelved" | "in_progress";

const PRIORITIES = ["P0", "P1", "P2", "P3"] as const;

const priorityStyles: Record<string, string> = {
  P0: "bg-pink-500/20 text-pink-500",
  P1: "bg-orange-500/20 text-orange-500",
  P2: "bg-blue-500/20 text-blue-500",
  P3: "bg-slate-500/20 text-slate-400",
};

const priorityDotColors: Record<string, string> = {
  P0: "bg-pink-500",
  P1: "bg-orange-500",
  P2: "bg-blue-500",
  P3: "bg-slate-500",
};

interface Thread {
  id: string;
  name: string;
}

export type ClosedSortOrder = "newest" | "oldest";

interface FilterDrawerProps {
  open?: boolean;
  statusFilter: StatusFilter;
  priorityFilter: string | null;
  labelFilter: string | null;
  threadFilter: string | null;
  hideSessionTasks: boolean;
  viewMode: "list" | "grid";
  labels: Label[];
  threads: Thread[];
  filterCounts: Partial<Record<StatusFilter, number>>;
  priorityCounts: Record<string, number>;
  closedSortOrder?: ClosedSortOrder;
  onStatusChange: (f: StatusFilter) => void;
  onPriorityChange: (p: string | null) => void;
  onLabelChange: (id: string | null) => void;
  onThreadChange: (id: string | null) => void;
  onSessionToggle: () => void;
  onViewModeChange: (v: "list" | "grid") => void;
  onClosedSortOrderChange?: (order: ClosedSortOrder) => void;
  onClearAll?: () => void;
  onClose: () => void;
}

export function FilterDrawer({
  open: _open,
  statusFilter,
  priorityFilter,
  labelFilter,
  threadFilter,
  hideSessionTasks,
  viewMode,
  labels,
  threads,
  filterCounts,
  priorityCounts,
  closedSortOrder = "newest",
  onStatusChange,
  onPriorityChange,
  onLabelChange,
  onThreadChange,
  onSessionToggle,
  onViewModeChange,
  onClosedSortOrderChange,
  onClearAll,
  onClose,
}: FilterDrawerProps) {
  const hasNonDefaultFilter =
    statusFilter !== "open" ||
    priorityFilter !== null ||
    labelFilter !== null ||
    threadFilter !== null ||
    !hideSessionTasks;

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

      {/* Sort row — only shown for closed tasks */}
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

      {/* Priority row */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide w-16 shrink-0">
          Priority
        </span>
        <div className="flex items-center gap-2 flex-wrap">
          {PRIORITIES.map((p) => (
            <button
              key={p}
              data-testid={`priority-filter-${p}`}
              onClick={() => onPriorityChange(priorityFilter === p ? null : p)}
              className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
                priorityFilter === p
                  ? `${priorityStyles[p]} ring-1 ring-white/30`
                  : `${priorityStyles[p]} opacity-60 hover:opacity-100`
              }`}
            >
              <span className={`w-2 h-2 rounded-full ${priorityDotColors[p]}`} />
              {p} <span className="opacity-70">{priorityCounts[p]}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Labels row */}
      {labels.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide w-16 shrink-0">
            Labels
          </span>
          <div className="flex items-center gap-1.5 flex-wrap">
            {labels.map((label) => (
              <button
                key={label.id}
                data-testid={`label-filter-${label.id}`}
                onClick={() => onLabelChange(labelFilter === label.id ? null : label.id)}
                className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium transition-all ${
                  labelFilter === label.id ? "ring-1 ring-white/30" : "opacity-60 hover:opacity-100"
                }`}
                style={{
                  backgroundColor: label.color + "20",
                  color: label.color,
                }}
              >
                <span className="w-2 h-2 rounded-full" style={{ backgroundColor: label.color }} />
                {label.name}
              </button>
            ))}
            {labelFilter && (
              <button
                onClick={() => onLabelChange(null)}
                className="text-xs text-slate-500 hover:text-slate-300 px-1"
                title="Clear label filter"
                aria-label="Clear label filter"
              >
                <Icon name="close" className="text-sm" />
              </button>
            )}
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

      {/* Bottom row: sessions toggle + clear all + view mode + close */}
      <div className="flex items-center justify-between pt-2 border-t border-slate-800">
        <div className="flex items-center gap-2">
          <button
            onClick={onSessionToggle}
            data-testid="toggle-session-tasks"
            className={`flex items-center gap-1.5 px-3 py-1 text-xs rounded-lg border transition-colors ${
              hideSessionTasks
                ? "bg-slate-800 text-slate-500 border-slate-700 hover:text-slate-300"
                : "bg-slate-700 text-slate-200 border-slate-600"
            }`}
            title={
              hideSessionTasks
                ? "Session tasks are hidden. Click to show them."
                : "Click to hide session tasks."
            }
          >
            <Icon name="terminal" className="text-sm" />
            {hideSessionTasks ? "Sessions hidden" : "Sessions shown"}
          </button>
          {hasNonDefaultFilter && onClearAll && (
            <button
              onClick={onClearAll}
              data-testid="filter-drawer-clear-all"
              className="px-3 py-1 text-xs rounded-lg border border-slate-700 text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
            >
              Clear all
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => onViewModeChange("list")}
            data-testid="view-mode-list"
            className={`p-1.5 ${viewMode === "list" ? "text-slate-300" : "text-slate-600"} hover:text-white`}
            title="List view"
          >
            <Icon name="view_list" className="text-lg" />
          </button>
          <button
            onClick={() => onViewModeChange("grid")}
            data-testid="view-mode-grid"
            className={`p-1.5 ${viewMode === "grid" ? "text-slate-300" : "text-slate-600"} hover:text-white`}
            title="Card view"
          >
            <Icon name="grid_view" className="text-lg" />
          </button>
          <button
            onClick={onClose}
            data-testid="filter-drawer-close"
            className="p-1.5 text-slate-500 hover:text-slate-300"
            title="Close filters"
          >
            <Icon name="close" className="text-sm" />
          </button>
        </div>
      </div>
    </div>
  );
}
