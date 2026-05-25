import { useState, useEffect, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import Icon from "../components/Icon";
import { Button, Card } from "../components/ui";
import { api } from "../lib/api";

interface AgentfileFormData {
  name: string;
  description: string;
  model: string;
  tools: string[];
  instructions: string;
  tags: string[];
  source?: string;
}

const MODELS = [
  { value: "claude-sonnet-4-6", label: "Sonnet 4.6 (recommended)" },
  { value: "claude-haiku-4-5", label: "Haiku 4.5 (fast)" },
  { value: "claude-opus-4-7", label: "Opus 4.7 (powerful)" },
  { value: "auto", label: "Auto" },
];

const COMMON_TOOLS = [
  { value: "bash", label: "Run commands" },
  { value: "read", label: "Read files" },
  { value: "write", label: "Write files" },
  { value: "search", label: "Search code" },
  { value: "web", label: "Browse web" },
];

export default function AgentfileEditor() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();

  const [form, setForm] = useState<AgentfileFormData>({
    name: name ?? "",
    description: "",
    model: "auto",
    tools: [],
    instructions: "",
    tags: [],
  });
  const [rawView, setRawView] = useState(false);
  const [rawText, setRawText] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const fetchForm = useCallback(async () => {
    if (!name) return;
    try {
      const data = await api.get<AgentfileFormData>(`/agentfiles/${encodeURIComponent(name)}/form`);
      setForm(data);
    } catch {
      setError("Could not load this agent setup.");
    } finally {
      setLoading(false);
    }
  }, [name]);

  const fetchRaw = useCallback(async () => {
    if (!name) return;
    try {
      const data = await api.get<{ raw: string }>(`/agentfiles/${encodeURIComponent(name)}`);
      setRawText(data.raw ?? "");
    } catch {
      // not critical
    }
  }, [name]);

  useEffect(() => {
    fetchForm();
    fetchRaw();
  }, [fetchForm, fetchRaw]);

  const handleToolToggle = (tool: string) => {
    setForm((f) => ({
      ...f,
      tools: f.tools.includes(tool)
        ? f.tools.filter((t) => t !== tool)
        : [...f.tools, tool],
    }));
  };

  const handleSave = async () => {
    if (!name) return;
    setSaving(true);
    setError(null);
    try {
      await api.put(`/agentfiles/${encodeURIComponent(name)}/form`, form);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
      await fetchRaw();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Save failed";
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48 text-slate-600 dark:text-slate-400 text-sm">
        Loading...
      </div>
    );
  }

  const isBuiltin = form.source === "builtin";

  return (
    <div className="max-w-2xl mx-auto px-4 py-8">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <button
          onClick={() => navigate("/agents")}
          className="text-slate-600 dark:text-slate-400 hover:text-white transition-colors"
          aria-label="Back to agents"
        >
          <Icon name="arrow_back" className="text-xl" />
        </button>
        <div>
          <h1 className="text-white font-semibold text-lg">Edit agent setup</h1>
          <p className="text-slate-500 text-xs">{name}</p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setRawView(!rawView)}
            className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-400 hover:text-white px-2.5 py-1.5 rounded-lg border border-slate-200 dark:border-slate-700 hover:border-slate-600 transition-colors"
            aria-label={rawView ? "Show form" : "View raw file"}
          >
            <Icon name={rawView ? "edit" : "code"} className="text-sm" />
            {rawView ? "Form" : "View raw"}
          </button>
        </div>
      </div>

      {isBuiltin && (
        <div className="mb-4 flex items-start gap-2 bg-amber-500/10 border border-amber-500/30 rounded-lg px-4 py-3 text-amber-600 dark:text-amber-400 text-sm">
          <Icon name="lock" className="text-base mt-0.5 shrink-0" />
          <span>This is a built-in setup and cannot be edited.</span>
        </div>
      )}

      {error && (
        <div className="mb-4 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-red-600 dark:text-red-400 text-sm">
          {error}
        </div>
      )}

      {rawView ? (
        <Card variant="default" padding="md">
          <p className="text-xs text-slate-500 uppercase tracking-wider mb-3">Raw file</p>
          <pre className="text-xs text-slate-700 dark:text-slate-300 font-mono whitespace-pre-wrap leading-relaxed">
            {rawText || "(empty)"}
          </pre>
        </Card>
      ) : (
        <div className="space-y-5">
          {/* Name */}
          <div>
            <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1.5">Name</label>
            <input
              type="text"
              value={form.name}
              disabled
              className="w-full bg-slate-100 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-slate-600 dark:text-slate-400 text-sm cursor-not-allowed"
            />
            <p className="text-xs text-slate-600 mt-1">Name is set by the file and cannot be changed here.</p>
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1.5" htmlFor="af-description">
              Description
            </label>
            <input
              id="af-description"
              type="text"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              disabled={isBuiltin}
              placeholder="What does this agent do?"
              className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
            />
          </div>

          {/* Model */}
          <div>
            <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1.5" htmlFor="af-model">
              Model
            </label>
            <select
              id="af-model"
              value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
              disabled={isBuiltin}
              className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {MODELS.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </div>

          {/* Tools */}
          <div>
            <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1.5">Tools</label>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {COMMON_TOOLS.map((tool) => {
                const checked = form.tools.includes(tool.value);
                return (
                  <label
                    key={tool.value}
                    className={`flex items-center gap-2 px-3 py-2 rounded-lg border cursor-pointer transition-colors ${
                      isBuiltin ? "opacity-50 cursor-not-allowed" : ""
                    } ${
                      checked
                        ? "bg-blue-600/20 border-blue-500/50 text-blue-700 dark:text-blue-300"
                        : "bg-slate-100 dark:bg-slate-800/50 border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:border-slate-600"
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => !isBuiltin && handleToolToggle(tool.value)}
                      disabled={isBuiltin}
                      className="sr-only"
                      aria-label={tool.label}
                    />
                    <Icon
                      name={checked ? "check_box" : "check_box_outline_blank"}
                      className="text-base shrink-0"
                    />
                    <span className="text-xs">{tool.label}</span>
                  </label>
                );
              })}
            </div>
            {/* Any tools not in the common list */}
            {form.tools.filter((t) => !COMMON_TOOLS.find((c) => c.value === t)).length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {form.tools
                  .filter((t) => !COMMON_TOOLS.find((c) => c.value === t))
                  .map((t) => (
                    <span key={t} className="text-[11px] bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 px-2 py-0.5 rounded">
                      {t}
                    </span>
                  ))}
              </div>
            )}
          </div>

          {/* Instructions */}
          <div>
            <label className="block text-sm text-slate-600 dark:text-slate-400 mb-1.5" htmlFor="af-instructions">
              What this agent does
            </label>
            <textarea
              id="af-instructions"
              value={form.instructions}
              onChange={(e) => setForm({ ...form, instructions: e.target.value })}
              disabled={isBuiltin}
              rows={6}
              placeholder="Describe what this agent should do when launched..."
              className="w-full bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 disabled:opacity-50 disabled:cursor-not-allowed resize-y"
            />
          </div>

          {/* Save */}
          {!isBuiltin && (
            <div className="flex items-center gap-3 pt-2">
              <Button
                variant="primary"
                size="md"
                onClick={handleSave}
                disabled={saving}
                loading={saving}
              >
                {saving ? "Saving..." : "Save changes"}
              </Button>
              {saved && (
                <span className="text-sm text-green-600 dark:text-green-400 flex items-center gap-1">
                  <Icon name="check_circle" className="text-base" />
                  Saved
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
