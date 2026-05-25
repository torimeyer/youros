import { useEffect, useRef, useState } from "react";
import Icon from "./Icon";

interface SpecUserInput {
  key: string;
  label: string;
  placeholder?: string;
  type: "text" | "textarea";
  required?: boolean;
}

// Shape of a spec template. Kept loose so this modal does not have to
// depend on the full type in Specs.tsx. Only the fields we show or
// submit are referenced below.
export interface SpecTemplateDetails {
  id: string;
  name: string;
  description: string;
  icon: string;
  acceptance_criteria?: string[];
  tasks?: string[];
  user_inputs?: SpecUserInput[];
}

// Values the user entered before applying the template. The parent
// decides what to do with these (e.g. forward to /specs/from-template).
export interface SpecTemplateDetailsValues {
  title: string;
  note: string;
}

interface Props {
  open: boolean;
  template: SpecTemplateDetails | null;
  submitting?: boolean;
  error?: string;
  onClose: () => void;
  onSubmit: (values: SpecTemplateDetailsValues) => void;
}

// Modal shown after clicking a spec template card. Mirrors the agent
// template flow: the user sees what the template will do, can adjust
// the plan name, and can add a short note before applying.
export default function SpecTemplateDetailsModal({
  open,
  template,
  submitting = false,
  error = "",
  onClose,
  onSubmit,
}: Props) {
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const titleRef = useRef<HTMLInputElement>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);

  // Reset fields and focus the name whenever a new template opens.
  useEffect(() => {
    if (open && template) {
      setTitle(template.name);
      setNote("");
      setFieldValues({});
      setTimeout(() => titleRef.current?.focus(), 0);
    }
  }, [open, template]);

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

  if (!open || !template) return null;

  const trimmedTitle = title.trim();
  const hasUserInputs = (template?.user_inputs?.length ?? 0) > 0;
  const requiredFilled = !hasUserInputs || template!.user_inputs!.filter((i) => i.required).every((i) => fieldValues[i.key]?.trim());
  const canSubmit = trimmedTitle.length > 0 && !submitting && requiredFilled;

  function buildSpecMessage(inputs: SpecUserInput[], values: Record<string, string>): string {
    return inputs
      .filter((inp) => values[inp.key]?.trim())
      .map((inp) => `${inp.label}: ${values[inp.key].trim()}`)
      .join("\n");
  }

  const handleSubmit = () => {
    if (!canSubmit) return;
    const builtNote = hasUserInputs
      ? buildSpecMessage(template!.user_inputs!, fieldValues)
      : note.trim();
    onSubmit({ title: trimmedTitle, note: builtNote });
  };

  const handleTitleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      noteRef.current?.focus();
    }
  };

  const handleNoteKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
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

  const criteria = template.acceptance_criteria || [];
  const tasks = template.tasks || [];

  return (
    <div
      role="dialog"
      aria-label="Spec template details"
      onClick={handleBackdropClick}
      data-testid="spec-template-details-backdrop"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div
        className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl w-full max-w-lg p-6 shadow-2xl"
        data-testid="spec-template-details-modal"
      >
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-2">
            <Icon name={template.icon || "description"} className="text-blue-600 dark:text-blue-400" size={22} />
            <h2 className="text-white font-semibold text-lg">
              {template.name}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-white transition-colors"
            aria-label="Close"
          >
            <Icon name="close" size={20} />
          </button>
        </div>

        <p
          className="text-sm text-slate-600 dark:text-slate-400 mb-4"
          data-testid="spec-template-details-description"
        >
          {template.description}
        </p>

        <div className="space-y-4">
          <div>
            <label className="block text-xs text-slate-600 dark:text-slate-400 mb-1.5">
              Spec name
</label>
            <input
              ref={titleRef}
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={handleTitleKeyDown}
              placeholder="Name this spec"
              data-testid="spec-template-title-input"
              className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
            />
          </div>

          {hasUserInputs ? (
            template!.user_inputs!.map((inp) => (
              <div key={inp.key}>
                <label className="block text-xs text-slate-600 dark:text-slate-400 mb-1.5">
                  {inp.label}{!inp.required ? " (optional)" : ""}
                </label>
                {inp.type === "textarea" ? (
                  <textarea
                    value={fieldValues[inp.key] || ""}
                    onChange={(e) => setFieldValues((prev) => ({ ...prev, [inp.key]: e.target.value }))}
                    placeholder={inp.placeholder}
                    rows={3}
                    className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 resize-none"
                  />
                ) : (
                  <input
                    type="text"
                    value={fieldValues[inp.key] || ""}
                    onChange={(e) => setFieldValues((prev) => ({ ...prev, [inp.key]: e.target.value }))}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleSubmit(); } }}
                    placeholder={inp.placeholder}
                    className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
                  />
                )}
              </div>
            ))
          ) : (
            <div>
              <label className="block text-xs text-slate-600 dark:text-slate-400 mb-1.5">
                Anything extra we should know? (optional)
              </label>
              <textarea
                ref={noteRef}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                onKeyDown={handleNoteKeyDown}
                placeholder="Add audience, deadline, or anything else that matters."
                rows={3}
                data-testid="spec-template-note-input"
                className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 resize-none"
              />
            </div>
          )}

          {criteria.length > 0 && (
            <div data-testid="spec-template-ac-preview">
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
                What this spec will ship
</p>
              <ul className="space-y-1">
                {criteria.map((c, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-300">
                    <span className="mt-0.5 w-3 h-3 rounded-full border border-slate-500 flex-shrink-0" />
                    <span>{c}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {tasks.length > 0 && (
            <div data-testid="spec-template-tasks-preview">
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
                Work this spec will break into
              </p>
              <ul className="space-y-1">
                {tasks.map((t, i) => (
                  <li key={i} className="text-sm text-slate-700 dark:text-slate-300">
                    {i + 1}. {t}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {error && (
            <div className="text-red-600 dark:text-red-400 text-sm" data-testid="spec-template-error">
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 text-slate-600 dark:text-slate-400 hover:text-white text-sm rounded-lg transition-colors"
            data-testid="spec-template-cancel"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            data-testid="spec-template-apply"
            className="bg-blue-500 hover:bg-blue-600 disabled:bg-blue-500/40 disabled:cursor-not-allowed text-white rounded-lg px-4 py-2 text-sm transition-colors"
          >
            {submitting ? "Creating spec..." : "Create spec"}
          </button>
        </div>
      </div>
    </div>
  );
}
