import { useState, useEffect } from "react";
import Icon from "./Icon";
import { api } from "../lib/api";

// What the backend returns for one recurring task rule.
interface Rule {
  id: string;
  task_template: {
    title: string;
    description: string;
    priority: string;
    ac: string;
  };
  schedule: {
    kind: "daily" | "weekly" | "monthly";
    days?: number[];
    day_of_month?: number;
  };
  last_spawned_at: string | null;
  active: boolean;
}

type ScheduleKind = "daily" | "weekly" | "monthly";

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// Turn a schedule into a plain language summary for the list view.
function scheduleLabel(schedule: Rule["schedule"]): string {
  if (schedule.kind === "daily") {
    const days = schedule.days || [];
    if (days.length === 7) return "Every day";
    if (days.length === 5 && days.every((d) => d < 5)) return "Every weekday";
    if (days.length === 2 && days.includes(5) && days.includes(6)) return "Every weekend";
    return `Every ${days.map((d) => DAY_LABELS[d]).join(", ")}`;
  }
  if (schedule.kind === "weekly") {
    const days = schedule.days || [];
    if (!days.length) return "Weekly";
    return `Every ${days.map((d) => DAY_LABELS[d]).join(", ")}`;
  }
  if (schedule.kind === "monthly") {
    const dom = schedule.day_of_month || 1;
    const suffix = dom === 1 || dom === 21 || dom === 31 ? "st"
      : dom === 2 || dom === 22 ? "nd"
      : dom === 3 || dom === 23 ? "rd"
      : "th";
    return `On the ${dom}${suffix} of each month`;
  }
  return "";
}

const EMPTY_FORM = {
  title: "",
  description: "",
  priority: "P1",
  ac: "",
  kind: "weekly" as ScheduleKind,
  days: [1] as number[], // Tuesday by default for weekly
  day_of_month: 1,
};

interface Props {
  // When rendered inside a card, we let the parent provide the outer class.
  cardClass?: string;
}

