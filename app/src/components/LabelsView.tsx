import { useState, useEffect, useCallback } from "react";
import SharePopover from "./SharePopover";
import Icon from "./Icon";
import { api } from "../lib/api";
import { reportError } from '../lib/reportError';

export interface Label {
  id: string;
  name: string;
  color: string;
  task_count: number;
  open_count?: number;
  closed_count?: number;
}

interface LabelsResponse {
  labels: Label[];
}

interface ColorsResponse {
  colors: string[];
}

interface CreateLabelResponse {
  label: Label;
}

interface Props {
  /** Called when the user clicks a label to filter tasks by it. */
  onFilterByLabel?: (labelId: string | null) => void;
  /** The currently active label filter, if any. */
  activeLabelId?: string | null;
  /** Callback to notify parent that labels changed (so task list can refresh). */
  onLabelsChanged?: () => void;
}

export default function LabelsView({ onFilterByLabel, activeLabelId, onLabelsChanged }: Props) {
  const [labels, setLabels] = useState<Label[]>([]);
  const [colors, setColors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newColor, setNewColor] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sharingLabelId, setSharingLabelId] = useState<string | null>(null);
  const [labelAllBusy, setLabelAllBusy] = useState(false);
  const [labelAllResult, setLabelAllResult] = useState<string | null>(null);
  const [labelAllError, setLabelAllError] = useState<string | null>(null);

  const fetchLabels = useCallback(async () => {
    try {
      const res = await api.get<LabelsResponse>("/labels");
      setLabels(res.labels ?? []);
    } catch (e) {
      reportError('Failed to fetch labels', e);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchColors = useCallback(async () => {
    try {
      const res = await api.get<ColorsResponse>("/labels/colors");
      setColors(res.colors ?? []);
    } catch (e) {
      reportError('Failed to fetch colors', e);
    }
  }, []);

  useEffect(() => {
    fetchLabels();
    fetchColors();
  }, [fetchLabels, fetchColors]);

  const createLabel = async () => {
    const name = newName.trim();
    if (!name) return;
    // Pick a random color if the user didn't choose one
    const color = newColor || colors[Math.floor(Math.random() * colors.length)] || "#3b82f6";
    setNewColor(color);
    setError(null);
    try {
      const res = await api.post<CreateLabelResponse>("/labels", { name, color });
      setLabels((prev) => [...prev, res.label]);
      setNewName("");
      setNewColor("");
      setShowCreate(false);
      onLabelsChanged?.();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Could not create label";
      setError(msg);
    }
  };

  const deleteLabel = async (labelId: string) => {
    try {
      await api.delete("/labels/" + labelId);
      setLabels((prev) => prev.filter((l) => l.id !== labelId));
      if (activeLabelId === labelId) {
        onFilterByLabel?.(null);
      }
      onLabelsChanged?.();
    } catch (e) {
      reportError('Failed to delete label', e);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      createLabel();
    }
    if (e.key === "Escape") {
      setShowCreate(false);
      setNewName("");
      setNewColor("");
      setError(null);
    }
  };

  const labelAll = async () => {
    setLabelAllBusy(true);
    setLabelAllResult(null);
    setLabelAllError(null);
    try {
      const res = await api.post<{ processed: number; labeled: number; total_open: number }>(
        "/tasks/backfill-labels"
      );
      // UAT item 10: report honestly. "labeled 0" is ambiguous between
      // "already labeled" and "processed some but matched no label".
      const processed = res.processed ?? 0;
      const labeled = res.labeled ?? 0;
      const totalOpen = res.total_open ?? 0;
      if (labeled > 0) {
        const missed = processed - labeled;
        setLabelAllResult(
          `Labeled ${labeled} item${labeled === 1 ? "" : "s"}.` +
            (missed > 0 ? ` ${missed} had no matching label yet.` : "")
        );
      } else if (processed > 0) {
        setLabelAllResult(`No matching labels for ${processed} item${processed === 1 ? "" : "s"}. Add a few labels first.`);
      } else if (totalOpen > 0) {
        setLabelAllResult("Every open task already has labels.");
      } else {
        setLabelAllResult("No open tasks to label.");
      }
      await fetchLabels();
      onLabelsChanged?.();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Could not apply labels";
      setLabelAllError(msg);
    } finally {
      setLabelAllBusy(false);
    }
  };

  if (loading && labels.length === 0) {
    return <p className="text-sm text-slate-500 py-4">Loading labels...</p>;
  }

  return (
    <div>
      {/* Header with create button */}
      <div className="flex items-center justify-between mb-4">
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Use labels to organize your needles into groups.
        </p>
        <div className="flex items-center gap-2">
          <button
            onClick={labelAll}
            disabled={labelAllBusy}
            className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Icon name="label" className="text-base" />
            {labelAllBusy ? "Labeling..." : "Label all"}
          </button>
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300"
          >
            <Icon name="add" className="text-base" />
            New label
          </button>
        </div>
      </div>
      {labelAllResult !== null && (
        <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">{labelAllResult}</p>
      )}
      {labelAllError && (
        <p className="text-xs text-red-600 dark:text-red-400 mb-3">{labelAllError}</p>
      )}

      {/* Create form */}
      {showCreate && (
        <div className="bg-white/60 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 rounded-lg px-5 py-4 mb-4">
          <div className="flex items-center gap-3 mb-3">
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Label name"
              className="flex-1 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-700 dark:text-slate-300 placeholder-slate-600 focus:outline-none focus:border-slate-500"
              autoFocus
            />
            <button
              onClick={createLabel}
              disabled={!newName.trim() || !newColor}
              className="px-4 py-2 text-sm rounded-lg bg-blue-500 hover:bg-blue-600 text-white disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Create
            </button>
            <button
              onClick={() => {
                setShowCreate(false);
                setNewName("");
                setNewColor("");
                setError(null);
              }}
              className="p-2 text-slate-600 dark:text-slate-400 hover:text-white"
            >
              <Icon name="close" className="text-base" />
            </button>
          </div>

          {/* Color picker */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500 mr-1">Color:</span>
            {colors.map((color) => (
              <button
                key={color}
                onClick={() => setNewColor(color)}
                className={`w-6 h-6 rounded-full transition-all ${
                  newColor === color ? "ring-2 ring-white ring-offset-2 ring-offset-slate-900 scale-110" : "hover:scale-110"
                }`}
                style={{ backgroundColor: color }}
                title={color}
              />
            ))}
          </div>

          {/* Preview */}
          {newName.trim() && newColor && (
            <div className="mt-3 flex items-center gap-2">
              <span className="text-xs text-slate-500">Preview:</span>
              <span
                className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium"
                style={{
                  backgroundColor: newColor + "20",
                  color: newColor,
                  border: `1px solid ${newColor}40`,
                }}
              >
                {newName.trim()}
              </span>
            </div>
          )}

          {error && (
            <p className="mt-2 text-xs text-red-600 dark:text-red-400">{error}</p>
          )}
        </div>
      )}

      {/* Labels grid */}
      {labels.length === 0 && !showCreate ? (
        <div className="text-center py-12">
          <Icon name="label" className="text-4xl text-slate-600 mb-3" />
          <p className="text-slate-600 dark:text-slate-400 text-sm">No labels yet.</p>
          <p className="text-slate-500 text-xs mt-1">
            Create labels to organize your needles into groups.
          </p>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {labels.map((label) => (
            <div
              key={label.id}
              className={`group flex items-center gap-2 px-3 py-2 rounded-lg border transition-all cursor-pointer ${
                activeLabelId === label.id
                  ? "border-white/30 bg-slate-100 dark:bg-slate-800"
                  : "border-slate-200 dark:border-slate-800 bg-white/60 dark:bg-slate-900/60 hover:border-slate-200 dark:hover:border-slate-700"
              }`}
              onClick={() => {
                if (activeLabelId === label.id) {
                  onFilterByLabel?.(null);
                } else {
                  onFilterByLabel?.(label.id);
                }
              }}
            >
              {/* Color dot and name */}
              <span
                className="w-3 h-3 rounded-full flex-shrink-0"
                style={{ backgroundColor: label.color }}
              />
              <span className="text-sm text-slate-700 dark:text-slate-300 font-medium">{label.name}</span>

              {/* Delete button (shows on hover) */}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  deleteLabel(label.id);
                }}
                className="opacity-0 group-hover:opacity-100 p-0.5 text-slate-500 hover:text-red-600 dark:hover:text-red-400 transition-opacity"
                title="Delete label"
              >
                <Icon name="close" className="text-sm" />
              </button>
              {/* Share button (shows on hover) */}
              <div className="relative" onClick={(e) => e.stopPropagation()}>
                <button
                  onClick={() => setSharingLabelId(sharingLabelId === label.id ? null : label.id)}
                  className="opacity-0 group-hover:opacity-100 p-0.5 text-slate-500 hover:text-blue-600 dark:hover:text-blue-400 transition-opacity"
                  title="Share this label view"
                >
                  <Icon name="share" className="text-sm" />
                </button>
                {sharingLabelId === label.id && (
                  <SharePopover
                    shareType="label_view"
                    contentIds={[label.id]}
                    title={`Label: ${label.name}`}
                    onClose={() => setSharingLabelId(null)}
                  />
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
