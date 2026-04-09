import { useEffect, useRef, useState } from "react";
import Icon from "./Icon";
import { api } from "../lib/api";

interface Props {
  open: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

export default function QuickCaptureIdeaModal({ open, onClose, onSuccess }: Props) {
  const [straw, setStraw] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-focus the textarea when the modal opens.
  useEffect(() => {
    if (open) {
      setStraw("");
      setError("");
      setTimeout(() => textareaRef.current?.focus(), 0);
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
    if (!straw.trim() || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      await api.post("/ideas", { thought: straw.trim() });
      setStraw("");
      if (onSuccess) onSuccess();
      onClose();
    } catch {
      setError("Could not save idea. Try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
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
      aria-label="Capture a new idea"
      onClick={handleBackdropClick}
      data-testid="quick-capture-idea-backdrop"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-lg p-6 shadow-2xl">
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-2">
            <Icon name="note_add" className="text-pink-400" size={22} />
            <h2 className="text-white font-semibold text-lg">Capture idea</h2>
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
              What is on your mind?
            </label>
            <textarea
              ref={textareaRef}
              value={straw}
              onChange={(e) => setStraw(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Drop the thought here. Enter to save, Shift+Enter for a new line."
              rows={4}
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-pink-500 resize-none"
            />
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
            disabled={!straw.trim() || submitting}
            className="bg-pink-500 hover:bg-pink-600 disabled:bg-pink-500/40 disabled:cursor-not-allowed text-white rounded-lg px-4 py-2 text-sm transition-colors"
          >
            {submitting ? "Saving..." : "Save idea"}
          </button>
        </div>
      </div>
    </div>
  );
}
