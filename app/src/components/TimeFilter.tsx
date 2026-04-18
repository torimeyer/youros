import { useEffect, useRef } from "react";

export type TimePeriod = "today" | "week" | "month" | "all";

export const DEFAULT_TIME_LABELS: Record<TimePeriod, string> = {
  today: "Today",
  week: "This Week",
  month: "This Month",
  all: "All Time",
};

export const TIME_PERIOD_ORDER: TimePeriod[] = ["today", "week", "month", "all"];

interface TimeFilterProps {
  value: TimePeriod;
  onChange: (period: TimePeriod) => void;
  labels?: Partial<Record<TimePeriod, string>>;
}

/**
 * Canonical pill-button row for time period selection.
 * Accessible: role="radiogroup", arrow-key navigation, Enter/Space to select.
 */
export default function TimeFilter({ value, onChange, labels }: TimeFilterProps) {
  const resolvedLabels = { ...DEFAULT_TIME_LABELS, ...labels };
  const containerRef = useRef<HTMLDivElement>(null);

  function handleKeyDown(e: React.KeyboardEvent<HTMLButtonElement>, period: TimePeriod) {
    const idx = TIME_PERIOD_ORDER.indexOf(period);
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      const next = TIME_PERIOD_ORDER[(idx + 1) % TIME_PERIOD_ORDER.length];
      onChange(next);
      focusPill(next);
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      const prev = TIME_PERIOD_ORDER[(idx - 1 + TIME_PERIOD_ORDER.length) % TIME_PERIOD_ORDER.length];
      onChange(prev);
      focusPill(prev);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onChange(period);
    }
  }

  function focusPill(period: TimePeriod) {
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
      {TIME_PERIOD_ORDER.map((period) => (
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
