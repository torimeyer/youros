import { useEffect, useRef } from "react";

export type TimePeriod = "today" | "week" | "month" | "all";

export const DEFAULT_TIME_LABELS: Record<TimePeriod, string> = {
  today: "Today",
  week: "This Week",
  month: "This Month",
  all: "All Time",
};

export const TIME_PERIOD_ORDER: TimePeriod[] = ["today", "week", "month"];

interface TimeFilterProps {
  value: string;
  onChange: (period: TimePeriod) => void;
  labels?: Partial<Record<TimePeriod, string>>;
  includeAll?: boolean;
}

/**
 * Canonical pill-button row for time period selection.
 * Accessible: role="radiogroup", arrow-key navigation, Enter/Space to select.
 */
export default function TimeFilter({ value, onChange, labels, includeAll }: TimeFilterProps) {
  const resolvedLabels = { ...DEFAULT_TIME_LABELS, ...labels };
  const containerRef = useRef<HTMLDivElement>(null);
  const effectiveOrder: TimePeriod[] = includeAll
    ? ["all", ...TIME_PERIOD_ORDER]
    : TIME_PERIOD_ORDER;

  function handleKeyDown(e: React.KeyboardEvent<HTMLButtonElement>, period: TimePeriod) {
    const idx = effectiveOrder.indexOf(period);
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      const next = effectiveOrder[(idx + 1) % effectiveOrder.length];
      onChange(next);
      focusPill(next);
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      const prev = effectiveOrder[(idx - 1 + effectiveOrder.length) % effectiveOrder.length];
      onChange(prev);
      focusPill(prev);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onChange(period);
    }
  }

  function focusPill(period: string) {
    if (containerRef.current) {
      const btn = containerRef.current.querySelector<HTMLButtonElement>(
        `[data-period="${period}"]`
      );
      btn?.focus();
    }
  }

  // When value changes externally (e.g. localStorage restore), keep focus in sync
  useEffect(() => {
    // Only move focus if the container already has focus, to avoid stealing it on mount
    if (containerRef.current?.contains(document.activeElement)) {
      focusPill(value);
    }
  }, [value]);

  return (
    <div
      ref={containerRef}
      role="radiogroup"
      aria-label="Time period"
      className="flex gap-2"
    >
      {effectiveOrder.map((period) => (
        <button
          key={period}
          role="radio"
          aria-checked={value === period}
          data-period={period}
          data-testid={`time-filter-${period}`}
          tabIndex={value === period ? 0 : -1}
          onClick={() => onChange(period)}
          onKeyDown={(e) => handleKeyDown(e, period)}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
            value === period
              ? "bg-blue-500/20 text-blue-400 border border-blue-500/50"
              : "bg-slate-900 text-slate-400 border border-slate-800 hover:border-slate-700 hover:text-slate-300"
          }`}
        >
          {resolvedLabels[period]}
        </button>
      ))}
    </div>
  );
}