export default function RecurringTasksSection({ cardClass }: Props) {
  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const fetchRules = async () => {
    setLoading(true);
    try {
      const res = await api.get<{ rules: Rule[] }>("/recurring");
      setRules(res.rules || []);
    } catch {
      setRules([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRules();
  }, []);

  const openAdd = () => {
    setForm(EMPTY_FORM);
    setError("");
    setShowModal(true);
  };

  const close = () => {
    setShowModal(false);
    setError("");
  };

  const toggleDay = (day: number) => {
    setForm((f) => ({
      ...f,
      days: f.days.includes(day)
        ? f.days.filter((d) => d !== day)
        : [...f.days, day].sort(),
    }));
  };

  const handleCreate = async () => {
    if (!form.title.trim()) {
      setError("Give the task a title so we know what to create.");
      return;
    }
    if ((form.kind === "daily" || form.kind === "weekly") && form.days.length === 0) {
      setError("Pick at least one day of the week.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const schedule: Rule["schedule"] =
        form.kind === "monthly"
          ? { kind: "monthly", day_of_month: form.day_of_month }
          : { kind: form.kind, days: form.days };
      await api.post("/recurring", {
        task_template: {
          title: form.title.trim(),
          description: form.description.trim(),
          priority: form.priority,
          ac: form.ac.trim(),
        },
        schedule,
        active: true,
      });
      close();
      await fetchRules();
    } catch {
      setError("Could not save that. Try again.");
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async (rule: Rule) => {
    try {
      await api.patch(`/recurring/${rule.id}`, {});
      await fetchRules();
    } catch {
      // no-op; list refresh is cheap enough
    }
  };

  const handleDelete = async (rule: Rule) => {
    if (!confirm(`Remove the recurring task "${rule.task_template.title}"?`)) return;
    try {
      await api.delete(`/recurring/${rule.id}`);
      await fetchRules();
    } catch {
      // no-op
    }
  };

  const outerClass =
    cardClass ||
    "bg-slate-900/40 border border-slate-800 p-6 rounded-xl hover:border-slate-700 transition-colors";

  return (
    <div className={outerClass} data-testid="recurring-tasks-section">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold">Recurring tasks</h2>
          <p className="text-xs text-slate-500 mt-1">
            Tasks that create themselves again on a schedule.
          </p>
        </div>
        <button
          onClick={openAdd}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 border border-slate-700 rounded-lg text-sm text-slate-300 hover:text-white hover:border-slate-600 transition-colors"
          data-testid="recurring-add-button"
        >
          <Icon name="add" size={16} />
          Add recurring task
        </button>
      </div>

      {loading && (
        <p className="text-sm text-slate-500">Loading...</p>
      )}

      {!loading && rules.length === 0 && (
        <p className="text-sm text-slate-500">
          No recurring tasks yet. Add one to have it show up automatically each day, week, or month.
        </p>
      )}

      {!loading && rules.length > 0 && (
        <div className="flex flex-col gap-2" data-testid="recurring-list">
          {rules.map((rule) => (
            <div
              key={rule.id}
              className={`flex items-start justify-between gap-3 p-3 rounded-lg border ${
                rule.active ? "border-slate-700 bg-slate-800/40" : "border-slate-800 opacity-60"
              }`}
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-200 truncate">
                  {rule.task_template.title}
                </p>
                <p className="text-xs text-slate-500 mt-0.5">
                  {scheduleLabel(rule.schedule)}
                  {rule.task_template.priority ? ` · ${rule.task_template.priority}` : ""}
                </p>
                {rule.task_template.description && (
                  <p className="text-xs text-slate-500 mt-1 truncate">
                    {rule.task_template.description}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => handleToggle(rule)}
                  aria-pressed={rule.active}
                  className={`text-xs px-2 py-1 rounded transition-colors ${
                    rule.active
                      ? "text-emerald-300 hover:text-emerald-200"
                      : "text-slate-500 hover:text-slate-300"
                  }`}
                  title={rule.active ? "Pause this recurring task" : "Resume this recurring task"}
                >
                  {rule.active ? "Active" : "Paused"}
                </button>
                <button
                  onClick={() => handleDelete(rule)}
                  className="text-xs text-red-400 hover:text-red-300 transition-colors px-2 py-1 rounded"
                  title="Remove"
                >
                  Remove
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showModal && (
        <div
          role="dialog"
          aria-label="Add a recurring task"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          data-testid="recurring-modal"
          onClick={(e) => {
            if (e.target === e.currentTarget) close();
          }}
        >
          <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-lg p-6 shadow-2xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-start justify-between mb-4">
              <h3 className="text-white font-semibold text-lg">New recurring task</h3>
              <button
                onClick={close}
                className="text-slate-500 hover:text-white transition-colors"
                aria-label="Close"
              >
                <Icon name="close" size={20} />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-xs text-slate-400 mb-1.5">Title</label>
                <input
                  type="text"
                  value={form.title}
                  onChange={(e) => setForm({ ...form, title: e.target.value })}
                  placeholder="Weekly status update"
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-pink-500"
                  data-testid="recurring-title-input"
                />
              </div>

              <div>
                <label className="block text-xs text-slate-400 mb-1.5">Description</label>
                <textarea
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  placeholder="What needs to be done and why."
                  rows={2}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-pink-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">Priority</label>
                  <select
                    value={form.priority}
                    onChange={(e) => setForm({ ...form, priority: e.target.value })}
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-pink-500"
                  >
                    <option value="P0">P0 (top priority)</option>
                    <option value="P1">P1 (normal)</option>
                    <option value="P2">P2 (later)</option>
                    <option value="P3">P3 (low)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">How often?</label>
                  <select
                    value={form.kind}
                    onChange={(e) => setForm({ ...form, kind: e.target.value as ScheduleKind })}
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-pink-500"
                    data-testid="recurring-kind-select"
                  >
                    <option value="daily">Daily</option>
                    <option value="weekly">Weekly</option>
                    <option value="monthly">Monthly</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-xs text-slate-400 mb-1.5">
                  Acceptance criteria (optional)
                </label>
                <input
                  type="text"
                  value={form.ac}
                  onChange={(e) => setForm({ ...form, ac: e.target.value })}
                  placeholder="How you'll know the task is done."
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-pink-500"
                />
              </div>

              {(form.kind === "daily" || form.kind === "weekly") && (
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">
                    Which days of the week?
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {DAY_LABELS.map((label, idx) => {
                      const selected = form.days.includes(idx);
                      return (
                        <button
                          key={label}
                          type="button"
                          onClick={() => toggleDay(idx)}
                          aria-pressed={selected}
                          className={`px-3 py-1.5 rounded-lg border text-sm transition-colors ${
                            selected
                              ? "bg-pink-500/20 border-pink-500 text-pink-300"
                              : "bg-slate-800 border-slate-700 text-slate-300 hover:border-slate-600"
                          }`}
                        >
                          {label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {form.kind === "monthly" && (
                <div>
                  <label className="block text-xs text-slate-400 mb-1.5">
                    Day of the month (1 to 31)
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={31}
                    value={form.day_of_month}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        day_of_month: Math.max(1, Math.min(31, Number(e.target.value) || 1)),
                      })
                    }
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2 text-white focus:outline-none focus:border-pink-500"
                  />
                  <p className="text-xs text-slate-500 mt-1">
                    If a month is shorter (like February), the task fires on that month's last day.
                  </p>
                </div>
              )}

              {error && <div className="text-red-400 text-sm">{error}</div>}
            </div>

            <div className="flex items-center justify-end gap-2 mt-6">
              <button
                onClick={close}
                className="px-4 py-2 text-slate-400 hover:text-white text-sm rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={saving}
                className="bg-pink-500 hover:bg-pink-600 disabled:bg-pink-500/40 disabled:cursor-not-allowed text-white rounded-lg px-4 py-2 text-sm transition-colors"
                data-testid="recurring-save-button"
              >
                {saving ? "Saving..." : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
