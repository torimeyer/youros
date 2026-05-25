// →1070 agent template file uploads
import { useCallback, useRef, useState } from "react";

interface AgentTemplateFileUploadProps {
  templateId: string;
  attachedFiles: string[];
  onFilesChange: (files: string[]) => void;
}

const ALLOWED_EXTENSIONS = [".txt", ".md", ".pdf", ".docx"];
const MAX_SIZE_MB = 5;

export default function AgentTemplateFileUpload({
  templateId,
  attachedFiles,
  onFilesChange,
}: AgentTemplateFileUploadProps) {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const uploadFile = useCallback(
    async (file: File) => {
      const ext = "." + file.name.split(".").pop()?.toLowerCase();
      if (!ALLOWED_EXTENSIONS.includes(ext)) {
        setError(`Only ${ALLOWED_EXTENSIONS.join(", ")} files are supported.`);
        return;
      }
      if (file.size > MAX_SIZE_MB * 1024 * 1024) {
        setError(`File must be under ${MAX_SIZE_MB} MB.`);
        return;
      }

      setError("");
      setUploading(true);
      try {
        const form = new FormData();
        form.append("file", file);
        const res = await fetch(`/api/agents/templates/${templateId}/upload`, {
          method: "POST",
          body: form,
          credentials: "include",
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          setError(data.detail || "Upload failed.");
          return;
        }
        const data = await res.json();
        onFilesChange([...attachedFiles, data.filename]);
      } catch {
        setError("Upload failed. Check your connection and try again.");
      } finally {
        setUploading(false);
      }
    },
    [templateId, attachedFiles, onFilesChange]
  );

  const handleFiles = useCallback(
    (files: FileList | null) => {
      if (!files || files.length === 0) return;
      // Upload one at a time; loop sequentially to keep state consistent
      const fileArr = Array.from(files);
      (async () => {
        for (const f of fileArr) await uploadFile(f);
      })();
    },
    [uploadFile]
  );

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    handleFiles(e.target.files);
    if (inputRef.current) inputRef.current.value = "";
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    handleFiles(e.dataTransfer.files);
  };

  const handleRemove = async (filename: string) => {
    setError("");
    try {
      const res = await fetch(
        `/api/agents/templates/${templateId}/upload/${encodeURIComponent(filename)}`,
        { method: "DELETE", credentials: "include" }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || "Could not remove file.");
        return;
      }
      onFilesChange(attachedFiles.filter((f) => f !== filename));
    } catch {
      setError("Could not remove file. Try again.");
    }
  };

  return (
    <div className="mb-6">
      <label className="block text-sm text-slate-600 dark:text-slate-400 mb-2">Attach files</label>
      <p className="text-xs text-slate-500 mb-3">
        Files you attach here become context the agent can read when it runs. Supported: .txt, .md, .pdf, .docx (max 5 MB each).
      </p>

      {/* Drop zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        className={`border-2 border-dashed rounded-lg px-4 py-5 text-center cursor-pointer transition-colors ${
          dragOver
            ? "border-blue-500 bg-blue-500/10"
            : "border-slate-200 dark:border-slate-700 hover:border-slate-500 bg-slate-100 dark:bg-slate-800/50"
        }`}
      >
        <span className="material-icons text-2xl text-slate-500 block mb-1">
          {uploading ? "hourglass_empty" : "upload_file"}
        </span>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          {uploading ? "Uploading..." : "Click to browse or drag a file here"}
        </p>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept={ALLOWED_EXTENSIONS.join(",")}
        multiple
        className="hidden"
        onChange={handleInputChange}
        disabled={uploading}
      />

      {error && (
        <p className="text-xs text-red-600 dark:text-red-400 mt-2">{error}</p>
      )}

      {/* Attached files list */}
      {attachedFiles.length > 0 && (
        <ul className="mt-3 space-y-1">
          {attachedFiles.map((f) => (
            <li
              key={f}
              className="flex items-center justify-between bg-slate-100 dark:bg-slate-800 rounded-lg px-3 py-2 text-sm text-slate-700 dark:text-slate-300"
            >
              <span className="flex items-center gap-2 truncate">
                <span className="material-icons text-base text-slate-500">description</span>
                <span className="truncate">{f}</span>
              </span>
              <button
                onClick={(e) => { e.stopPropagation(); handleRemove(f); }}
                className="text-slate-500 hover:text-red-600 dark:hover:text-red-400 transition-colors ml-2 shrink-0"
                title="Remove file"
              >
                <span className="material-icons text-base">close</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
