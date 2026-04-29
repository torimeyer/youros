import { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import TopBar from "../components/TopBar";
import { api } from "../lib/api";

interface ParsedPreview {
  title: string;
  description: string;
  taskCount: number;
  acCount: number;
}

function parsePreview(yaml: string): ParsedPreview | null {
  if (!yaml.trim()) return null;
  try {
    const lines = yaml.split("\n");
    let title = "";
    let description = "";
    let taskCount = 0;
    let acCount = 0;
    let inTasks = false;
    let inAC = false;
    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith("name:")) {
        title = trimmed.replace(/^name:\s*/, "").replace(/^["']|["']$/g, "");
      } else if (trimmed.startsWith("description:") && !inTasks) {
        description = trimmed.replace(/^description:\s*/, "").replace(/^["']|["']$/g, "");
      } else if (trimmed === "tasks:") {
        inTasks = true;
      } else if (inTasks && trimmed.startsWith("- title:")) {
        taskCount++;
        inAC = false;
      } else if (inTasks && trimmed === "acceptance_criteria:") {
        inAC = true;
      } else if (inAC && trimmed.startsWith("- ")) {
        acCount++;
      }
    }
    return { title, description, taskCount, acCount };
  } catch {
    return null;
  }
}

export default function SpecImport() {
  const navigate = useNavigate();
  const [yaml, setYaml] = useState("");
  const [preview, setPreview] = useState<ParsedPreview | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  function handleYamlChange(value: string) {
    setYaml(value);
    setImportError(null);
    if (!value.trim()) {
      setPreview(null);
      setParseError(null);
      return;
    }
    const p = parsePreview(value);
    if (p && p.title) {
      setPreview(p);
      setParseError(null);
    } else if (value.trim()) {
      setPreview(null);
      setParseError("Paste a valid spec-kit YAML with a 'name' field to see a preview.");
    }
  }

  function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target?.result as string;
      handleYamlChange(text);
    };
    reader.readAsText(file);
  }

  async function handleImport() {
    if (!yaml.trim()) return;
    setImporting(true);
    setImportError(null);
    try {
      await api.post("/api/specs/import", {
        yaml,
        format: "speckit",
      });
      navigate("/specs");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Import failed";
      setImportError(msg);
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="flex flex-col h-full">
      <TopBar title="Import spec" />
      <div className="flex-1 overflow-y-auto p-6 max-w-2xl mx-auto w-full">
        <div className="mb-6">
          <h2 className="text-lg font-semibold mb-1">Import from spec-kit YAML</h2>
          <p className="text-sm text-gray-500">
            Paste or upload a spec-kit YAML file. We'll create a spec you can build from.
          </p>
        </div>

        <div className="mb-4">
          <div className="flex items-center justify-between mb-2">
            <label className="text-sm font-medium" htmlFor="yaml-input">
              Spec-kit YAML
            </label>
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="text-xs text-blue-500 hover:underline"
              data-testid="upload-button"
            >
              Upload file
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".yaml,.yml"
              className="hidden"
              onChange={handleFileUpload}
              data-testid="file-input"
            />
          </div>
          <textarea
            id="yaml-input"
            data-testid="yaml-textarea"
            className="w-full h-48 font-mono text-sm border border-gray-200 rounded-lg p-3 resize-none focus:outline-none focus:ring-2 focus:ring-blue-400"
            placeholder={`name: "My feature"\ndescription: "What it does"\ntasks:\n  - title: "Task 1"\n    priority: P1\n    acceptance_criteria:\n      - "Criterion 1"`}
            value={yaml}
            onChange={(e) => handleYamlChange(e.target.value)}
          />
        </div>

        {parseError && (
          <div
            className="text-sm text-red-500 mb-4"
            data-testid="parse-error"
          >
            {parseError}
          </div>
        )}

        {preview && (
          <div
            className="border border-gray-200 rounded-lg p-4 mb-4 bg-gray-50"
            data-testid="preview-panel"
          >
            <div className="text-sm font-semibold mb-2">Preview</div>
            <div className="text-base font-medium mb-1" data-testid="preview-title">
              {preview.title}
            </div>
            {preview.description && (
              <div className="text-sm text-gray-600 mb-2" data-testid="preview-description">
                {preview.description}
              </div>
            )}
            <div className="flex gap-4 text-sm text-gray-500">
              <span data-testid="preview-task-count">{preview.taskCount} task{preview.taskCount !== 1 ? "s" : ""}</span>
              <span data-testid="preview-ac-count">{preview.acCount} acceptance {preview.acCount !== 1 ? "criteria" : "criterion"}</span>
            </div>
          </div>
        )}

        {importError && (
          <div
            className="text-sm text-red-500 mb-4"
            data-testid="import-error"
          >
            {importError}
          </div>
        )}

        <div className="flex gap-3">
          <button
            type="button"
            data-testid="import-button"
            onClick={handleImport}
            disabled={!yaml.trim() || importing}
            className="px-4 py-2 bg-blue-500 text-white rounded-lg text-sm font-medium disabled:opacity-50 hover:bg-blue-600 transition-colors"
          >
            {importing ? "Importing..." : "Import"}
          </button>
          <button
            type="button"
            onClick={() => navigate("/specs")}
            className="px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
