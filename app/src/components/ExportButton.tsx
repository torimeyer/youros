import { useEffect, useRef, useState } from "react";
import Icon from "./Icon";

export type ExportFormat = "markdown" | "csv";

interface Props {
  /** Path the export endpoint lives at, e.g. "/api/export/tasks?status=open" */
  buildUrl: (format: ExportFormat) => string;
  /** Formats the user can pick from */
  formats?: ExportFormat[];
  /** Friendly label that appears in the success message, e.g. "tasks" */
  contentLabel?: string;
}

const FORMAT_LABELS: Record<ExportFormat, string> = {
  markdown: "As Markdown",
  csv: "As CSV",
};

const FORMAT_EXT: Record<ExportFormat, string> = {
  markdown: "md",
  csv: "csv",
};

export default function ExportButton({
  buildUrl,
  formats = ["markdown", "csv"],
  contentLabel = "file",
}: Props) {
  const [open, setOpen] = useState(false);
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  useEffect(() => {
    if (!confirmation) return;
    const t = setTimeout(() => setConfirmation(null), 3000);
    return () => clearTimeout(t);
  }, [confirmation]);

  const triggerDownload = (format: ExportFormat) => {
    const url = buildUrl(format);
    // Create a temporary anchor and click it. The backend's
    // Content-Disposition header tells the browser to save it.
    const a = document.createElement("a");
    a.href = url;
    a.rel = "noopener";
    // Hint a default filename. The server's header will override this
    // when present, but it gives a sensible fallback.
    a.download = `${contentLabel}.${FORMAT_EXT[format]}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setOpen(false);
    setConfirmation(`Downloaded ${contentLabel}.${FORMAT_EXT[format]}`);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        data-testid="export-button"
        onClick={() => setOpen((v) => !v)}
        className="p-1.5 bg-slate-800 hover:bg-slate-700 rounded-lg border border-slate-700"
        title="Download as a file"
      >
        <Icon name="download" className="text-slate-400 text-base" />
      </button>
      {open && (
        <div
          data-testid="export-menu"
          className="absolute right-0 mt-2 w-44 bg-slate-900 border border-slate-700 rounded-lg shadow-lg z-20"
        >
          {formats.map((f) => (
            <button
              key={f}
              data-testid={`export-option-${f}`}
              onClick={() => triggerDownload(f)}
              className="w-full text-left px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 first:rounded-t-lg last:rounded-b-lg"
            >
              {FORMAT_LABELS[f]}
            </button>
          ))}
        </div>
      )}
      {confirmation && (
        <div
          data-testid="export-confirmation"
          className="absolute right-0 top-10 text-xs text-emerald-300 bg-emerald-500/10 border border-emerald-500/30 rounded-md px-2 py-1 whitespace-nowrap"
        >
          {confirmation}
        </div>
      )}
    </div>
  );
}
