import { useEffect, useRef, useState } from "react";
import Icon from "./Icon";
import { api } from "../lib/api";

interface Props {
  open: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

type Priority = "P0" | "P1" | "P2" | "P3";

const PRIORITIES: { value: Priority; label: string; description: string }[] = [
  { value: "P0", label: "P0", description: "Top priority" },
  { value: "P1", label: "P1", description: "Normal" },
  { value: "P2", label: "P2", description: "Later" },
  { value: "P3", label: "P3", description: "Low" },
];

export default function QuickAddTaskModal({ open, onClose, onSuccess }: Props) {
  const [title, setTitle] = useState("");
  const [priority, setPriority] = useState<Priority>("P1");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-focus the title field when the modal opens.
  useEffect(() => {
    if (open) {
      setTitle("");
      setPriority("P1");
      setError("");
      // Wait one tick so the input is mounted before focusing.
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  // Escape closes the modal.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  const handleSubmit = async () => {
    if (!title.trim() || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      await api.post("/tasks", { title: title.trim(), priority });
      setTitle("");
      setPriority("P1");
      if (onSuccess) onSuccess();
      onClose();
    } catch {
      setError("Could not create task. Try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) {
      onClose();
    }
  };

  return (
    <div
      role="dialog"
      aria-label="Add a new task"
      onClick={handleBackdropClick}
      data-testid="quick-add-task-backdrop"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-lg p-6 shadow-2xl">
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-2">
            <Icon name="add_task" className="text-blue-400" size={22} />
            <h2 className="text-white font-semibold text-lg">New task</h2>
          </div>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-white transition-colors"
            aria-label="Close"
          >
            <Icon name="close" size={20} />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1.5">
              What needs to get done?
            </label>
            <input
              ref={inputRef}
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Task title"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-pink-500"
            />
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1.5">
              How urgent is it?
            </label>
            <div className="flex gap-2">
              {PRIORITIES.map((p) => {
                const selected = priority === p.value;
                return (
                  <button
                    key={p.value}
                    type="button"
                    onClick={() => setPriority(p.value)}
                    aria-pressed={selected}
                    className={`flex-1 px-3 py-2 rounded-lg border text-sm transition-colors ${
                      selected
                        ? "bg-pink-500/20 border-pink-500 text-pink-300"
                        : "bg-slate-800 border-slate-700 text-slate-300 hover:border-slate-600"
                    }`}
                  >
                    <div className="font-semibold">{p.label}</div>
                    <div className="text-xs opacity-80">{p.description}</div>
                  </button>
                );
              })}
            </div>
          </div>

          {error && <div className="text-red-400 text-sm">{error}</div>}
        </div>

        <div className="flex items-center justify-end gap-2 mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 text-slate-400 hover:text-white text-sm rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!title.trim() || submitting}
            className="bg-pink-500 hover:bg-pink-600 disabled:bg-pink-500/40 disabled:cursor-not-allowed text-white rounded-lg px-4 py-2 text-sm transition-colors"
          >
            {submitting ? "Adding..." : "Add task"}
          </button>
        </div>
      </div>
    </div>
  );
}
