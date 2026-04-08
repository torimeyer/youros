import { useState, useEffect } from "react";
import Icon from "./Icon";
import { api } from "../lib/api";

interface Template {
  id: string;
  name: string;
  description: string;
  icon: string;
  prompt_template: string;
  breakdown_hint: string;
  builtin: boolean;
}

interface Props {
  onClose: () => void;
}

const EMPTY_FORM = {
  name: "",
  description: "",
  icon: "lightbulb",
  prompt_template: "",
  breakdown_hint: "",
};

export default function TemplateManager({ onClose }: Props) {
  const [builtins, setBuiltins] = useState<Template[]>([]);
  const [custom, setCustom] = useState<Template[]>([]);
  const [editing, setEditing] = useState<Template | null>(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const fetchTemplates = async () => {
    try {
      const all = await api.get<{ templates: Template[] }>("/ideas/templates");
      const allTemplates = all.templates || [];
      setBuiltins(allTemplates.filter((t) => t.builtin));
      setCustom(allTemplates.filter((t) => !t.builtin));
    } catch {
      // leave as-is
    }
  };

  useEffect(() => {
    fetchTemplates();
  }, []);

  const handleAdd = () => {
    setAdding(true);
    setEditing(null);
    setForm(EMPTY_FORM);
    setError("");
  };

  const handleEdit = (tpl: Template) => {
    setEditing(tpl);
    setAdding(false);
    setForm({
      name: tpl.name,
      description: tpl.description,
      icon: tpl.icon,
      prompt_template: tpl.prompt_template,
      breakdown_hint: tpl.breakdown_hint,
    });
    setError("");
  };

  const handleDelete = async (tpl: Template) => {
    if (!confirm(`Remove the template "${tpl.name}"?`)) return;
    try {
      await api.delete(`/admin/templates/${encodeURIComponent(tpl.id)}`);
      await fetchTemplates();
    } catch {
      setError("Could not delete that template. Try again.");
    }
  };

  const handleSave = async () => {
    if (!form.name.trim() || !form.prompt_template.trim()) {
      setError("Name and prompt template are required.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      if (editing) {
        await api.put(`/admin/templates/${encodeURIComponent(editing.id)}`, form);
      } else {
        await api.post("/admin/templates", form);
      }
      setEditing(null);
      setAdding(false);
      setForm(EMPTY_FORM);
      await fetchTemplates();
    } catch {
      setError("Could not save the template. Try again.");
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    setEditing(null);
    setAdding(false);
    setForm(EMPTY_FORM);
    setError("");
  };

  const showForm = adding || editing !== null;

  return (
    <div
      role="dialog"
      aria-label="Manage idea templates"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
    >
      <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 max-w-2xl w-full shadow-2xl max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-2">
            <Icon name="dashboard_customize" className="text-pink-400 text-xl" />
            <span className="text-white font-semibold text-lg">Manage templates</span>
          </div>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-white transition-colors"
            aria-label="Close"
          >
            <Icon name="close" className="text-lg" />
          </button>
        </div>

        {/* Built-in templates (read-only) */}
        <div className="mb-6">
          <h3 className="text-slate-400 text-xs font-semibold uppercase tracking-wide mb-3">
            Built-in templates
          </h3>
          <div className="space-y-2">
            {builtins.map((tpl) => (
              <div
                key={tpl.id}
                className="flex items-center gap-3 bg-slate-800/50 border border-slate-700 rounded-lg px-4 py-3"
              >
                <Icon name={tpl.icon} className="text-slate-400 text-base flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-white text-sm font-medium">{tpl.name}</div>
                  <div className="text-slate-500 text-xs truncate">{tpl.description}</div>
                </div>
                <span className="text-slate-600 text-xs">Built-in</span>
              </div>
            ))}
          </div>
        </div>

        {/* Custom templates */}
        <div className="mb-6">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-slate-400 text-xs font-semibold uppercase tracking-wide">
              Your templates
            </h3>
            {!showForm && (
              <button
                onClick={handleAdd}
                className="flex items-center gap-1 text-xs text-pink-400 hover:text-pink-300 transition-colors"
              >
                <Icon name="add" className="text-sm" />
                Add template
              </button>
            )}
          </div>

          {custom.length === 0 && !showForm && (
            <p className="text-slate-500 text-sm">
              No custom templates yet. Add one to speed up idea capture.
            </p>
          )}

          <div className="space-y-2">
            {custom.map((tpl) => (
              <div
                key={tpl.id}
                className="flex items-center gap-3 bg-slate-800/50 border border-slate-700 rounded-lg px-4 py-3"
              >
                <Icon name={tpl.icon} className="text-pink-400 text-base flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-white text-sm font-medium">{tpl.name}</div>
                  <div className="text-slate-500 text-xs truncate">{tpl.description}</div>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => handleEdit(tpl)}
                    className="text-slate-500 hover:text-white transition-colors text-xs"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => handleDelete(tpl)}
                    className="text-slate-500 hover:text-red-400 transition-colors text-xs"
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Add / Edit form */}
        {showForm && (
          <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4">
            <h4 className="text-white font-medium mb-4">
              {editing ? "Edit template" : "New template"}
            </h4>
            <div className="space-y-3">
              <div>
                <label className="text-slate-400 text-xs mb-1 block">Name</label>
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="e.g. Bug report"
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-pink-500"
                />
              </div>
              <div>
                <label className="text-slate-400 text-xs mb-1 block">Description</label>
                <input
                  type="text"
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  placeholder="Short description shown on hover"
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-pink-500"
                />
              </div>
              <div>
                <label className="text-slate-400 text-xs mb-1 block">
                  Prompt template
                  <span className="text-slate-500 ml-1">(use [brackets] for fill-in spots)</span>
                </label>
                <input
                  type="text"
                  value={form.prompt_template}
                  onChange={(e) => setForm({ ...form, prompt_template: e.target.value })}
                  placeholder="e.g. The bug is [what] and it happens when [situation]"
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-pink-500"
                />
              </div>
              <div>
                <label className="text-slate-400 text-xs mb-1 block">
                  Hint for the AI
                  <span className="text-slate-500 ml-1">(optional, helps generate better tasks)</span>
                </label>
                <input
                  type="text"
                  value={form.breakdown_hint}
                  onChange={(e) => setForm({ ...form, breakdown_hint: e.target.value })}
                  placeholder="e.g. This is a bug report. Include steps to reproduce and a fix task."
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-pink-500"
                />
              </div>
              <div>
                <label className="text-slate-400 text-xs mb-1 block">
                  Icon name
                  <span className="text-slate-500 ml-1">(Material icon name)</span>
                </label>
                <input
                  type="text"
                  value={form.icon}
                  onChange={(e) => setForm({ ...form, icon: e.target.value })}
                  placeholder="e.g. bug_report"
                  className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-pink-500"
                />
              </div>

              {error && <p className="text-red-400 text-sm">{error}</p>}

              <div className="flex gap-2 pt-2">
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="bg-pink-500 hover:bg-pink-600 disabled:bg-pink-500/40 disabled:cursor-not-allowed text-white rounded-lg px-4 py-2 text-sm transition-colors"
                >
                  {saving ? "Saving..." : editing ? "Save changes" : "Add template"}
                </button>
                <button
                  onClick={handleCancel}
                  className="px-4 py-2 text-slate-400 hover:text-white text-sm rounded-lg transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
