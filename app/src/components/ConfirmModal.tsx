import { useEffect, useRef } from "react";

export interface ConfirmModalProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** When true the confirm button is styled red to signal a destructive action. */
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmModal({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  // Focus the confirm button when modal opens.
  useEffect(() => {
    if (open) {
      setTimeout(() => confirmRef.current?.focus(), 0);
    }
  }, [open]);

  // Keyboard: Enter = confirm, Escape = cancel.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      } else if (e.key === "Enter") {
        e.preventDefault();
        onConfirm();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onConfirm, onCancel]);

  if (!open) return null;

  const handleBackdropClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) {
      onCancel();
    }
  };

  const confirmClass = danger
    ? "bg-red-600 hover:bg-red-700 text-white"
    : "bg-pink-500 hover:bg-pink-600 text-white";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-modal-title"
      aria-describedby="confirm-modal-message"
      data-testid="confirm-modal-backdrop"
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-sm p-6 shadow-2xl">
        <h2
          id="confirm-modal-title"
          className="text-white font-semibold text-base mb-2"
        >
          {title}
        </h2>
        <p
          id="confirm-modal-message"
          className="text-slate-400 text-sm mb-6"
        >
          {message}
        </p>
        <div className="flex items-center justify-end gap-2">
          <button
            data-testid="confirm-modal-cancel"
            onClick={onCancel}
            className="px-4 py-2 text-slate-400 hover:text-white text-sm rounded-lg transition-colors"
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            data-testid="confirm-modal-confirm"
            onClick={onConfirm}
            className={`px-4 py-2 text-sm rounded-lg transition-colors ${confirmClass}`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
