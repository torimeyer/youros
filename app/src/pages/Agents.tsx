import { useState, useEffect, useRef, useCallback } from "react";
import TopBar from "../components/TopBar";
import Icon from "../components/Icon";
import TemplateCard from "../components/TemplateCard";
import { AgentChatThread } from "../components/AgentChatThread";
import ConfirmModal from "../components/ConfirmModal";
import { useConfirm } from "../hooks/useConfirm";
import { api, ApiError, ApiTimeoutError } from "../lib/api";
import { onAgentsChange } from "../lib/sidebarBus";
import { useNotificationStore } from "../stores/notifications";
import { useAppStore, type CustomAgentTemplate } from "../stores/app";
import { AGENT_MARKETPLACE } from "../data/agentMarketplace";
import { type AgentInfo, agentTitleParts, isAgentActive, isUserSpawnedAgent } from "../lib/agentUtils";
import { renderMarkdown } from "../lib/markdown";
import { hasSpeakerPrefixes, parseTranscript } from "../lib/transcript";
import { Button, EmptyState, Card } from "../components/ui";

// Re-export so tests can still import these from './Agents'
export { friendlyAgentName, isMainSession, isUserSpawnedAgent } from "../lib/agentUtils";

const BASE_TABS = ["Active", "Recent", "Templates"];

// Every backend status that means "this agent is no longer running".
// Keep this in sync with ``_TERMINAL_FROM_META`` in api/routers/agents.py.
// The Recent tab and the Active-to-Recent transition both read from this
// list, so missing entries make finished agents disappear from both tabs
// (the "flash and vanish" bug).
export const TERMINAL_AGENT_STATUSES = [
  "completed",
  "completed_timeout",
  "cancelled",
  "failed",
  "terminated_stale",
  "stopped",
  "abandoned",
  "killed",
] as const;

// Statuses the "Hiding cancelled" chip hides by default. ``failed`` and
// ``completed`` are intentionally NOT in this list: a genuine failure or
// success should always be visible in Recent even when the user has the
// chip set to hide cancellations. Keeping the other cancellation-synonyms
// in place preserves existing behavior where a cancel-all flood does not
// drown out the completed rows the user actually cares about.
export const CANCELLED_SYNONYM_STATUSES = [
  "cancelled",
  "terminated_stale",
  "killed",
  "abandoned",
  "stopped",
] as const;

/* ---------- Icon Picker ---------- */
const ICON_PICKER_ICONS = [
  // General
  "smart_toy", "psychology", "search", "edit", "code", "bug_report",
  "rocket_launch", "summarize", "today", "mail",
  // Business
  "business", "campaign", "article", "assignment_ind", "analytics",
  "trending_up",
  // Creative
  "edit_note", "share", "title", "spellcheck", "label", "brush", "palette",
  // Communication
  "question_answer", "support_agent", "forward_to_inbox", "outgoing_mail",
  "record_voice_over",
  // Education
  "school", "menu_book", "quiz", "format_quote", "format_list_numbered",
  // Home / Lifestyle
  "restaurant", "shopping_cart", "flight_takeoff", "redeem", "home",
  // Tech
  "storage", "security", "speed", "engineering", "architecture", "api", "build",
  // Status
  "check_circle", "warning", "info", "help_outline",
];

function IconPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (icon: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const filtered = search.trim()
    ? ICON_PICKER_ICONS.filter((ic) =>
        ic.toLowerCase().includes(search.trim().toLowerCase()),
      )
    : ICON_PICKER_ICONS;

  return (
    <div ref={containerRef} className="relative mb-4">
      <label className="block text-sm text-slate-400 mb-1">Icon</label>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm hover:border-blue-500 transition-colors w-full text-left"
        data-testid="icon-picker-trigger"
      >
        <Icon name={value} size={20} className="text-blue-400" />
        <span className="flex-1">{value}</span>
        <Icon name={open ? "expand_less" : "expand_more"} size={18} className="text-slate-500" />
      </button>

      {open && (
        <div className="absolute z-50 mt-1 left-0 right-0 bg-slate-800 border border-slate-700 rounded-xl shadow-xl p-3 max-h-80 overflow-y-auto" data-testid="icon-picker-dropdown">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search icons..."
            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 mb-2"
            data-testid="icon-picker-search"
            autoFocus
          />
          {filtered.length === 0 ? (
            <p className="text-xs text-slate-500 text-center py-2">No matching icons</p>
          ) : (
            <div className="grid grid-cols-8 gap-1" data-testid="icon-picker-grid">
              {filtered.map((ic) => (
                <button
                  key={ic}
                  type="button"
                  onClick={() => {
                    onChange(ic);
                    setOpen(false);
                    setSearch("");
                  }}
                  title={ic}
                  className={`flex items-center justify-center p-1.5 rounded-lg transition-colors ${
                    ic === value
                      ? "bg-blue-500/20 text-blue-400 ring-1 ring-blue-500"
                      : "text-slate-400 hover:bg-slate-700 hover:text-white"
                  }`}
                  data-testid={`icon-option-${ic}`}
                >
                  <Icon name={ic} size={22} />
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ---------- Agentfiles Tab ---------- */

interface AgentfileInfo {
  name: string;
  path: string;
  model: string;
  prompt: string;
  tools: string[];
  token_limit: number;
  boot: string;
  pin: string;
  description: string;
  source: "builtin" | "user";
  raw?: string;
}

const AGENTFILE_ICONS: Record<string, string> = {
  saa: "engineering",
  diagnose: "bug_report",
  review: "rate_review",
  test: "science",
  research: "search",
};

function AgentfilesTab({ onLaunch }: { onLaunch: () => void }) {
  const [agentfiles, setAgentfiles] = useState<AgentfileInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [launching, setLaunching] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({
    name: "",
    prompt: "",
    model: "auto",
    description: "",
  });
  const [creating, setCreating] = useState(false);

  const fetchAgentfiles = useCallback(async () => {
    try {
      const data = await api.get<{ agentfiles: AgentfileInfo[] }>("/agentfiles");
      setAgentfiles(data.agentfiles ?? []);
    } catch {
      // keep empty
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAgentfiles();
  }, [fetchAgentfiles]);

  const handleLaunch = async (name: string) => {
    setLaunching(name);
    try {
      await api.post(`/agentfiles/${encodeURIComponent(name)}/run`, {});
      onLaunch();
    } catch (e) {
      console.error("Failed to launch Agentfile:", e);
    } finally {
      setLaunching(null);
    }
  };

  const handleCreate = async () => {
    if (!createForm.name.trim() || !createForm.prompt.trim()) return;
    setCreating(true);
    try {
      await api.post("/agentfiles", {
        name: createForm.name,
        prompt: createForm.prompt,
        model: createForm.model,
        tools: ["shell", "file:read", "file:write"],
        token_limit: 200000,
        description: createForm.description,
      });
      setShowCreate(false);
      setCreateForm({ name: "", prompt: "", model: "auto", description: "" });
      await fetchAgentfiles();
    } catch (e) {
      console.error("Failed to create Agentfile:", e);
    } finally {
      setCreating(false);
    }
  };

  if (loading) return <p className="text-sm text-slate-500">Loading agent setups...</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h3 className="text-white font-semibold">Agent Setups</h3>
          <p className="text-xs text-slate-500 mt-1">Ready-made agent setups you can start with one click</p>
        </div>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="flex items-center gap-2 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition-colors"
        >
          <Icon name={showCreate ? "close" : "add"} className="text-base" />
          {showCreate ? "Cancel" : "New Setup"}
        </button>
      </div>

      {/* Create form */}
      {showCreate && (
        <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-5 mb-6">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-sm text-slate-400 mb-1">Name</label>
              <input
                type="text"
                value={createForm.name}
                onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
                placeholder="e.g. deploy-checker"
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500"
              />
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-1">Model</label>
              <select
                value={createForm.model}
                onChange={(e) => setCreateForm({ ...createForm, model: e.target.value })}
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500"
              >
                <option value="auto">Auto</option>
                <option value="opus">Opus</option>
                <option value="sonnet">Sonnet</option>
                <option value="haiku">Haiku</option>
              </select>
            </div>
          </div>
          <div className="mb-4">
            <label className="block text-sm text-slate-400 mb-1">Description</label>
            <input
              type="text"
              value={createForm.description}
              onChange={(e) => setCreateForm({ ...createForm, description: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
              placeholder="What does this agent do?"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500"
            />
          </div>
          <div className="mb-4">
            <label className="block text-sm text-slate-400 mb-1">Instructions</label>
            <textarea
              value={createForm.prompt}
              onChange={(e) => setCreateForm({ ...createForm, prompt: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleCreate(); } }}
              rows={3}
              placeholder="What should this agent do? (Enter to create, Shift+Enter for newline)"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 resize-none"
            />
          </div>
          <button
            onClick={handleCreate}
            disabled={creating || !createForm.name.trim() || !createForm.prompt.trim()}
            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg px-4 py-2 text-sm font-medium transition-colors"
          >
            {creating ? "Creating..." : "Create Setup"}
          </button>
        </div>
      )}

      {/* Agentfile cards */}
      {agentfiles.length === 0 ? (
        <EmptyState
          icon="description"
          title="No agent setups yet"
          description="Create one above to get started."
        />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {agentfiles.map((af) => {
            const isExpanded = expanded === af.name;
            const icon = AGENTFILE_ICONS[af.name] || "smart_toy";
            return (
              <Card key={af.name} variant="default" padding="sm">
                <div
                  className="flex items-start gap-3 cursor-pointer"
                  onClick={() => setExpanded(isExpanded ? null : af.name)}
                >
                  <Icon name={icon} className="text-2xl text-blue-400 mt-0.5 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-white text-sm font-medium">{af.name}</p>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                        af.source === "builtin"
                          ? "bg-blue-500/20 text-blue-400"
                          : "bg-green-500/20 text-green-400"
                      }`}>
                        {af.source === "builtin" ? "built-in" : "custom"}
                      </span>
                    </div>
                    <p className="text-slate-400 text-xs mt-0.5 line-clamp-3">{af.description || "No description yet"}</p>
                    <div className="flex gap-3 mt-1.5">
                      <span className="text-[10px] text-slate-500">{af.model}</span>
                      <span className="text-[10px] text-slate-500">{af.tools.length} tools</span>
                      {af.token_limit > 0 && (
                        <span className="text-[10px] text-slate-500">{(af.token_limit / 1000).toFixed(0)}k limit</span>
                      )}
                    </div>
                  </div>
                  <Icon name={isExpanded ? "expand_less" : "expand_more"} className="text-slate-500 shrink-0" size={20} />
                </div>

                {isExpanded && (
                  <div className="mt-3 pt-3 border-t border-slate-700/50">
                    <div className="mb-3">
                      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Instructions</p>
                      <p className="text-xs text-slate-300 leading-relaxed">{af.prompt || "No instructions yet"}</p>
                    </div>
                    {af.tools.length > 0 && (
                      <div className="mb-3">
                        <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Tools</p>
                        <div className="flex flex-wrap gap-1.5">
                          {af.tools.map((tool) => (
                            <span key={tool} className="text-[10px] bg-slate-800 text-slate-300 px-2 py-0.5 rounded">
                              {tool}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    <Button
                      variant="primary"
                      size="md"
                      fullWidth
                      onClick={() => handleLaunch(af.name)}
                      disabled={launching === af.name}
                      loading={launching === af.name}
                      className="mt-2"
                    >
                      {launching === af.name ? "Launching..." : "Launch Agent"}
                    </Button>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

const POWER_USER_TABS = ["Delegate", "Workspace"];

type CustomTemplate = CustomAgentTemplate;

/* ---------- Template Editor Modal ---------- */
// prompt_template is the system prompt shown read-only in detail view.
// userMessage is what the user types before spawning.
function TemplateEditorModal({
  initial,
  isNew,
  promptTemplate,
  templateId,
  source,
  aliases,
  capabilities,
  onSpawn,
  onSave,
  onCancel,
  onDescriptionSaved,
}: {
  initial: CustomTemplate | null;
  isNew: boolean;
  promptTemplate?: string;
  templateId?: string;
  source?: string;
  aliases?: string[];
  capabilities?: { writes_to: string; cannot_touch: string; budget: string; time_limit: string; sandbox: string } | null;
  onSpawn: (t: CustomTemplate, userMessage: string) => void;
  onSave: (t: CustomTemplate) => void;
  onCancel: () => void;
  onDescriptionSaved?: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [icon, setIcon] = useState(initial?.icon ?? "smart_toy");
  const [model, setModel] = useState(initial?.model ?? "sonnet");
  const [budget, setBudget] = useState(initial?.budget ?? 2.0);
  // The initial user message typed before spawning
  const [userMessage, setUserMessage] = useState("");

  // Alias editing state
  const [aliasValue, setAliasValue] = useState("");
  const [aliasLoading, setAliasLoading] = useState(false);
  const [aliasError, setAliasError] = useState("");
  const [aliasSaved, setAliasSaved] = useState(false);
  const [promptExpanded, setPromptExpanded] = useState(false);

  // Description editing state (detail view only). ``descriptionBaseline``
  // is the last saved value so Save only lights up when the user has
  // actually typed something new.
  const [descriptionLoading, setDescriptionLoading] = useState(false);
  const [descriptionError, setDescriptionError] = useState("");
  const [descriptionSaved, setDescriptionSaved] = useState(false);
  const [descriptionBaseline, setDescriptionBaseline] = useState(initial?.description ?? "");
  const descriptionDirty = description.trim() !== descriptionBaseline.trim();

  // Fetch existing alias when opening detail view
  useEffect(() => {
    if (!isNew && templateId) {
      api.get<{ alias: string | null }>(`/agents/templates/${templateId}/alias`)
        .then((data) => {
          if (data.alias) setAliasValue(data.alias);
        })
        .catch(() => { /* non-fatal */ });
    }
  }, [isNew, templateId]);

  const handleSaveAlias = async () => {
    if (!templateId) return;
    setAliasLoading(true);
    setAliasError("");
    setAliasSaved(false);
    try {
      await api.patch(`/agents/templates/${templateId}/alias`, {
        alias: aliasValue.trim() || null,
      });
      setAliasSaved(true);
      setTimeout(() => setAliasSaved(false), 2000);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : (err instanceof Error ? err.message : String(err));
      setAliasError(msg);
    } finally {
      setAliasLoading(false);
    }
  };

  const handleClearAlias = async () => {
    if (!templateId) return;
    setAliasLoading(true);
    setAliasError("");
    try {
      await api.patch(`/agents/templates/${templateId}/alias`, { alias: null });
      setAliasValue("");
      setAliasSaved(true);
      setTimeout(() => setAliasSaved(false), 2000);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : (err instanceof Error ? err.message : String(err));
      setAliasError(msg);
    } finally {
      setAliasLoading(false);
    }
  };

  const handleSaveDescription = async () => {
    if (!templateId) return;
    const trimmed = description.trim();
    if (!trimmed) {
      setDescriptionError("Description cannot be empty.");
      return;
    }
    setDescriptionLoading(true);
    setDescriptionError("");
    setDescriptionSaved(false);
    try {
      const res = await api.patch<{ template: { description?: string } }>(
        `/agents/templates/${templateId}/description`,
        { description: trimmed },
      );
      const saved = res?.template?.description ?? trimmed;
      setDescription(saved);
      setDescriptionBaseline(saved);
      setDescriptionSaved(true);
      setTimeout(() => setDescriptionSaved(false), 2000);
      // Notify the parent so it can refresh the template list so the card
      // under the modal reflects the new description the next time it paints.
      if (onDescriptionSaved) onDescriptionSaved();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : (err instanceof Error ? err.message : String(err));
      setDescriptionError(msg);
    } finally {
      setDescriptionLoading(false);
    }
  };

  const current: CustomTemplate = { name, description, icon, model, budget };

  // Detail view (existing template): show prompt + user message input
  if (!isNew && promptTemplate) {
    const promptPreview = promptTemplate.length > 500 && !promptExpanded
      ? promptTemplate.slice(0, 500) + "..."
      : promptTemplate;
    const sourceBadge = source === "builtin"
      ? { label: "built-in", cls: "bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-blue-400" }
      : source === "custom"
        ? { label: "custom", cls: "bg-purple-100 text-purple-700 dark:bg-purple-500/20 dark:text-purple-400" }
        : source === "marketplace"
          ? { label: "marketplace", cls: "bg-slate-200 text-slate-700 dark:bg-slate-700/60 dark:text-slate-400" }
          : null;

    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
        <div
          className="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-lg shadow-xl overflow-hidden max-h-[90vh] flex flex-col"
          data-testid="template-detail-modal"
        >
          {/* Header */}
          <div className="flex items-center gap-3 p-6 border-b border-slate-800 shrink-0">
            <Icon name={icon} className="text-blue-400 text-2xl" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="text-lg font-semibold text-white">{name}</h3>
                {sourceBadge && (
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${sourceBadge.cls}`}>
                    {sourceBadge.label}
                  </span>
                )}
                {(aliases || []).map((a) => (
                  <span key={a} className="text-[10px] px-1.5 py-0.5 rounded bg-slate-700 text-slate-400 font-mono">
                    alias: {a}
                  </span>
                ))}
              </div>
              <p className="text-xs text-slate-400 mt-0.5 line-clamp-2" data-testid="template-detail-description-preview">
                {description}
              </p>
            </div>
            <button
              onClick={onCancel}
              className="ml-auto text-slate-500 hover:text-white transition-colors shrink-0"
              aria-label="Close"
            >
              <Icon name="close" size={20} />
            </button>
          </div>

          <div className="p-6 space-y-5 overflow-y-auto flex-1">
            {/* Description section (editable) */}
            {templateId && (
              <div data-testid="template-description-section">
                <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
                  Description
                </p>
                <p className="text-[10px] text-slate-500 mb-2">
                  Edit the short summary that shows on the template card.
                </p>
                <textarea
                  value={description}
                  onChange={(e) => { setDescription(e.target.value); setDescriptionError(""); }}
                  rows={3}
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 resize-y"
                  data-testid="template-description-input"
                  maxLength={2000}
                  placeholder="Short summary shown on the template card"
                />
                <div className="flex items-center justify-end gap-2 mt-1">
                  {descriptionError && (
                    <p className="text-xs text-red-400 mr-auto" data-testid="template-description-error">{descriptionError}</p>
                  )}
                  {descriptionSaved && (
                    <p className="text-xs text-green-400 mr-auto" data-testid="template-description-saved">Description saved.</p>
                  )}
                  <button
                    onClick={handleSaveDescription}
                    disabled={descriptionLoading || !descriptionDirty || !description.trim()}
                    className="bg-blue-500 hover:bg-blue-600 disabled:bg-slate-700 disabled:text-slate-500 text-white rounded-lg px-3 py-1.5 text-xs transition-colors"
                    data-testid="template-description-save"
                  >
                    {descriptionLoading ? "Saving..." : "Save description"}
                  </button>
                </div>
              </div>
            )}

            {/* Prompt section */}
            <div data-testid="template-prompt-section">
              <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Prompt</p>
              <div className="bg-slate-950 border border-slate-800 rounded-lg px-4 py-3 text-xs text-slate-300 leading-relaxed max-h-48 overflow-y-auto whitespace-pre-wrap">
                {promptPreview}
              </div>
              {promptTemplate.length > 500 && (
                <button
                  onClick={() => setPromptExpanded(!promptExpanded)}
                  className="text-[10px] text-blue-400 hover:text-blue-300 mt-1 transition-colors"
                  data-testid="template-prompt-expand"
                >
                  {promptExpanded ? "Show less" : "Show full prompt"}
                </button>
              )}
            </div>

            {/* Capabilities section */}
            {capabilities && (
              <div data-testid="template-capabilities-section">
                <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Capabilities</p>
                <div className="grid grid-cols-2 gap-2 text-xs text-slate-300">
                  <div className="bg-slate-800/60 rounded-lg px-3 py-2">
                    <span className="text-slate-500 block text-[10px]">Writes to</span>
                    {capabilities.writes_to}
                  </div>
                  <div className="bg-slate-800/60 rounded-lg px-3 py-2">
                    <span className="text-slate-500 block text-[10px]">Budget</span>
                    {capabilities.budget}
                  </div>
                  <div className="bg-slate-800/60 rounded-lg px-3 py-2">
                    <span className="text-slate-500 block text-[10px]">Time limit</span>
                    {capabilities.time_limit}
                  </div>
                  <div className="bg-slate-800/60 rounded-lg px-3 py-2">
                    <span className="text-slate-500 block text-[10px]">Sandbox</span>
                    {capabilities.sandbox}
                  </div>
                </div>
              </div>
            )}

            {/* Alias section */}
            {templateId && (
              <div data-testid="template-alias-section">
                <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
                  Custom alias
                </p>
                <p className="text-[10px] text-slate-500 mb-2">
                  Set a shortcut name you can type in chat or use when spawning agents.
                </p>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={aliasValue}
                    onChange={(e) => { setAliasValue(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "")); setAliasError(""); }}
                    onKeyDown={(e) => { if (e.key === "Enter" && aliasValue.trim()) handleSaveAlias(); }}
                    placeholder="e.g. my-builder"
                    className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500"
                    data-testid="template-alias-input"
                    maxLength={30}
                  />
                  <button
                    onClick={handleSaveAlias}
                    disabled={aliasLoading || !aliasValue.trim()}
                    className="bg-blue-500 hover:bg-blue-600 disabled:bg-slate-700 disabled:text-slate-500 text-white rounded-lg px-3 py-1.5 text-sm transition-colors"
                    data-testid="template-alias-save"
                  >
                    {aliasLoading ? "Saving..." : "Save"}
                  </button>
                  {aliasValue && (
                    <button
                      onClick={handleClearAlias}
                      disabled={aliasLoading}
                      className="text-slate-500 hover:text-red-400 transition-colors"
                      title="Remove alias"
                      data-testid="template-alias-delete"
                    >
                      <Icon name="delete" size={18} />
                    </button>
                  )}
                </div>
                {aliasError && (
                  <p className="text-xs text-red-400 mt-1" data-testid="template-alias-error">{aliasError}</p>
                )}
                {aliasSaved && (
                  <p className="text-xs text-green-400 mt-1" data-testid="template-alias-saved">Alias saved.</p>
                )}
              </div>
            )}

            {/* User message input */}
            <div data-testid="template-user-input-section">
              <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
                What do you want to tell this agent?
              </label>
              <textarea
                value={userMessage}
                onChange={(e) => setUserMessage(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSpawn(current, userMessage); } }}
                rows={3}
                placeholder="Describe the specific task, provide context, or paste content for the agent to work with... (Enter to spawn, Shift+Enter for newline)"
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 resize-none"
                data-testid="template-user-message-input"
                autoFocus
              />
            </div>

            <div className="flex items-center justify-end gap-3 pt-1">
              <button
                onClick={onCancel}
                className="text-slate-400 hover:text-white text-sm transition-colors px-4 py-2"
              >
                Cancel
              </button>
              <button
                onClick={() => { onSpawn(current, userMessage); }}
                className="bg-pink-500 hover:bg-pink-600 text-white rounded-lg px-5 py-2 text-sm font-medium transition-colors"
                data-testid="template-spawn-button"
              >
                Spawn agent
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // New template creation form
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-lg p-6 shadow-xl">
        <h3 className="text-lg font-semibold text-white mb-4">
          {isNew ? "New Template" : "Edit Template"}
        </h3>

        {/* Name */}
        <label className="block text-sm text-slate-400 mb-1">Name</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && name.trim()) onSpawn(current, userMessage);
          }}
          placeholder="e.g. Research Agent"
          className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 mb-4"
        />

        {/* Description / Prompt */}
        <label className="block text-sm text-slate-400 mb-1">Description</label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && name.trim()) {
              e.preventDefault();
              onSpawn(current, userMessage);
            }
          }}
          rows={3}
          placeholder="What should this agent do? (Enter to spawn, Shift+Enter for newline)"
          className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 mb-4 resize-none"
        />

        {/* Icon */}
        <IconPicker value={icon} onChange={setIcon} />

        {/* Model + Budget row */}
        <div className="flex gap-4 mb-6">
          <div className="flex-1">
            <label className="block text-sm text-slate-400 mb-1">Model</label>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500"
            >
              <option value="sonnet">Sonnet</option>
              <option value="opus">Opus</option>
              <option value="haiku">Haiku</option>
            </select>
          </div>
          <div className="flex-1">
            <label className="block text-sm text-slate-400 mb-1">Budget ($)</label>
            <input
              type="number"
              min={0}
              step={0.5}
              value={budget}
              onChange={(e) => setBudget(parseFloat(e.target.value) || 0)}
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500"
            />
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center justify-end gap-3">
          <button
            onClick={onCancel}
            className="text-slate-400 hover:text-white text-sm transition-colors px-4 py-2"
          >
            Cancel
          </button>
          {isNew && (
            <button
              onClick={() => { if (name.trim()) onSave(current); }}
              disabled={!name.trim()}
              className="border border-blue-500 text-blue-400 hover:bg-blue-500/10 disabled:border-slate-700 disabled:text-slate-600 rounded-lg px-4 py-2 text-sm transition-colors"
            >
              Save Template
            </button>
          )}
          <button
            onClick={() => { if (name.trim()) onSpawn(current, userMessage); }}
            disabled={!name.trim()}
            className="bg-pink-500 hover:bg-pink-600 disabled:bg-slate-700 disabled:text-slate-500 text-white rounded-lg px-4 py-2 text-sm transition-colors"
          >
            Spawn Agent
          </button>
        </div>
      </div>
    </div>
  );
}

// First-paint cache (needle 299).
//
// See the matching block in Tasks.tsx for the full rationale. Short
// version: Tori sees a blank Active Sessions panel for several seconds
// every time she opens /agents cold, so we seed the list from the last
// good /agents response stashed in localStorage. The live poll still
// runs and overwrites the cache as soon as fresh data arrives.
const AGENTS_CACHE_KEY = "myos.agentsCache.v1";

// Cache keys for the three template lists shown on the Templates tab.
// Templates parse rarely (only when the user adds or removes one) but
// the first paint otherwise waits on three parallel fetches. Seeding
// state from localStorage lets the cards appear instantly, then the
// background fetch reconciles any drift.
const TEMPLATES_CACHE_KEY = "myos.templatesCache.v1";
const PM_TEMPLATES_CACHE_KEY = "myos.pmTemplatesCache.v1";
const USER_TEMPLATES_CACHE_KEY = "myos.userTemplatesCache.v1";

// Persistence key for the Recent tab's "show cancelled" toggle. Default
// is hidden. When the user clicks the chip to reveal cancelled rows we
// store "1" here so a page refresh keeps their choice.
const RECENT_SHOW_CANCELLED_KEY = "myos.recentShowCancelled.v1";

function readShowCancelledPref(): boolean {
  try {
    if (typeof window === "undefined" || !window.localStorage) return false;
    return window.localStorage.getItem(RECENT_SHOW_CANCELLED_KEY) === "1";
  } catch {
    return false;
  }
}

function writeShowCancelledPref(show: boolean) {
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    if (show) {
      window.localStorage.setItem(RECENT_SHOW_CANCELLED_KEY, "1");
    } else {
      window.localStorage.removeItem(RECENT_SHOW_CANCELLED_KEY);
    }
  } catch {
    // Quota or serialization errors are not fatal.
  }
}

// Active-session card expanded state lives in sessionStorage so
// navigating away and back in the same tab keeps what the user chose.
// A fresh tab starts with every card collapsed. Each agent gets its
// own entry keyed by name so multiple cards can be expanded
// independently of each other.
const ACTIVE_EXPANDED_KEY = "myos.agents.activeExpanded.v1";

function readActiveExpandedMap(): Record<string, boolean> {
  try {
    if (typeof window === "undefined" || !window.sessionStorage) return {};
    const raw = window.sessionStorage.getItem(ACTIVE_EXPANDED_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const out: Record<string, boolean> = {};
      for (const [k, v] of Object.entries(parsed)) {
        if (typeof v === "boolean") out[k] = v;
      }
      return out;
    }
    return {};
  } catch {
    return {};
  }
}

function writeActiveExpandedMap(map: Record<string, boolean>) {
  try {
    if (typeof window === "undefined" || !window.sessionStorage) return;
    // Drop false entries so the blob does not grow forever.
    const pruned: Record<string, boolean> = {};
    for (const [k, v] of Object.entries(map)) {
      if (v) pruned[k] = true;
    }
    window.sessionStorage.setItem(ACTIVE_EXPANDED_KEY, JSON.stringify(pruned));
  } catch {
    // Quota or privacy mode: skip persistence.
  }
}

function readAgentsCache(): AgentInfo[] {
  try {
    if (typeof window === "undefined" || !window.localStorage) return [];
    const raw = window.localStorage.getItem(AGENTS_CACHE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as AgentInfo[]) : [];
  } catch {
    return [];
  }
}

function writeAgentsCache(agents: AgentInfo[]) {
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    window.localStorage.setItem(AGENTS_CACHE_KEY, JSON.stringify(agents));
  } catch {
    // Quota or serialization errors are not fatal. Skip the cache.
  }
}

// Generic reader for template list caches. Returns [] on any error so
// a corrupt cache never blocks the page.
function readTemplatesCache<T>(key: string): T[] {
  try {
    if (typeof window === "undefined" || !window.localStorage) return [];
    const raw = window.localStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as T[]) : [];
  } catch {
    return [];
  }
}

function writeTemplatesCache<T>(key: string, list: T[]): void {
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    window.localStorage.setItem(key, JSON.stringify(list));
  } catch {
    // Quota or serialization errors are not fatal.
  }
}

// Hardcoded seed for the persona-templates list. Used on the very first
// cold visit when localStorage has no cache yet. Without this, cards like
// ``Roadmap`` would not appear until the /settings + /persona-templates
// fetch chain resolved (about 1s on a slow connection). The seed mirrors
// the PM persona built-ins shipped in
// ``api/services/agent_templates_store.py`` so the user sees real cards
// instantly. The background fetch reconciles any drift on the same paint.
const DEFAULT_PERSONA_TEMPLATES_SEED: PMAgentTemplateSeed[] = [
  {
    id: "builtin-pm-competitive-scan",
    name: "Competitive Scan",
    description: "Research what competitors are shipping in a product area.",
    icon: "monitor_heart",
    prompt_template: "",
    model: "sonnet",
    budget: 3.0,
    builtin: true,
    source: "marketplace",
  },
  {
    id: "builtin-pm-prd",
    name: "PRD",
    description: "Turn a rough idea into a product requirements doc.",
    icon: "article",
    prompt_template: "",
    model: "sonnet",
    budget: 3.0,
    builtin: true,
    source: "marketplace",
  },
  {
    id: "builtin-pm-customer-interviews",
    name: "Customer Interview Notes",
    description: "Turn raw interview notes into themes and insights.",
    icon: "record_voice_over",
    prompt_template: "",
    model: "sonnet",
    budget: 2.0,
    builtin: true,
    source: "marketplace",
  },
  {
    id: "builtin-pm-launch-checklist",
    name: "Launch Checklist",
    description: "Generate a launch checklist for a new feature.",
    icon: "checklist",
    prompt_template: "",
    model: "sonnet",
    budget: 2.0,
    builtin: true,
    source: "marketplace",
  },
  {
    id: "builtin-pm-roadmap",
    name: "Roadmap",
    description: "Draft a multi-year quarterly roadmap from a set of initiatives.",
    icon: "timeline",
    prompt_template: "",
    model: "sonnet",
    budget: 3.0,
    builtin: true,
    source: "marketplace",
  },
  {
    id: "builtin-pm-stakeholder-update",
    name: "Stakeholder Update",
    description: "Write a weekly update for your leadership team.",
    icon: "campaign",
    prompt_template: "",
    model: "sonnet",
    budget: 2.0,
    builtin: true,
    source: "marketplace",
  },
];

// Read the persona-templates cache, falling back to the hardcoded PM seed
// so the very first cold visit never paints an empty Templates tab. The
// background fetch overwrites this with the real backend data within
// milliseconds.
function readPersonaTemplatesCacheOrSeed<T>(key: string): T[] {
  const cached = readTemplatesCache<T>(key);
  if (cached.length > 0) return cached;
  return (DEFAULT_PERSONA_TEMPLATES_SEED as unknown) as T[];
}

interface PMAgentTemplateSeed {
  id: string;
  name: string;
  description: string;
  icon: string;
  prompt_template: string;
  model: string;
  budget: number;
  builtin: boolean;
  source: "marketplace" | "builtin" | "custom";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

function formatModelShort(model?: string): string {
  if (!model) return "auto";
  if (model.includes("sonnet")) return "sonnet";
  if (model.includes("opus")) return "opus";
  if (model.includes("haiku")) return "haiku";
  return model.split("-")[0] || model;
}

const ROLE_LABEL: Record<string, string> = {
  User: "User",
  Assistant: "Assistant",
  Tool: "Tool",
  "Tool result": "Tool result",
  System: "System",
};

const ROLE_COLORS: Record<string, string> = {
  User: "text-blue-400",
  Assistant: "text-emerald-400",
  Tool: "text-amber-400",
  "Tool result": "text-amber-300",
  System: "text-slate-400",
};

function TranscriptContent({ content }: { content: string }) {
  if (hasSpeakerPrefixes(content)) {
    const blocks = parseTranscript(content);
    return (
      <div className="space-y-3">
        {blocks.map((block, idx) => (
          <Card key={idx} variant="outlined" padding="sm">
            <div className={`text-xs font-semibold uppercase tracking-wide mb-1 ${ROLE_COLORS[block.role] ?? "text-slate-400"}`}>
              {ROLE_LABEL[block.role] ?? block.role}
            </div>
            <div className="chat-bubble-content text-sm text-slate-300 space-y-1">
              {renderMarkdown(block.body)}
            </div>
          </Card>
        ))}
      </div>
    );
  }
  // Fallback: no speaker prefixes, render full content as markdown
  return (
    <div className="chat-bubble-content text-sm text-slate-300 space-y-1">
      {renderMarkdown(content)}
    </div>
  );
}

function AgentStatusBar({ spawnedAt, budget, model, transcriptBytes, transcriptLines, durationStats }: {
  spawnedAt: string;
  budget?: string;
  model?: string;
  transcriptBytes?: number;
  transcriptLines?: number;
  durationStats?: { median_seconds: number; sample_count: number } | null;
}) {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);

  const startMs = new Date(spawnedAt).getTime();
  const elapsedSec = Math.max(0, Math.floor((now - startMs) / 1000));
  const elapsedMin = Math.floor(elapsedSec / 60);
  const elapsedRemSec = elapsedSec % 60;

  // Remaining time comes from a rolling median of recently completed
  // agents (api/services/agent_duration_stats.py). We subtract elapsed
  // from that median while the agent is within the typical window. Once
  // elapsed exceeds the median the "left" estimate is a lie, so we
  // switch to an honest "running Nm, typical ~Mm" readout that keeps
  // growing with real time rather than freezing at "<1m left". When we
  // do not yet have any samples, show "calculating" instead of a
  // guessed number.
  let etaText = "";
  if (durationStats && durationStats.sample_count > 0 && durationStats.median_seconds > 0) {
    const medianSec = durationStats.median_seconds;
    const typicalMin = Math.max(1, Math.round(medianSec / 60));
    if (elapsedSec >= medianSec) {
      // Past typical: no more fake countdown. Show honest "over typical".
      const overMin = Math.max(1, Math.round((elapsedSec - medianSec) / 60));
      etaText = `${overMin}m over typical ~${typicalMin}m`;
    } else {
      const remainingSec = medianSec - elapsedSec;
      if (remainingSec < 60) {
        etaText = "<1m left";
      } else {
        const remainingMin = Math.round(remainingSec / 60);
        etaText = `~${remainingMin}m left`;
      }
    }
  } else if (durationStats && durationStats.sample_count === 0) {
    etaText = "calculating";
  }

  const segments = [
    `${elapsedMin}:${elapsedRemSec.toString().padStart(2, "0")}`,
    formatModelShort(model),
    budget ? `$${budget} cap` : null,
    transcriptBytes ? formatBytes(transcriptBytes) : null,
    transcriptLines ? `${transcriptLines} lines` : null,
  ].filter(Boolean);

  return (
    <div className="bg-slate-950/80 border border-slate-800 rounded-lg px-3 py-2 font-mono text-xs flex items-center gap-1 flex-wrap" data-testid="agent-status-bar">
      {segments.map((seg, i) => (
        <span key={i} className="flex items-center gap-1">
          {i > 0 && <span className="text-slate-700 mx-0.5">|</span>}
          <span className={i === 0 ? "text-green-400" : "text-slate-400"}>{seg}</span>
        </span>
      ))}
      {etaText && (
        <>
          <span className="text-slate-700 mx-0.5">|</span>
          <span className="text-blue-400">{etaText}</span>
        </>
      )}
    </div>
  );
}

// One-line condensed view shown when an active agent card is collapsed.
// Mirrors the AgentStatusBar but in a single inline row so the card stays
// tiny. Tori asked for this so the Active Sessions list is scannable by
// default and the full chat + controls only appear on demand.
function AgentCompactSummary({ spawnedAt, budget, model, costEstimate, durationStats }: {
  spawnedAt?: string;
  budget?: string;
  model?: string;
  costEstimate?: number;
  durationStats?: { median_seconds: number; sample_count: number } | null;
}) {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);

  const segments: string[] = [];
  if (spawnedAt) {
    const startMs = new Date(spawnedAt).getTime();
    const elapsedSec = Math.max(0, Math.floor((now - startMs) / 1000));
    if (elapsedSec < 60) {
      segments.push(`${elapsedSec}s`);
    } else {
      const m = Math.floor(elapsedSec / 60);
      const s = elapsedSec % 60;
      segments.push(`${m}:${s.toString().padStart(2, "0")}`);
    }
  }
  segments.push(formatModelShort(model));
  if (costEstimate !== undefined && costEstimate > 0 && budget) {
    segments.push(`$${costEstimate.toFixed(2)}/${budget}`);
  } else if (budget) {
    segments.push(`$${budget} cap`);
  }

  // See AgentStatusBar above for the rationale. Once elapsed exceeds
  // the rolling median, showing a countdown lies. Switch to an honest
  // "running Nm over typical" readout that keeps growing with real time.
  let etaText = "";
  if (spawnedAt && durationStats && durationStats.sample_count > 0 && durationStats.median_seconds > 0) {
    const startMs = new Date(spawnedAt).getTime();
    const elapsedSec = Math.max(0, Math.floor((now - startMs) / 1000));
    const medianSec = durationStats.median_seconds;
    const typicalMin = Math.max(1, Math.round(medianSec / 60));
    if (elapsedSec >= medianSec) {
      const overMin = Math.max(1, Math.round((elapsedSec - medianSec) / 60));
      etaText = `${overMin}m over typical ~${typicalMin}m`;
    } else {
      const remainingSec = medianSec - elapsedSec;
      if (remainingSec < 60) {
        etaText = "<1m left";
      } else {
        const remainingMin = Math.round(remainingSec / 60);
        etaText = `${remainingMin}m left`;
      }
    }
  }

  return (
    <div
      className="font-mono text-xs text-slate-400 flex items-center gap-1 flex-wrap"
      data-testid="agent-compact-summary"
    >
      {segments.map((seg, i) => (
        <span key={i} className="flex items-center gap-1">
          {i > 0 && <span className="text-slate-700 mx-0.5">|</span>}
          <span className={i === 0 ? "text-green-400" : "text-slate-400"}>{seg}</span>
        </span>
      ))}
      {etaText && (
        <>
          <span className="text-slate-700 mx-0.5">|</span>
          <span className="text-blue-400">{etaText}</span>
        </>
      )}
    </div>
  );
}

function BudgetProgressBar({ tokensUsed, tokenLimit, costEstimate }: {
  tokensUsed: number;
  tokenLimit: number | null | undefined;
  costEstimate?: number;
}) {
  if (!tokenLimit || tokenLimit <= 0) {
    // No limit set, show usage only
    if (tokensUsed <= 0) return null;
    return (
      <div className="flex items-center gap-2 text-xs" data-testid="budget-bar">
        <span className="text-slate-400">{(tokensUsed / 1000).toFixed(1)}k used</span>
        {costEstimate !== undefined && costEstimate > 0 && (
          <span className="text-slate-500">(~${costEstimate.toFixed(4)})</span>
        )}
      </div>
    );
  }

  const pct = Math.min(100, Math.round((tokensUsed / tokenLimit) * 100));
  const barColor = pct < 50 ? "bg-green-500" : pct < 80 ? "bg-yellow-500" : "bg-red-500";
  const textColor = pct < 50 ? "text-green-400" : pct < 80 ? "text-yellow-400" : "text-red-400";

  return (
    <div className="mt-2" data-testid="budget-bar">
      <div className="flex items-center justify-between text-xs mb-1">
        <span className={textColor}>
          {(tokensUsed / 1000).toFixed(1)}k / {(tokenLimit / 1000).toFixed(0)}k ({pct}%)
        </span>
        {costEstimate !== undefined && costEstimate > 0 && (
          <span className="text-slate-500">~${costEstimate.toFixed(4)}</span>
        )}
      </div>
      <div className="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function RecoveryBadge({ recoveryCount, maxRecoveries }: {
  recoveryCount: number;
  maxRecoveries: number;
}) {
  if (recoveryCount <= 0) return null;
  return (
    <span
      className="text-xs font-bold px-2 py-0.5 rounded bg-yellow-500/20 text-yellow-400"
      data-testid="recovery-badge"
    >
      Recovered ({recoveryCount}/{maxRecoveries})
    </span>
  );
}

function AgentMemorySection({
  agentName,
  memory,
  clearing,
  onClear,
}: {
  agentName: string;
  memory: { facts: Record<string, string>; summaries: { text: string; saved_at: string }[] } | undefined;
  clearing: boolean;
  onClear: () => void;
}) {
  const facts = memory?.facts ?? {};
  const summaries = memory?.summaries ?? [];
  const hasSomething = Object.keys(facts).length > 0 || summaries.length > 0;

  return (
    <div className="mt-2">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
          Memory
        </span>
        {hasSomething && (
          <button
            onClick={onClear}
            disabled={clearing}
            className="text-xs text-red-400 hover:text-red-300 disabled:opacity-50 transition-colors"
          >
            {clearing ? "Clearing..." : "Clear memory"}
          </button>
        )}
      </div>
      {!hasSomething ? (
        <p className="text-xs text-slate-600">
          No memory yet. When {agentName} finishes with a summary, it will appear here and load automatically next time this agent runs.
        </p>
      ) : (
        <div className="space-y-2">
          {Object.keys(facts).length > 0 && (
            <div className="bg-slate-950 rounded-lg p-3 text-xs space-y-1">
              <p className="text-slate-500 mb-1">Remembered facts</p>
              {Object.entries(facts).map(([k, v]) => (
                <div key={k} className="flex gap-2">
                  <span className="text-slate-400 shrink-0">{k}:</span>
                  <span className="text-white">{v}</span>
                </div>
              ))}
            </div>
          )}
          {summaries.length > 0 && (
            <div className="bg-slate-950 rounded-lg p-3 text-xs space-y-2">
              <p className="text-slate-500 mb-1">Past sessions</p>
              {summaries.map((s, i) => (
                <div key={i} className="flex gap-2">
                  <span className="text-slate-600 shrink-0 tabular-nums">
                    {s.saved_at ? s.saved_at.slice(0, 10) : ""}
                  </span>
                  <span className="text-slate-300">{s.text}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface AgentsResponse {
  daemon_running: boolean;
  status: string;
  active: string[];
  agents: AgentInfo[];
  avg_min_per_dollar?: Record<string, number>;
}

interface TemplateCapabilities {
  writes_to: string;
  cannot_touch: string;
  budget: string;
  time_limit: string;
  sandbox: string;
}

interface AgentfileTemplate {
  name: string;
  file: string;
  content: string;
  description?: string;
  capabilities: TemplateCapabilities | null;
  parse_error: string | null;
  // Aliases folded in from alias-only .agent files (e.g. ``saa`` on
  // ``builder``, ``elit`` on ``explain-plain``). The server filters the
  // alias stubs out and attaches their stems here so the UI can render
  // them as chips under the card title.
  aliases?: string[];
}

interface TemplatesResponse {
  templates: AgentfileTemplate[];
}

interface PMAgentTemplate {
  id: string;
  name: string;
  description: string;
  icon: string;
  prompt_template: string;
  model: string;
  budget: number;
  builtin: boolean;
  source?: 'builtin' | 'marketplace' | 'custom' | string;
}

interface PMTemplatesResponse {
  templates: PMAgentTemplate[];
}

interface NudgeRecord {
  message: string;
  timestamp: string;
  source: string;
  stdin_delivered: boolean;
  // Delivery status field added for needle 235. Older records written
  // before the fix will not have these, so the UI treats them as
  // optional and falls back to a neutral status line.
  delivery?: "stdin" | "file_only" | "unavailable";
  delivery_message?: string;
  // "user_message" for the default chat send, "correction" for the
  // structured course-change channel. Optional for back compat with
  // older records that predate the field.
  kind?: "user_message" | "correction" | string;
}

interface NudgeReplyRecord {
  message: string;
  timestamp: string;
  source: string;
  in_reply_to?: string | null;
  // "ack" tags the warm canned reply from chat_ack_bot. "real" tags
  // the substantive reply from the live subagent. Older records may
  // have neither field, in which case the UI treats them as "real".
  kind?: "ack" | "real" | string;
}

interface NudgeResponse {
  result: string;
  nudge: NudgeRecord;
}

interface NudgesListResponse {
  agent: string;
  nudges: NudgeRecord[];
  session_nudges: NudgeRecord[];
  // Replies were added by needle 235. Older backends may not return
  // them, so the UI treats them as optional and defaults to [].
  replies?: NudgeReplyRecord[];
  session_replies?: NudgeReplyRecord[];
}

interface DelegationTarget {
  id: string;
  title: string;
}

interface DelegationRingNeedle {
  id: string;
  priority: string;
  title: string;
}

interface DelegationRing {
  radius: number;
  total: number;
  open: number;
  needles: DelegationRingNeedle[];
}

interface DelegationResponse {
  point: string | null;
  point_title: string;
  rings: DelegationRing[];
  delegation_targets: DelegationTarget[];
}

interface GrantRequest {
  id: string;
  type: string;
  agent: string;
  target: string;
  status: string;
  requested_at: string;
  detail?: string;
}

interface GrantsResponse {
  grants: GrantRequest[];
  status_filter: string;
}

interface AgentMemoryFact {
  [key: string]: string;
}

interface AgentMemorySummary {
  text: string;
  saved_at: string;
}

interface AgentMemoryResponse {
  agent: string;
  facts: AgentMemoryFact;
  summaries: AgentMemorySummary[];
}

interface WorkspaceMessage {
  id: string;
  agent_name: string;
  content: string;
  message_type: "finding" | "question" | "result" | "context";
  timestamp: string;
}

interface WorkspaceMessagesResponse {
  messages: WorkspaceMessage[];
  count: number;
}

const statusLabel = (status: string) => {
  switch (status) {
    case "running":
    case "spawned":
      return "RUNNING";
    case "completed":
      return "COMPLETE";
    case "failed":
      return "FAILED";
    case "killed":
      return "KILLED";
    case "recovering":
      return "RECOVERING";
    case "terminated_stale":
      return "STALE";
    case "abandoned":
      return "ABANDONED";
    default:
      return status.toUpperCase();
  }
};

const statusColor = (status: string) => {
  switch (status) {
    case "running":
    case "spawned":
      return "bg-green-500/20 text-green-400";
    case "completed":
      return "bg-blue-500/20 text-blue-400";
    case "failed":
      return "bg-red-500/20 text-red-400";
    case "killed":
      return "bg-orange-500/20 text-orange-400";
    case "recovering":
      return "bg-yellow-500/20 text-yellow-400";
    case "terminated_stale":
    case "abandoned":
      return "bg-red-500/20 text-red-400";
    default:
      return "bg-slate-500/20 text-slate-400";
  }
};

function tryBrowserNotification(agentName: string, status: string) {
  if (!("Notification" in window)) return;
  const messages: Record<string, string> = {
    completed: "finished successfully",
    failed: "failed",
    killed: "was cancelled",
    stopped: "stopped",
  };
  const msg = messages[status];
  if (!msg) return;
  if (Notification.permission === "granted") {
    new Notification(`${agentName} ${msg}`, { silent: true });
  } else if (Notification.permission !== "denied") {
    Notification.requestPermission().then((perm) => {
      if (perm === "granted") {
        new Notification(`${agentName} ${msg}`, { silent: true });
      }
    });
  }
}
function PMTemplateEditorForm({
  initial,
  saving,
  onSave,
  onCancel,
}: {
  initial: PMAgentTemplate | null;
  saving: boolean;
  onSave: (data: Partial<PMAgentTemplate>) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [promptTemplate, setPromptTemplate] = useState(initial?.prompt_template ?? "");
  const [icon, setIcon] = useState(initial?.icon ?? "smart_toy");
  const [model, setModel] = useState(initial?.model ?? "sonnet");
  const [budget, setBudget] = useState(initial?.budget ?? 2.0);

  const handleSave = () => {
    if (name.trim()) {
      onSave({ name, description, prompt_template: promptTemplate, icon, model, budget });
    }
  };

  return (
    <>
      <label className="block text-sm text-slate-400 mb-1">Name</label>
      <input
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") handleSave(); }}
        placeholder="e.g. Competitive analysis"
        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 mb-4"
      />

      <label className="block text-sm text-slate-400 mb-1">Description</label>
      <input
        type="text"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") handleSave(); }}
        placeholder="One line summary of what this template does"
        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 mb-4"
      />

      <label className="block text-sm text-slate-400 mb-1">Instructions template</label>
      <textarea
        value={promptTemplate}
        onChange={(e) => setPromptTemplate(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSave(); } }}
        rows={4}
        placeholder="Use [placeholders] for parts you will fill in before spawning (Enter to save, Shift+Enter for newline)"
        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 mb-1 resize-none"
      />
      <p className="text-xs text-slate-500 mb-4">Use [square brackets] for parts the user fills in. Example: Research [topic] and write a summary.</p>

      <IconPicker value={icon} onChange={setIcon} />

      <div className="flex gap-4 mb-6">
        <div className="flex-1">
          <label className="block text-sm text-slate-400 mb-1">Model</label>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500"
          >
            <option value="sonnet">Sonnet</option>
            <option value="opus">Opus</option>
            <option value="haiku">Haiku</option>
          </select>
        </div>
        <div className="flex-1">
          <label className="block text-sm text-slate-400 mb-1">Budget ($)</label>
          <input
            type="number"
            min={0}
            step={0.5}
            value={budget}
            onChange={(e) => setBudget(parseFloat(e.target.value) || 0)}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>

      <div className="flex items-center justify-end gap-3">
        <button
          onClick={onCancel}
          className="text-slate-400 hover:text-white text-sm transition-colors px-4 py-2"
        >
          Cancel
        </button>
        <button
          onClick={handleSave}
          disabled={!name.trim() || saving}
          className="bg-blue-500 hover:bg-blue-600 disabled:bg-slate-700 disabled:text-slate-500 text-white rounded-lg px-4 py-2 text-sm transition-colors"
        >
          {saving ? "Saving..." : "Save Template"}
        </button>
      </div>
    </>
  );
}

export default function Agents() {
  const [activeTab, setActiveTab] = useState("Active");
  // Ref so fetchAgents can read the current tab without a stale closure.
  const activeTabRef = useRef("Active");
  const setActiveTabWithRef = (tab: string) => {
    activeTabRef.current = tab;
    setActiveTab(tab);
  };
  // Seed from the localStorage cache so the first paint shows the last
  // known list of agents instead of the empty "Loading..." state.
  // Needle 299. The daemon-filter matches the runtime filter in
  // fetchAgents so a stale cached daemon row never sneaks back in.
  const [allAgents, setAllAgents] = useState<AgentInfo[]>(() =>
    readAgentsCache().filter((a) => a.name !== 'daemon'),
  );
  // Agents the user locally dismissed (cancelled or deleted). The poll
  // must not re-add these until the page is fully reloaded.
  const dismissedAgentsRef = useRef<Set<string>>(new Set());
  // Tracks whether the first /agents fetch has resolved. We use this to show
  // a loading state on first paint instead of flashing the empty state.
  // Subsequent polling refreshes do not reset this flag, so the spinner never
  // reappears during background updates.
  // When we have cached rows to show, start in the loaded state so the
  // "Loading..." placeholder never appears over real data.
  const [agentsLoaded, setAgentsLoaded] = useState<boolean>(
    () => readAgentsCache().length > 0,
  );
  const [, setActiveAgents] = useState<string[]>([]);
  const [, setConnectionStatus] = useState("Connecting...");
  const [, setDaemonRunning] = useState(false);
  // Rolling median of recent agent durations, used by AgentStatusBar
  // to compute "~Nm left". Fetched from /api/agents/duration-stats on
  // mount and refreshed every 5 minutes. Null until first response.
  const [durationStats, setDurationStats] = useState<{
    median_seconds: number;
    p75_seconds: number;
    sample_count: number;
    window_days: number;
  } | null>(null);
  // Seed template state from localStorage so the Templates tab paints
  // cards immediately instead of flashing the empty state while the
  // three fetches run. The background fetch reconciles any drift.
  const [templates, setTemplates] = useState<TemplatesResponse["templates"]>(
    () => readTemplatesCache<TemplatesResponse["templates"][number]>(TEMPLATES_CACHE_KEY),
  );
  // Seed from cache OR a hardcoded PM persona default so the first cold
  // visit (no localStorage) still paints Roadmap and the other PM cards
  // synchronously. The fetch overwrites this once it returns.
  const [pmTemplates, setPmTemplates] = useState<PMAgentTemplate[]>(
    () => readPersonaTemplatesCacheOrSeed<PMAgentTemplate>(PM_TEMPLATES_CACHE_KEY),
  );
  // persona-aware: persona built-ins come from /agents/persona-templates,
  // user custom templates come from /agents/user-templates (persona-agnostic).
  const [currentPersona, setCurrentPersona] = useState<string>("");
  const [userTemplates, setUserTemplates] = useState<PMAgentTemplate[]>(
    () => readTemplatesCache<PMAgentTemplate>(USER_TEMPLATES_CACHE_KEY),
  );
  const [pmTemplateEditor, setPmTemplateEditor] = useState<{
    open: boolean;
    template: PMAgentTemplate | null;
    isNew: boolean;
  }>({ open: false, template: null, isNew: false });
  const [pmTemplateSaving, setPmTemplateSaving] = useState(false);
  const [showNewForm, setShowNewForm] = useState(false);
  const [newAgentName, setNewAgentName] = useState("");
  const [newAgentPrompt, setNewAgentPrompt] = useState("");
  const [newAgentTokenLimit, setNewAgentTokenLimit] = useState<string>("");
  const [recoveringAgents, setRecoveringAgents] = useState<Record<string, boolean>>({});
  const [, setLastUpdate] = useState<Date | null>(null);
  const [transcriptModal, setTranscriptModal] = useState<{name: string; content: string; loading: boolean; error?: string; retryable?: boolean} | null>(null);
  const { confirm, confirmProps } = useConfirm();

  const openTranscript = async (name: string) => {
    setTranscriptModal({name, content: "", loading: true, error: undefined});
    try {
      const data = await api.get<{content: string; empty?: boolean; reason?: string}>(`/agents/${encodeURIComponent(name)}/transcript`);
      if (data.empty) {
        // Backend explicitly signalled no transcript. Show its reason
        // and do NOT offer a retry. This is not an error, just absence.
        setTranscriptModal({
          name,
          content: "",
          loading: false,
          error: data.reason ?? "No transcript available for this agent yet.",
          retryable: false,
        });
      } else {
        // 200 with content. Render it, clear any prior error state.
        setTranscriptModal({name, content: data.content ?? "", loading: false, error: undefined, retryable: false});
      }
    } catch (err) {
      // Distinguish real failures so the user sees an actionable
      // message with the HTTP status and a hint, not a generic
      // "Upstream unavailable" catch-all from the vite proxy.
      let msg: string;
      if (err instanceof ApiTimeoutError) {
        msg = "Transcript request timed out. The backend may be slow or stuck. Try again or check backend logs.";
      } else if (err instanceof ApiError) {
        if (err.status === 502 || err.status === 503 || err.status === 504) {
          msg = `Transcript endpoint returned ${err.status} (backend unreachable via proxy). Try again or check backend logs.`;
        } else if (err.status >= 500) {
          msg = `Transcript endpoint returned ${err.status}. Try again or check backend logs.`;
        } else {
          msg = `Transcript endpoint returned ${err.status}: ${err.message}`;
        }
      } else {
        const raw = err instanceof Error ? err.message : String(err);
        msg = `Could not load transcript (${raw}). Try again or check backend logs.`;
      }
      setTranscriptModal({
        name,
        content: "",
        loading: false,
        error: msg,
        retryable: true,
      });
    }
  };

  const addNotification = useNotificationStore((s) => s.addNotification);
  // Track previous agent statuses to detect changes between polls
  const prevStatusRef = useRef<Record<string, string>>({});

  // Delegation state
  const [delegationData, setDelegationData] = useState<DelegationResponse | null>(null);
  const [delegationLoading, setDelegationLoading] = useState(false);
  const [delegationError, setDelegationError] = useState("");
  const [spawningDelegation, setSpawningDelegation] = useState<Record<string, boolean>>({});

  // Grant / permission request state
  const [grants, setGrants] = useState<GrantRequest[]>([]);
  const [grantsLoading, setGrantsLoading] = useState(false);
  const [grantActioning, setGrantActioning] = useState<Record<string, boolean>>({});
  const [grantFilter, setGrantFilter] = useState<"pending" | "granted" | "denied">("pending");

  // Memory state: per-agent memory facts and summaries
  const [agentMemory, setAgentMemory] = useState<Record<string, AgentMemoryResponse>>({});
  const [memoryClearing, setMemoryClearing] = useState<Record<string, boolean>>({});

  // Workspace state
  const [workspaceMessages, setWorkspaceMessages] = useState<WorkspaceMessage[]>([]);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [workspaceClearing, setWorkspaceClearing] = useState(false);

  // Nudge state: per-agent input text and message history
  const [nudgeInputs, setNudgeInputs] = useState<Record<string, string>>({});
  const [nudgeHistory, setNudgeHistory] = useState<Record<string, NudgeRecord[]>>({});
  const [nudgeReplies, setNudgeReplies] = useState<Record<string, NudgeReplyRecord[]>>({});
  const [nudgeSending, setNudgeSending] = useState<Record<string, boolean>>({});
  // Per-agent inline error message for the nudge Send flow. Empty
  // string means no error. Shown under the input so a failed send is
  // never silent (feedback_chat_response_silent.md).
  const [nudgeErrors, setNudgeErrors] = useState<Record<string, string>>({});
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  // Active Sessions cards start collapsed so the list is scannable at a
  // glance. Clicking Expand on a card reveals the metrics bar, chat
  // thread, message input, and transcript controls. Each agent has its
  // own entry so multiple cards can be expanded independently. The map
  // is persisted in sessionStorage so an in-tab navigation preserves
  // what the user chose, but a fresh tab starts every card collapsed.
  const [activeExpandedMap, setActiveExpandedMap] = useState<Record<string, boolean>>(
    () => readActiveExpandedMap(),
  );
  const toggleActiveExpanded = useCallback((agentName: string) => {
    setActiveExpandedMap((prev) => {
      const next = { ...prev, [agentName]: !prev[agentName] };
      writeActiveExpandedMap(next);
      return next;
    });
  }, []);
  // Recent tab hides cancelled, terminated_stale, killed, abandoned,
  // stopped, and failed rows by default. Clicking the chip flips this
  // flag to show them. Persisted to localStorage so a page refresh
  // keeps the user's choice.
  const [showInactive, setShowInactive] = useState<boolean>(() => readShowCancelledPref());
  const toggleShowInactive = () => {
    setShowInactive((prev) => {
      const next = !prev;
      writeShowCancelledPref(next);
      return next;
    });
  };

  // Coordination locks (needle 338)
  const [locks, setLocks] = useState<{ name: string; holder?: string; created_at?: string }[]>([]);
  const [releasingLock, setReleasingLock] = useState<Record<string, boolean>>({});

  // Context pressure per agent (needle 337)
  const [contextPressure, setContextPressure] = useState<Record<string, { available: boolean; pressure_pct?: number }>>({});

  // Fetch nudge history and memory when expanding an agent
  useEffect(() => {
    if (expandedAgent) {
      fetchNudges(expandedAgent);
      fetchMemory(expandedAgent);
      // Poll nudges while expanded
      const interval = setInterval(() => fetchNudges(expandedAgent), 5000);
      return () => clearInterval(interval);
    }
  }, [expandedAgent]);

  // Active Sessions cards hide the chat thread until expanded. When any
  // card opens for the first time, pull the nudge history and memory so
  // the thread shows existing messages immediately rather than an empty
  // box that only fills in after the next poll tick.
  const expandedActiveNames = Object.keys(activeExpandedMap)
    .filter((n) => activeExpandedMap[n])
    .sort()
    .join("|");
  useEffect(() => {
    if (!expandedActiveNames) return;
    for (const name of expandedActiveNames.split("|")) {
      if (!name) continue;
      fetchNudges(name);
      fetchMemory(name);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandedActiveNames]);

  // Regression for needle 235: Tori sent an inline nudge to a running
  // agent and saw no reply because polling only fired while the card
  // was expanded. We now poll nudges for every active agent the user
  // has messaged in this session, whether or not its card is expanded,
  // so agent replies surface in the same place the user sent them.
  const agentsWithMessages = Object.keys(nudgeHistory);
  const agentsWithMessagesKey = agentsWithMessages.sort().join("|");
  useEffect(() => {
    if (agentsWithMessages.length === 0) return;
    const tick = () => {
      for (const name of agentsWithMessages) {
        fetchNudges(name);
      }
    };
    const interval = setInterval(tick, 3000);
    return () => clearInterval(interval);
    // Using the joined key string so the effect only re-subscribes
    // when the set of agents actually changes, not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentsWithMessagesKey]);

  // Template editor modal state
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorInitial, setEditorInitial] = useState<CustomTemplate | null>(null);
  const [editorIsNew, setEditorIsNew] = useState(false);
  const [editorBuiltInName, setEditorBuiltInName] = useState<string | null>(null);
  // The system prompt shown in the template detail view (read-only)
  const [editorPromptTemplate, setEditorPromptTemplate] = useState<string | undefined>(undefined);
  // Detail view metadata
  const [editorTemplateId, setEditorTemplateId] = useState<string | undefined>(undefined);
  const [editorSource, setEditorSource] = useState<string | undefined>(undefined);
  const [editorAliases, setEditorAliases] = useState<string[] | undefined>(undefined);
  const [editorCapabilities, setEditorCapabilities] = useState<{ writes_to: string; cannot_touch: string; budget: string; time_limit: string; sandbox: string } | null>(null);

  // Custom templates live on the server via the app store. localStorage
  // is only a first paint cache.
  const customTemplates = useAppStore((s) => s.customAgentTemplates);
  const setCustomTemplates = useAppStore((s) => s.setCustomAgentTemplates);
  const powerUserMode = useAppStore((s) => s.powerUserMode);
  const tabs = powerUserMode ? [...BASE_TABS, ...POWER_USER_TABS] : BASE_TABS;

  // Fleets panel removed. The backend /agents/fleets/* endpoints stay
  // alive for backwards compatibility, but the Plans page template grid
  // is the new home for team-style starter templates.

  const addCustomTemplate = useCallback(
    (t: CustomTemplate) => {
      setCustomTemplates([...customTemplates, t]);
    },
    [customTemplates, setCustomTemplates],
  );

  const deleteCustomTemplate = useCallback(
    (name: string) => {
      setCustomTemplates(customTemplates.filter((t) => t.name !== name));
    },
    [customTemplates, setCustomTemplates],
  );

  const isCustomTemplate = useCallback(
    (name: string) => customTemplates.some((t) => t.name === name),
    [customTemplates]
  );

  // Icon map for agentfile-backed templates. Keys are lowercase stems
  // (from the .agent filename) AND Title Case display names.
  const templateIcons: Record<string, string> = {
    // Agentfile stems (lowercase from API)
    builder: "engineering",
    diagnose: "bug_report",
    research: "search",
    review: "rate_review",
    test: "science",
    // Title Case variants
    Builder: "engineering",
    Diagnose: "bug_report",
    Research: "search",
    Review: "rate_review",
    Test: "science",
    // Marketplace templates
    "Code Review": "code",
    "Write Tests": "bug_report",
    Deploy: "rocket_launch",
    Summarizer: "summarize",
    "Daily Planner": "today",
    "Email Drafter": "mail",
    "Bug Finder": "pest_control",
    Brainstorm: "psychology",
    Writer: "edit_note",
    "Name Generator": "label",
    PRD: "article",
  };

  // Aliases shown on agentfile cards (from the store's alias lists).
  // Keyed by Title Case template name.
  const AGENTFILE_ALIASES: Record<string, string[]> = {
    Builder: ["saa"],
  };

  // Map agentfile template names to their pm-templates store IDs for the alias feature
  const AGENTFILE_TEMPLATE_IDS: Record<string, string> = {
    Builder: "builtin-builder",
    Diagnose: "builtin-diagnose",
    Research: "builtin-research",
    Review: "builtin-review",
    Test: "builtin-test",
  };

  // Convert an agentfile stem name to Title Case.
  // Hyphens in stems become spaces so ``explain-plain`` becomes
  // ``Explain Plain`` and collapses with the pm-templates entry of the
  // same name (fixes the duplicate-card bug where ``Explain-plain`` and
  // ``Explain Plain`` both rendered).
  const toTitleCase = (name: string): string => {
    const map: Record<string, string> = {
      builder: "Builder",
      diagnose: "Diagnose",
      research: "Research",
      review: "Review",
      test: "Test",
    };
    const mapped = map[name.toLowerCase()];
    if (mapped) return mapped;
    return name
      .split(/[-_\s]+/)
      .filter(Boolean)
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
      .join(" ");
  };

  const fetchAgents = async () => {
    try {
      const data = await api.get<AgentsResponse>("/agents");
      const newAgents = data.agents || [];

      // Detect status changes and fire notifications
      const prev = prevStatusRef.current;
      for (const agent of newAgents) {
        // Skip notifications for agents the user explicitly dismissed
        if (dismissedAgentsRef.current.has(agent.name)) continue;
        const prevStatus = prev[agent.name];
        const newStatus = agent.status;
        if (prevStatus !== undefined && prevStatus !== newStatus) {
          const wasActive = prevStatus === "running" || prevStatus === "spawned";
          const isDone = (TERMINAL_AGENT_STATUSES as readonly string[]).includes(newStatus);
          if (wasActive && isDone) {
            // Pass the full agent so the store can filter out chat/audit/
            // hook/subscription rows via isUserSpawnedAgent. This stops the
            // "chat-default Agent finished" toast from firing (bug 1) and
            // the store dedupes on (name, status) so the same terminal
            // transition cannot fire twice (bug 2).
            addNotification(agent, prevStatus, newStatus);
            tryBrowserNotification(agent.name, newStatus);
            // Auto-switch from Active to Recent so the row moves without
            // requiring a manual tab click or page reload.
            if (activeTabRef.current === "Active") {
              setActiveTabWithRef("Recent");
            }
          }
        }
      }
      prevStatusRef.current = Object.fromEntries(newAgents.map((a) => [a.name, a.status]));

      const filtered = newAgents.filter(
        (a) => a.name !== 'daemon' && !dismissedAgentsRef.current.has(a.name)
      );
      setAllAgents(filtered);
      // Persist the last good response so the next cold visit paints
      // real rows from the first render. Needle 299.
      writeAgentsCache(filtered);
      setActiveAgents(data.active || []);
      setDaemonRunning(data.daemon_running ?? false);
      setConnectionStatus(data.daemon_running ? "Connected" : "Standby");
      setLastUpdate(new Date());
    } catch {
      setConnectionStatus("Disconnected");
    } finally {
      // Mark the inbox as loaded once the first fetch settles (success or
      // failure). This flips the Active tab from the loading state to either
      // the real agent list or the empty state, with no flash in between.
      setAgentsLoaded(true);
    }
  };

  const fetchTemplates = async () => {
    try {
      const data = await api.get<TemplatesResponse>("/agents/templates");
      const list = data.templates || [];
      setTemplates(list);
      // Persist the fresh list so the next cold visit to the Templates
      // tab paints real cards on first render instead of empty state.
      writeTemplatesCache(TEMPLATES_CACHE_KEY, list);
    } catch {
      // keep empty
    }
  };

  // Fetch persona from settings, then load persona-specific built-ins.
  // Falls back to "pm" when persona is not set so the section is never empty.
  const fetchPersonaTemplates = async () => {
    let persona = "";
    try {
      const settings = await api.get<{ persona?: string }>("/settings");
      persona = (settings.persona || "").trim();
    } catch {
      // keep empty persona, will default to pm
    }
    setCurrentPersona(persona);
    try {
      const effectivePersona = persona || "pm";
      const data = await api.get<PMTemplatesResponse & { persona: string }>(
        `/agents/persona-templates?persona=${encodeURIComponent(effectivePersona)}`
      );
      const list = data.templates || [];
      setPmTemplates(list);
      writeTemplatesCache(PM_TEMPLATES_CACHE_KEY, list);
    } catch {
      // keep empty
    }
  };

  const fetchUserTemplates = async () => {
    try {
      const data = await api.get<PMTemplatesResponse>("/agents/user-templates");
      const list = data.templates || [];
      setUserTemplates(list);
      writeTemplatesCache(USER_TEMPLATES_CACHE_KEY, list);
    } catch {
      // keep empty
    }
  };


  const handleUsePmTemplate = (tpl: PMAgentTemplate) => {
    // Open the template detail modal so the user can type a prompt (or
    // accept the template's default prompt) and then spawn. The prior
    // fast-path skipped the modal and appended a random suffix to the
    // agent name; both of those were wrong. The modal gives the user the
    // intended "type what you want, click spawn" flow, and its spawn
    // callback calls handleSpawn() which already does optimistic insert +
    // Active tab switch, so the row still appears instantly on click.
    setEditorInitial({
      name: tpl.name,
      description: tpl.description,
      icon: tpl.icon,
      model: tpl.model,
      budget: tpl.budget,
    });
    setEditorIsNew(false);
    // Pass the template name through so the modal spawn path tags the
    // resulting agent row with its template. Without this, the POST body
    // drops the `template` field entirely and backend demo_mode detection
    // (which looks up the agentfile by template name) stops working, so
    // Roadmap loses its prewarm-replay path and its Haiku coercion.
    setEditorBuiltInName(tpl.builtin ? tpl.name : null);
    setEditorPromptTemplate(tpl.prompt_template);
    setEditorTemplateId(tpl.id);
    setEditorSource(tpl.source ?? (tpl.builtin ? "builtin" : "marketplace"));
    setEditorAliases(undefined);
    setEditorCapabilities(null);
    setEditorOpen(true);
  };

  const handleSavePmTemplate = async (d: Partial<PMAgentTemplate>) => {
    setPmTemplateSaving(true);
    try {
      if (pmTemplateEditor.isNew) {
        await api.post("/agents/user-templates", d);
      } else if (pmTemplateEditor.template) {
        await api.put(`/agents/user-templates/${pmTemplateEditor.template.id}`, d);
      }
      await fetchUserTemplates();
      setPmTemplateEditor({ open: false, template: null, isNew: false });
    } catch {
      // keep
    } finally {
      setPmTemplateSaving(false);
    }
  };

  const handleDeletePmTemplate = async (templateId: string) => {
    try {
      await api.delete(`/agents/user-templates/${templateId}`);
      await fetchUserTemplates();
    } catch {
      // keep
    }
  };

  const fetchDelegation = async () => {
    setDelegationLoading(true);
    setDelegationError("");
    try {
      const data = await api.get<DelegationResponse>("/agents/delegate");
      setDelegationData(data);
    } catch {
      setDelegationError("Could not load delegation suggestions. The task graph may be empty.");
    } finally {
      setDelegationLoading(false);
    }
  };

  const handleDelegateSpawn = async (target: DelegationTarget) => {
    const agentName = `delegate-${target.id.replace("→", "")}`;
    setSpawningDelegation((prev) => ({ ...prev, [target.id]: true }));
    try {
      // Fetch the full task details (description, AC) before spawning so
      // the agent gets real context, not just the title.
      let taskContext = `Task: "${target.title}"`;
      try {
        const briefing = await api.get<{description?: string; acceptance_criteria?: string; priority?: string}>(`/tasks/${encodeURIComponent(target.id)}/briefing`);
        if (briefing.description) taskContext += `\n\nDescription:\n${briefing.description}`;
        if (briefing.acceptance_criteria) taskContext += `\n\nAcceptance criteria:\n${briefing.acceptance_criteria}`;
        if (briefing.priority) taskContext += `\n\nPriority: ${briefing.priority}`;
      } catch {
        // fall through with just the title
      }
      await api.post("/agents/spawn", {
        name: agentName,
        prompt: `You have been handed off a task. Read it, complete it, and post a short summary to the shared workspace when done.\n\n${taskContext}\n\nWhen finished, close the task using ostk and report what you did.`,
        model: "sonnet",
        budget: 2.0,
      });
      await fetchAgents();
      setActiveTabWithRef("Active");
    } catch {
      // handle silently
    } finally {
      setSpawningDelegation((prev) => ({ ...prev, [target.id]: false }));
    }
  };

  const fetchGrants = useCallback(async (status?: string) => {
    const filter = status || grantFilter;
    setGrantsLoading(true);
    try {
      const data = await api.get<GrantsResponse>(`/agents/grants?status=${filter}`);
      setGrants(data.grants || []);
    } catch {
      setGrants([]);
    } finally {
      setGrantsLoading(false);
    }
  }, [grantFilter]);

  const handleApproveGrant = async (grantId: string) => {
    setGrantActioning((prev) => ({ ...prev, [grantId]: true }));
    try {
      await api.post(`/agents/grants/${grantId}/approve`);
      await fetchGrants();
    } catch {
      // handle silently
    } finally {
      setGrantActioning((prev) => ({ ...prev, [grantId]: false }));
    }
  };

  const handleDenyGrant = async (grantId: string) => {
    setGrantActioning((prev) => ({ ...prev, [grantId]: true }));
    try {
      await api.post(`/agents/grants/${grantId}/deny`);
      await fetchGrants();
    } catch {
      // handle silently
    } finally {
      setGrantActioning((prev) => ({ ...prev, [grantId]: false }));
    }
  };

  const fetchMemory = async (agentName: string) => {
    try {
      const data = await api.get<AgentMemoryResponse>(`/agents/${agentName}/memory`);
      setAgentMemory((prev) => ({ ...prev, [agentName]: data }));
    } catch {
      // keep existing
    }
  };

  const handleClearMemory = async (agentName: string) => {
    setMemoryClearing((prev) => ({ ...prev, [agentName]: true }));
    try {
      await api.delete(`/agents/${agentName}/memory`);
      setAgentMemory((prev) => ({
        ...prev,
        [agentName]: { agent: agentName, facts: {}, summaries: [] },
      }));
    } catch {
      // handle silently
    } finally {
      setMemoryClearing((prev) => ({ ...prev, [agentName]: false }));
    }
  };

  const fetchWorkspace = async () => {
    setWorkspaceLoading(true);
    try {
      const data = await api.get<WorkspaceMessagesResponse>('/workspace/messages');
      setWorkspaceMessages(data.messages || []);
    } catch {
      // keep existing
    } finally {
      setWorkspaceLoading(false);
    }
  };

  const clearWorkspace = async () => {
    setWorkspaceClearing(true);
    try {
      await api.delete('/workspace/messages');
      setWorkspaceMessages([]);
    } catch {
      // handle silently
    } finally {
      setWorkspaceClearing(false);
    }
  };

  const fetchNudges = async (agentName: string) => {
    try {
      const data = await api.get<NudgesListResponse>(`/agents/${agentName}/nudges`);
      // Merge file-based and session nudges from the server, then
      // merge again with any optimistic entries we already have in
      // local state. A server poll must never erase a message the
      // user just sent, so local entries always win the dedupe and
      // stay visible until the server catches up. Needle 235 added
      // this invariant after the inline status line depended on the
      // optimistic delivery_message that the poll would otherwise
      // overwrite before the user had a chance to read it.
      const serverAll = [...(data.nudges || []), ...(data.session_nudges || [])];
      setNudgeHistory((prev) => {
        const existing = prev[agentName] || [];
        const all = [...serverAll, ...existing];
        const seen = new Set<string>();
        const unique = all.filter((n) => {
          const key = `${n.timestamp}-${n.message}`;
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
        unique.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
        return { ...prev, [agentName]: unique };
      });

      // Merge file-based and session replies the same way. Needle 235
      // added this second channel so agent responses surface inline
      // without the user having to click View Transcript.
      const serverReplies = [...(data.replies || []), ...(data.session_replies || [])];
      setNudgeReplies((prev) => {
        const existing = prev[agentName] || [];
        const all = [...serverReplies, ...existing];
        const seen = new Set<string>();
        const unique = all.filter((r) => {
          const key = `${r.timestamp}-${r.message}`;
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
        unique.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
        return { ...prev, [agentName]: unique };
      });
    } catch {
      // keep existing
    }
  };

  const handleNudge = async (agentName: string, explicitMessage?: string) => {
    // AgentChatThread manages its own input state and passes the typed
    // message directly, so when explicitMessage is provided we use it and
    // skip reading from nudgeInputs. The older call sites that still rely
    // on nudgeInputs continue to work unchanged.
    const rawMessage = explicitMessage ?? nudgeInputs[agentName] ?? "";
    const message = rawMessage.trim();
    if (!message) return;

    // Clear any previous error line for this agent so the user gets a
    // fresh slate on every send attempt.
    setNudgeErrors((prev) => {
      if (!prev[agentName]) return prev;
      const next = { ...prev };
      delete next[agentName];
      return next;
    });
    setNudgeSending((prev) => ({ ...prev, [agentName]: true }));

    // Belt and suspenders: even if one of the state updates below
    // throws (e.g. a setter closed over a stale scope, or an exception
    // inside a functional updater), the finally block guarantees the
    // sending flag is cleared. This is the regression guard for needle
    // 237 where the Send button got stuck on "Sending..." because the
    // previous implementation relied solely on a silent catch to unwind
    // and swallowed every clue about why. Do not remove this block.
    try {
      const resp = await api.post<NudgeResponse>(`/agents/${agentName}/nudge`, { message });
      // Clear the sending flag FIRST, before any other state update
      // that could theoretically throw. This makes the button
      // responsive again the instant the server replies, even if the
      // optimistic history merge below hits a bug.
      setNudgeSending((prev) => {
        if (!prev[agentName]) return prev;
        const next = { ...prev };
        delete next[agentName];
        return next;
      });
      // Add to local history immediately.
      setNudgeHistory((prev) => ({
        ...prev,
        [agentName]: [...(prev[agentName] || []), resp.nudge],
      }));
      setNudgeInputs((prev) => ({ ...prev, [agentName]: "" }));
      // Pull the latest nudges and replies right away so any
      // reply already on disk surfaces without waiting a poll cycle.
      // The merge logic in fetchNudges protects the optimistic
      // record we just added. We do not await it so a slow follow-up
      // fetch cannot wedge the Send button.
      void fetchNudges(agentName);
    } catch (err) {
      // Never silent. Tori must always see what went wrong. Per
      // feedback_chat_response_silent.md: chat and chat-adjacent flows
      // must surface an error, never render blank.
      const errorMessage =
        err instanceof ApiTimeoutError
          ? "The server did not respond in time. Your message was not sent. Please try again."
          : err instanceof ApiError
            ? `Could not send message. ${err.message}`
            : "Could not send message. Check your connection and try again.";
      setNudgeErrors((prev) => ({ ...prev, [agentName]: errorMessage }));
    } finally {
      // Final clear. If the try block already cleared the flag this
      // is a no-op for the agent's key. If anything threw before the
      // early clear, this is the safety net that guarantees the
      // button returns to "Send".
      setNudgeSending((prev) => {
        if (!prev[agentName]) return prev;
        const next = { ...prev };
        delete next[agentName];
        return next;
      });
    }
  };

  // Fetch coordination locks (needle 338)
  const fetchLocks = useCallback(async () => {
    try {
      const data = await api.get<{ locks: typeof locks }>("/agents/locks");
      setLocks(data.locks || []);
    } catch {
      // Locks are optional. If the endpoint is unavailable, hide the section.
    }
  }, []);

  // Release a coordination lock (needle 338)
  const handleReleaseLock = async (lockName: string) => {
    setReleasingLock((prev) => ({ ...prev, [lockName]: true }));
    try {
      await api.delete(`/agents/locks/${encodeURIComponent(lockName)}`);
      setLocks((prev) => prev.filter((l) => l.name !== lockName));
    } catch {
      // show nothing, the lock may already be released
    } finally {
      setReleasingLock((prev) => {
        const next = { ...prev };
        delete next[lockName];
        return next;
      });
    }
  };

  // Fetch context pressure for a running agent (needle 337)
  const fetchContextPressure = useCallback(async (agentName: string) => {
    try {
      const data = await api.get<{ available: boolean; pressure_pct?: number }>(
        `/agents/${encodeURIComponent(agentName)}/context-pressure`
      );
      setContextPressure((prev) => ({ ...prev, [agentName]: data }));
    } catch {
      // Service unavailable, do not show anything
    }
  }, []);

  // Send a correction to a running agent (needle 333)
  const handleCorrection = async (agentName: string, message: string) => {
    if (!message.trim()) return;
    setNudgeSending((prev) => ({ ...prev, [agentName]: true }));
    try {
      const resp = await api.post<{ nudge: NudgeRecord }>(`/agents/${agentName}/correct`, { message });
      setNudgeSending((prev) => {
        const next = { ...prev };
        delete next[agentName];
        return next;
      });
      setNudgeHistory((prev) => ({
        ...prev,
        [agentName]: [...(prev[agentName] || []), resp.nudge],
      }));
      void fetchNudges(agentName);
    } catch (err) {
      const errorMessage =
        err instanceof ApiTimeoutError
          ? "The server did not respond in time. Correction was not sent."
          : err instanceof ApiError
            ? `Could not send correction. ${err.message}`
            : "Could not send correction. Check your connection and try again.";
      setNudgeErrors((prev) => ({ ...prev, [agentName]: errorMessage }));
    } finally {
      setNudgeSending((prev) => {
        const next = { ...prev };
        delete next[agentName];
        return next;
      });
    }
  };

  useEffect(() => {
    fetchAgents();
    fetchTemplates();
    fetchPersonaTemplates();
    fetchUserTemplates();

    // Poll for agent updates every 2 seconds so status changes
    // (running -> completed) propagate quickly without a page reload.
    const interval = setInterval(fetchAgents, 2000);
    return () => clearInterval(interval);
  }, []);

  // Clean up stale customAgentTemplates entries. Older onboarding builds
  // stuffed persona marketplace templates into this list, which made the
  // Templates page render them twice (once here with a "custom" badge,
  // once again from /agents/persona-templates). After the backend has
  // finished loading, drop any localStorage entry whose name matches a
  // backend-owned template so "custom" only ever means user-created.
  useEffect(() => {
    if (customTemplates.length === 0) return;
    const backendNames = new Set<string>();
    for (const t of templates) backendNames.add(toTitleCase(t.name).toLowerCase().trim());
    for (const t of pmTemplates) backendNames.add(t.name.toLowerCase().trim());
    for (const t of userTemplates) backendNames.add(t.name.toLowerCase().trim());
    if (backendNames.size === 0) return; // nothing loaded yet, keep cache
    const cleaned = customTemplates.filter(
      (ct) => !backendNames.has(ct.name.toLowerCase().trim())
    );
    if (cleaned.length !== customTemplates.length) {
      setCustomTemplates(cleaned);
    }
  }, [templates, pmTemplates, userTemplates, customTemplates, setCustomTemplates]);

  // Fetch rolling duration stats for the "~Nm left" pill. The backend
  // caches the computation for 60s, so refreshing every 5 minutes is
  // plenty while still picking up new samples as agents complete.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await api.get<{
          median_seconds: number;
          p75_seconds: number;
          sample_count: number;
          window_days: number;
        }>("/agents/duration-stats");
        if (!cancelled) setDurationStats(data);
      } catch {
        // Leave the previous value in place so we do not flicker back
        // to "calculating" during a transient network blip.
      }
    };
    load();
    const interval = setInterval(load, 5 * 60 * 1000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  // Fetch delegation suggestions when the Delegate tab is selected
  useEffect(() => {
    if (activeTab === "Delegate") {
      fetchDelegation();
    }
  }, [activeTab]);

  // Fetch permission requests whenever the Active tab is showing, so
  // pending approvals surface inline on each agent card instead of in a
  // separate Permissions tab.
  useEffect(() => {
    if (activeTab === "Active") {
      // Always look at pending grants for inline display.
      fetchGrants("pending");
      const interval = setInterval(() => fetchGrants("pending"), 5000);
      return () => clearInterval(interval);
    }
  }, [activeTab, fetchGrants]);

  // Fetch coordination locks when the Active tab is showing (needle 338)
  useEffect(() => {
    if (activeTab === "Active") {
      fetchLocks();
      const interval = setInterval(fetchLocks, 10000);
      return () => clearInterval(interval);
    }
  }, [activeTab, fetchLocks]);

  // Fetch context pressure for running agents (needle 337)
  useEffect(() => {
    if (activeTab === "Active") {
      const running = allAgents.filter((a) => isAgentActive(a));
      for (const agent of running) {
        fetchContextPressure(agent.name);
      }
      const interval = setInterval(() => {
        const current = allAgents.filter((a) => isAgentActive(a));
        for (const agent of current) {
          fetchContextPressure(agent.name);
        }
      }, 30000); // Check every 30 seconds
      return () => clearInterval(interval);
    }
  }, [activeTab, allAgents, fetchContextPressure]);

  // Fetch workspace messages when the Workspace tab is selected
  useEffect(() => {
    if (activeTab === "Workspace") {
      fetchWorkspace();
      const interval = setInterval(fetchWorkspace, 5000);
      return () => clearInterval(interval);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  // Listen for the dashboard "Spawn Agent" quick launch so the form
  // opens the moment the user lands on this page.
  useEffect(() => {
    const handler = () => {
      setActiveTabWithRef("Active");
      setShowNewForm(true);
    };
    window.addEventListener('myos-quick-spawn-agent', handler);
    return () => window.removeEventListener('myos-quick-spawn-agent', handler);
  }, []);

  // When ANY surface in the app spawns an agent (template card, chat
  // tool call, dashboard quick-spawn, sidebar shortcut), the api
  // wrapper bumps the agents bus. Subscribe here so the Active list
  // refreshes within milliseconds instead of waiting up to 2 seconds
  // for the next poll. Without this, "spawn roadmap" from chat would
  // not show the new row until the poll caught up. Also covers the
  // case where Tori switches away from the Agents tab and a chat-driven
  // spawn happens in the background.
  useEffect(() => {
    const unsubscribe = onAgentsChange(() => {
      fetchAgents();
    });
    return () => unsubscribe();
  }, []);

  const handleSpawn = async (name: string, prompt?: string, model?: string, budget?: number, tokenLimit?: number | null, template?: string, honorExplicitModel?: boolean) => {
    if (!name.trim()) return;
    const cleanName = name.trim();
    const cleanModel = model || "sonnet";
    // Optimistic insert: the row appears the moment the user clicks,
    // before the spawn POST returns. The next fetchAgents call
    // reconciles by replacing the placeholder with the server row
    // (matched by name). This keeps the Active tab visually instant
    // even when the spawn POST takes 100-300ms and the next poll tick
    // is 2 seconds away.
    const placeholder: AgentInfo = {
      name: cleanName,
      status: "spawned",
      source: "ui",
      model: cleanModel,
      budget: String(budget ?? 2.0),
      spawned_at: new Date().toISOString(),
      transcript_bytes: 0,
      transcript_lines: 0,
    };
    // Make sure the placeholder is not blocked by a stale dismissal.
    dismissedAgentsRef.current.delete(cleanName);
    setAllAgents((prev) => {
      // If a row with this name is already in the list (rare race),
      // do not insert a duplicate.
      if (prev.some((a) => a.name === cleanName)) return prev;
      return [placeholder, ...prev];
    });
    // Switch to the Active tab so the user sees the new row immediately
    // even if they clicked from the Templates tab.
    setActiveTabWithRef("Active");
    try {
      const spawnPayload: Record<string, unknown> = {
        name: cleanName,
        prompt: prompt || `You are a ${cleanName} agent. Do your job well.`,
        model: cleanModel,
        budget: budget ?? 2.0,
        // Every spawn initiated from the Agents UI (template Run button,
        // manual form, retry) carries source="ui". Without this the
        // backend falls back to "api", which still shows up in the Recent
        // tab, but "ui" is more precise and lets the filter keep the
        // row visible even if list_agents logic changes in the future.
        source: "ui",
      };
      if (tokenLimit && tokenLimit > 0) {
        spawnPayload.token_limit = tokenLimit;
      }
      if (template) {
        spawnPayload.template = template;
      }
      // When the user explicitly picks a model in the template detail
      // modal, the backend must respect that choice even if the
      // matching agentfile has ``LIMIT demo_mode true``. Without this
      // flag, demo_mode silently downgrades the model to Haiku and the
      // badge in the Agents list lies about which model actually ran.
      if (honorExplicitModel) {
        spawnPayload.honor_explicit_model = true;
      }
      await api.post("/agents/spawn", spawnPayload);
      setShowNewForm(false);
      setNewAgentName("");
      setEditorOpen(false);
      await fetchAgents();
    } catch {
      // Spawn failed: drop the placeholder so the user is not lied to.
      setAllAgents((prev) => prev.filter((a) => a.name !== cleanName));
    }
  };

  const [killingAgents, setKillingAgents] = useState<Record<string, boolean>>({});
  const [cancelAllPending, setCancelAllPending] = useState(false);

  const handleCancelAll = async () => {
    // Count agents eligible for bulk cancel: same filter as Active Sessions list.
    const eligible = allAgents.filter(
      (a) => isAgentActive(a) && isUserSpawnedAgent(a)
    );
    if (eligible.length === 0) return;
    const confirmed = await confirm({
      title: `Cancel all ${eligible.length} running agent${eligible.length === 1 ? "" : "s"}?`,
      message: "This cannot be undone.",
      confirmLabel: `Cancel all`,
      danger: true,
    });
    if (!confirmed) return;
    setCancelAllPending(true);
    try {
      const data = await api.post<{ cancelled: number; names: string[] }>("/agents/cancel-all", {});
      const count = data.cancelled ?? 0;
      // cancel-all is a user-initiated one-shot, not a real agent. Use a
      // unique synthetic name per click so the store's dedupe Set never
      // blocks a repeat Cancel-All.
      addNotification(
        { name: `cancel-all-${Date.now()}` },
        "running",
        count > 0
          ? `Cancelled ${count} agent${count === 1 ? "" : "s"}`
          : "No agents were running"
      );
      // Remove cancelled agents from the local list immediately.
      if (data.names && data.names.length > 0) {
        const cancelledSet = new Set(data.names);
        cancelledSet.forEach((n) => dismissedAgentsRef.current.add(n));
        setAllAgents((prev) => prev.filter((a) => !cancelledSet.has(a.name)));
      }
      // Refresh the full agent list so the UI reflects the updated statuses.
      await fetchAgents();
    } catch {
      addNotification(
        { name: `cancel-all-${Date.now()}` },
        "running",
        "Could not cancel agents. Try again."
      );
    } finally {
      setCancelAllPending(false);
    }
  };

  const handleKill = async (name: string) => {
    setKillingAgents((prev) => ({ ...prev, [name]: true }));
    try {
      // Try the new cancel endpoint first. This just marks the record as
      // cancelled so it falls out of Active Sessions, which is the right
      // call for externally managed Claude Code agents that myOS cannot
      // signal directly. If that fails (legacy 404), fall back to kill
      // which only works for in-process subprocesses.
      try {
        await api.post(`/agents/${name}/cancel`, { reason: "user cancelled" });
      } catch {
        await api.post(`/agents/${name}/kill`);
      }
    } catch {
      // Agent may already be gone, that is fine
    } finally {
      setKillingAgents((prev) => ({ ...prev, [name]: false }));
      // Dismiss so the poll never brings it back
      dismissedAgentsRef.current.add(name);
      setAllAgents((prev) => prev.filter((a) => a.name !== name));
    }
  };


  const handleRecover = async (name: string) => {
    setRecoveringAgents((prev) => ({ ...prev, [name]: true }));
    try {
      await api.post(`/agents/${name}/recover`);
      await fetchAgents();
    } catch {
      // Recovery may fail if cap is reached
    } finally {
      setRecoveringAgents((prev) => ({ ...prev, [name]: false }));
    }
  };

  // Default template icons for API templates that don't match known names
  const getTemplateIcon = (name: string) => {
    return templateIcons[name] || "smart_toy";
  };

  // Built-in templates (from API or defaults)
  type DisplayTemplate = {
    icon: string;
    name: string;
    description: string;
    model: string;
    budget: number;
    isBuiltIn: boolean;
    aliases: string[];
    capabilities: TemplateCapabilities | null;
    parseError: string | null;
    templateId?: string;
  };

  const builtInTemplates: DisplayTemplate[] =
    templates.length > 0
      ? templates
          // The server filters alias-only agentfiles (saa.agent,
          // elit.agent) and fleet-member files already. Keep this guard
          // so older backends still work: strip alias stems by name and
          // strip fleet member stems that slip through.
          .filter((t) => {
            const stem = t.name.toLowerCase();
            if (stem === "saa" || stem === "elit") return false;
            // Fleet member stems look like ``fleet-<parent>-<role>``.
            // The parent card itself (``fleet-build-website``) is shown
            // in the Fleets panel, not the templates grid, so drop any
            // ``fleet-*`` stem here.
            if (stem.startsWith("fleet-")) return false;
            return true;
          })
          .map((t) => {
            const titleName = toTitleCase(t.name);
            // Prefer the server-provided aliases list (folded in from
            // alias-only .agent files). Fall back to the static map for
            // backward compatibility.
            const aliases = (t.aliases && t.aliases.length > 0)
              ? t.aliases
              : (AGENTFILE_ALIASES[titleName] ?? []);
            return {
              icon: getTemplateIcon(t.name),
              name: titleName,
              // Never fall back to the raw agentfile body: it starts with a
              // "#" comment header and a char-count slice truncates mid-word.
              // Use the structured DESC field, or a friendly placeholder
              // when the agentfile does not set one. The card itself uses
              // CSS line-clamp-2 with a hover tooltip for long descriptions.
              description: t.description || "No description provided",
              model: "sonnet",
              budget: 2.0,
              isBuiltIn: true,
              aliases,
              capabilities: t.capabilities ?? null,
              parseError: t.parse_error ?? null,
              templateId: AGENTFILE_TEMPLATE_IDS[titleName],
            };
          })
      : [
          { icon: "search", name: "Research", description: "Search and summarize information", model: "sonnet", budget: 2.0, isBuiltIn: true, aliases: [], capabilities: null, parseError: null, templateId: "builtin-research" },
          { icon: "engineering", name: "Builder", description: "Build solutions end to end", model: "sonnet", budget: 3.0, isBuiltIn: true, aliases: ["saa"], capabilities: null, parseError: null, templateId: "builtin-builder" },
          { icon: "bug_report", name: "Diagnose", description: "Find root causes and fix bugs", model: "sonnet", budget: 2.0, isBuiltIn: true, aliases: [], capabilities: null, parseError: null, templateId: "builtin-diagnose" },
          { icon: "rate_review", name: "Review", description: "Review code for issues and improvements", model: "sonnet", budget: 2.0, isBuiltIn: true, aliases: [], capabilities: null, parseError: null, templateId: "builtin-review" },
          { icon: "science", name: "Test", description: "Run tests and report results", model: "sonnet", budget: 2.0, isBuiltIn: true, aliases: [], capabilities: null, parseError: null, templateId: "builtin-test" },
        ];

  // Merge built-in + custom templates. De-duplicate by normalized name
  // so a marketplace entry that matches a builtin doesn't appear twice.
  const builtInNames = new Set(builtInTemplates.map((b) => b.name.toLowerCase().trim()));
  const displayTemplates: DisplayTemplate[] = [
    ...builtInTemplates,
    ...customTemplates
      .filter((ct) => !builtInNames.has(ct.name.toLowerCase().trim()))
      .map((ct) => ({
        ...ct,
        isBuiltIn: false,
        aliases: [],
        capabilities: null,
        parseError: null,
      })),
  ];

  return (
    <>
      <TopBar title="Agents" />
      <div data-tour="agents" className="pt-16 px-4 pb-4 sm:pt-20 sm:p-8">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
          <div className="flex flex-wrap items-center gap-4 sm:gap-6">
            <h1 className="text-xl sm:text-2xl font-bold text-white">Agents</h1>
            <div className="flex gap-2 sm:gap-4 flex-wrap">
              {tabs.map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTabWithRef(tab)}
                  data-testid={`tab-${tab.toLowerCase()}`}
                  className={`text-sm pb-1 transition-colors ${
                    activeTab === tab
                      ? "text-blue-400 border-b-2 border-blue-400"
                      : "text-slate-400 hover:text-white"
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>
          </div>
          <Button
            variant="primary"
            size="md"
            onClick={() => setShowNewForm(!showNewForm)}
            data-testid="spawn-agent-btn"
          >
            <Icon name="add" className="text-lg" />
            New Agent
          </Button>
        </div>

        {/* New Agent Form */}
        {showNewForm && (
          <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-4 mb-6">
            <div className="flex gap-3 items-center mb-3">
              <input
                type="text"
                placeholder="Agent name (e.g. research-agent)"
                value={newAgentName}
                onChange={(e) => setNewAgentName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSpawn(newAgentName, newAgentPrompt || undefined, undefined, undefined, newAgentTokenLimit ? parseInt(newAgentTokenLimit, 10) : null);
                }}
                className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
              />
              <button
                onClick={() => handleSpawn(newAgentName, newAgentPrompt || undefined, undefined, undefined, newAgentTokenLimit ? parseInt(newAgentTokenLimit, 10) : null)}
                className="bg-blue-500 hover:bg-blue-600 text-white rounded-lg px-4 py-2 transition-colors"
              >
                Spawn
              </button>
              <button
                onClick={() => {
                  setShowNewForm(false);
                  setNewAgentName("");
                  setNewAgentPrompt("");
                  setNewAgentTokenLimit("");
                }}
                className="text-slate-400 hover:text-white transition-colors"
              >
                <Icon name="close" size={20} />
              </button>
            </div>
            {/* Prompt field with placeholder highlighting */}
            <div className="relative">
              <textarea
                value={newAgentPrompt}
                onChange={(e) => setNewAgentPrompt(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSpawn(newAgentName, newAgentPrompt || undefined, undefined, undefined, newAgentTokenLimit ? parseInt(newAgentTokenLimit, 10) : null); } }}
                rows={3}
                placeholder="What should this agent do? (Enter to spawn, Shift+Enter for newline)"
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 text-sm resize-none"
              />
              {newAgentPrompt && /\[.+?\]/.test(newAgentPrompt) && (
                <p className="text-xs text-amber-400 mt-1">
                  Replace the <span className="font-mono">[placeholders]</span> above with your specific details before spawning.
                </p>
              )}
            </div>
            {/* Token budget limit */}
            <div className="flex items-center gap-3 mt-3">
              <label className="text-sm text-slate-400 whitespace-nowrap">Usage limit</label>
              <input
                type="number"
                min={0}
                step={10000}
                value={newAgentTokenLimit}
                onChange={(e) => setNewAgentTokenLimit(e.target.value)}
                placeholder="No limit"
                className="w-40 bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500"
              />
              <span className="text-xs text-slate-500">Leave empty for unlimited</span>
            </div>
          </div>
        )}


        {/* Tab content */}
        {activeTab === "Active" && (
          <>
            {/* Coordination Locks (needle 338) */}
            {locks.length > 0 && (
              <div className="mb-4 bg-slate-900/40 border border-slate-800 rounded-xl p-4">
                <div className="flex items-center gap-2 mb-3">
                  <Icon name="lock" className="text-amber-400" size={18} />
                  <span className="text-sm font-semibold text-white">Agents working on shared items</span>
                  <span className="text-xs text-slate-500">({locks.length})</span>
                </div>
                <div className="flex flex-wrap gap-2">
                  {locks.map((lock) => (
                    <div
                      key={lock.name}
                      className="flex items-center gap-2 bg-slate-800/60 border border-amber-500/30 rounded-lg px-3 py-1.5"
                    >
                      <Icon name="lock" className="text-amber-400" size={14} />
                      <span className="text-sm text-white font-mono">{lock.name}</span>
                      {lock.holder && (
                        <span className="text-xs text-slate-400">{lock.holder}</span>
                      )}
                      <button
                        onClick={() => handleReleaseLock(lock.name)}
                        disabled={releasingLock[lock.name]}
                        className="ml-1 text-xs text-slate-400 hover:text-red-400 transition-colors disabled:opacity-50"
                        title="Unstick this"
                      >
                        {releasingLock[lock.name] ? "..." : "Release"}
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Active Sessions */}
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-white">
                Active Sessions
              </h2>
              {(() => {
                // Same filter as Active Sessions list and handleCancelAll.
                const runningCount = allAgents.filter(
                  (a) => isAgentActive(a) && isUserSpawnedAgent(a)
                ).length;
                return (
                  <button
                    data-testid="cancel-all-agents-btn"
                    onClick={handleCancelAll}
                    disabled={runningCount === 0 || cancelAllPending}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed bg-red-900/30 border border-red-700/50 text-red-400 hover:bg-red-900/50 hover:text-red-300"
                    title={runningCount === 0 ? "No background agents running" : `Cancel all ${runningCount} running agent${runningCount === 1 ? "" : "s"}`}
                  >
                    <Icon name="cancel" size={14} />
                    {cancelAllPending ? "Cancelling..." : `Cancel all${runningCount > 0 ? ` (${runningCount})` : ""}`}
                  </button>
                );
              })()}
            </div>
            {!agentsLoaded ? (
              <div
                data-testid="active-agents-loading"
                className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400 mb-8"
              >
                Loading...
              </div>
            ) : (() => {
              // Only show agents that Tori or a subagent explicitly spawned.
              // Filter uses the shared isUserSpawnedAgent + isAgentActive
              // helpers from agentUtils so the Active list, sidebar badge,
              // and Cancel-all button all count identically.
              //
              // isAgentActive treats running, spawned, and starting as
              // active, AND keeps terminated_stale rows visible if their
              // last heartbeat is fresher than 2 minutes (covers the race
              // where the stale-sweep flips the row moments before the
              // agent's next heartbeat lands). cancelled stays hidden.
              const isVisibleActive = (a: typeof allAgents[number]) =>
                isUserSpawnedAgent(a) && isAgentActive(a);

              const visibleAgents = allAgents.filter(isVisibleActive);

              // Nothing running: show the spawn CTA empty state
              if (visibleAgents.length === 0) {
                return (
                  <div className="mb-8">
                    <EmptyState
                      icon="smart_toy"
                      title="No agents running right now"
                      description="Click a template below or use the New Agent button to get started."
                      action={{ label: "Spawn your first agent", onClick: () => setShowNewForm(true) }}
                    />
                  </div>
                );
              }

              return (
                <>
                      <div className="grid grid-cols-1 gap-6 mb-8" data-testid="background-agents-list">
                        {visibleAgents.map((agent) => {
                    const isActiveExpanded = !!activeExpandedMap[agent.name];
                    const agentNudges = nudgeHistory[agent.name] || [];
                    const agentReplies = nudgeReplies[agent.name] || [];
                    const isSending = nudgeSending[agent.name] || false;
                    const nudgeError = nudgeErrors[agent.name] || "";
                    const pendingGrants = grants.filter(
                      (g) => g.status === "pending" && g.agent === agent.name
                    );

                    return (
                  <Card
                    key={agent.name}
                    variant="default"
                    padding="md"
                  >
                    <div className={`flex items-center justify-between${isActiveExpanded ? " mb-3" : ""}`}>
                      <div className="flex items-center gap-3 flex-wrap">
                        <span
                          className="flex flex-col"
                          title={agentTitleParts(agent).secondary ? `${agentTitleParts(agent).primary} (${agent.name})` : agent.name}
                        >
                          <span className="text-white font-semibold leading-tight">{agentTitleParts(agent).primary}</span>
                          {agentTitleParts(agent).secondary && (
                            <span className="text-slate-400 text-xs leading-tight">{agentTitleParts(agent).secondary}</span>
                          )}
                        </span>
                        <span className={`text-xs font-bold px-2 py-0.5 rounded ${statusColor(agent.status)}`}>
                          {statusLabel(agent.status)}
                        </span>
                        {pendingGrants.length > 0 && (
                          <span className="text-xs font-bold px-2 py-0.5 rounded bg-yellow-500/20 text-yellow-400">
                            {pendingGrants.length} pending
                          </span>
                        )}
                        <RecoveryBadge
                          recoveryCount={agent.recovery_count ?? 0}
                          maxRecoveries={agent.max_recoveries ?? 3}
                        />
                        {/* Context pressure indicator (needle 337) */}
                        {contextPressure[agent.name]?.available && contextPressure[agent.name]?.pressure_pct != null && (
                          <span
                            className={`text-xs font-bold px-2 py-0.5 rounded ${
                              (contextPressure[agent.name]?.pressure_pct ?? 0) >= 90
                                ? "bg-red-500/20 text-red-400"
                                : (contextPressure[agent.name]?.pressure_pct ?? 0) >= 70
                                  ? "bg-amber-500/20 text-amber-400"
                                  : "bg-slate-700/50 text-slate-400"
                            }`}
                            title="How much of the agent's memory is used"
                          >
                            Context: {contextPressure[agent.name]?.pressure_pct}%
                          </span>
                        )}
                        {/* One-line compact summary shown inline with the title
                            row when the card is collapsed. See
                            AgentCompactSummary for the format. */}
                        {!isActiveExpanded && (
                          <AgentCompactSummary
                            spawnedAt={agent.spawned_at || agent.timestamp}
                            budget={agent.budget}
                            model={agent.model}
                            costEstimate={agent.cost_estimate}
                            durationStats={durationStats}
                          />
                        )}
                      </div>
                      <button
                        onClick={() => toggleActiveExpanded(agent.name)}
                        className="text-slate-400 hover:text-white transition-colors flex items-center gap-1 text-sm shrink-0"
                        title={isActiveExpanded ? "Collapse session" : "Expand session"}
                        data-testid={`active-agent-toggle-${agent.name}`}
                      >
                        <Icon name={isActiveExpanded ? "expand_less" : "expand_more"} size={20} />
                        {isActiveExpanded ? "Collapse" : "Expand"}
                      </button>
                    </div>

                    {/* Everything below the title row only renders when the
                        card is expanded. Collapsed cards show just the title
                        row and the inline compact summary above. This keeps
                        the Active Sessions list scannable by default. */}
                    {isActiveExpanded && (
                      <>
                        {(agent.spawned_at || agent.timestamp) && (
                          <AgentStatusBar
                            spawnedAt={agent.spawned_at || agent.timestamp!}
                            budget={agent.budget}
                            model={agent.model}
                            transcriptBytes={agent.transcript_bytes}
                            transcriptLines={agent.transcript_lines}
                            durationStats={durationStats}
                          />
                        )}
                        <BudgetProgressBar
                          tokensUsed={agent.tokens_used ?? 0}
                          tokenLimit={agent.token_limit}
                          costEstimate={agent.cost_estimate}
                        />

                        {/* Inline pending permission requests for this agent */}
                        {pendingGrants.length > 0 && (
                          <div className="mt-3 space-y-2">
                            {pendingGrants.map((grant) => (
                              <div
                                key={grant.id}
                                className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-3 flex items-start justify-between gap-3"
                              >
                                <div className="flex items-start gap-2 flex-1 min-w-0">
                                  <Icon name="lock_open" className="text-yellow-400 mt-0.5" size={18} />
                                  <div className="flex-1 min-w-0">
                                    <p className="text-sm text-white">
                                      Wants to {grant.type === "secret" ? "access a stored password" : grant.type === "file_access" ? "read a file" : grant.type === "tool" ? "use a tool" : grant.type === "budget" ? "increase its spending limit" : grant.type === "model_upgrade" ? "use a more powerful AI model" : grant.type}: <span className="font-mono text-yellow-300">{grant.target}</span>
                                    </p>
                                    {grant.detail && (
                                      <p className="text-xs text-slate-400 mt-1 break-words">{grant.detail}</p>
                                    )}
                                  </div>
                                </div>
                                <div className="flex gap-1 shrink-0">
                                  <button
                                    onClick={() => handleApproveGrant(grant.id)}
                                    disabled={grantActioning[grant.id]}
                                    className="bg-green-600 hover:bg-green-700 disabled:bg-slate-700 disabled:text-slate-500 text-white text-xs rounded px-2.5 py-1 transition-colors"
                                  >
                                    {grantActioning[grant.id] ? "..." : "Approve"}
                                  </button>
                                  <button
                                    onClick={() => handleDenyGrant(grant.id)}
                                    disabled={grantActioning[grant.id]}
                                    className="border border-slate-700 text-slate-300 text-xs rounded px-2.5 py-1 hover:border-red-500 hover:text-red-400 disabled:opacity-50 transition-colors"
                                  >
                                    Deny
                                  </button>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}

                        {/* Real chat thread for this agent. Renders nudges as
                            right aligned user bubbles and replies as left
                            aligned assistant bubbles with markdown, using the
                            same look as the main ChatPanel. Replaces the old
                            custom monospace "You:" text lines. See needle 244. */}
                        <AgentChatThread
                          agentName={agent.name}
                          nudges={agentNudges.map((n) => ({
                            message: n.message,
                            timestamp: n.timestamp,
                            delivery: n.delivery,
                            delivery_message:
                              n.delivery_message ||
                              (n.stdin_delivered ? "Delivered to agent stdin" : undefined),
                          }))}
                          replies={agentReplies.map((r) => ({
                            message: r.message,
                            timestamp: r.timestamp,
                          }))}
                          onSend={(message) => handleNudge(agent.name, message)}
                          onCorrect={(message) => handleCorrection(agent.name, message)}
                          isSending={isSending}
                          errorMessage={nudgeError || null}
                          agentRegisteredAt={agent.spawned_at || agent.timestamp}
                        />

                        {/* Extra details and memory live inside the expanded
                            card. They were previously gated behind a second
                            "expandedAgent" toggle, but that UX is gone now
                            that the whole detail view is collapsible. */}
                        <div className="mt-4 pt-4 border-t border-slate-800">
                          <div className="grid grid-cols-3 gap-4 text-xs mb-4">
                            <div>
                              <span className="text-slate-500">Source</span>
                              <p className="text-white mt-1">{agent.source}</p>
                            </div>
                            {agent.budget && (
                              <div>
                                <span className="text-slate-500">Budget</span>
                                <p className="text-white mt-1">${agent.budget}</p>
                              </div>
                            )}
                            <div>
                              <span className="text-slate-500">Messages sent</span>
                              <p className="text-white mt-1">{agentNudges.length}</p>
                            </div>
                          </div>
                          <AgentMemorySection
                            agentName={agent.name}
                            memory={agentMemory[agent.name]}
                            clearing={memoryClearing[agent.name] || false}
                            onClear={() => handleClearMemory(agent.name)}
                          />
                        </div>

                        <div className="flex items-center justify-between mt-4">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => openTranscript(agent.name)}
                            className="text-blue-400 hover:text-blue-300"
                          >
                            <Icon name="description" size={16} />
                            View Transcript
                          </Button>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => handleKill(agent.name)}
                            disabled={killingAgents[agent.name]}
                            className="hover:border-red-500 hover:text-red-400"
                          >
                            {killingAgents[agent.name] ? "Cancelling..." : "Cancel"}
                          </Button>
                        </div>
                      </>
                    )}
                  </Card>
                    );
                  })}
                      </div>
                </>
              );
            })()}
          </>
        )}

        {activeTab === "Delegate" && (
          <>
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  Hand Off Tasks
                  <div className="group relative">
                    <Icon name="help_outline" size={16} className="text-slate-500 hover:text-slate-300 cursor-help" />
                    <div className="absolute left-0 top-full mt-1 w-72 bg-slate-800 border border-slate-700 rounded-lg p-3 text-xs text-slate-300 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-lg z-10">
                      These tasks are open and unblocked. They are good candidates for an AI agent to handle while you focus on other work. Clicking "Hand off" spawns an agent that will read the task, do the work, and report back when done.
                    </div>
                  </div>
                </h2>
                <p className="text-sm text-slate-400 mt-1">
                  Open tasks an agent can pick up for you. Click "Hand off" to start one working on it now.
                </p>
              </div>
              <button
                onClick={fetchDelegation}
                disabled={delegationLoading}
                className="text-sm text-blue-400 hover:text-blue-300 transition-colors flex items-center gap-1 disabled:opacity-50"
              >
                <Icon name="refresh" size={18} />
                Refresh
              </button>
            </div>

            {delegationLoading && (
              <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400 mb-6">
                Loading suggestions...
              </div>
            )}

            {delegationError && (
              <div className="bg-slate-900/40 border border-red-900/30 rounded-xl p-8 text-center text-slate-400 mb-6">
                {delegationError}
              </div>
            )}

            {!delegationLoading && !delegationError && delegationData && (
              <>
                {/* Delegation targets */}
                {delegationData.delegation_targets.length === 0 ? (
                  <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400 mb-6">
                    No tasks to delegate right now. As you add more tasks, delegation suggestions will appear here.
                  </div>
                ) : (
                  <div className="flex flex-col gap-3 mb-6">
                    {delegationData.delegation_targets.map((target) => (
                      <div
                        key={target.id}
                        className="bg-slate-900/40 border border-slate-800 rounded-xl p-5 flex items-center justify-between hover:border-slate-700 transition-colors"
                      >
                        <div className="flex items-center gap-4">
                          <div className="w-10 h-10 rounded-lg bg-pink-500/10 border border-pink-500/20 flex items-center justify-center">
                            <Icon name="smart_toy" className="text-pink-400" size={22} />
                          </div>
                          <div>
                            <div className="flex items-center gap-2">
                              <span className="text-white font-mono text-sm font-semibold">{target.id}</span>
                              <span className="text-white font-medium">{target.title}</span>
                            </div>
                            <p className="text-slate-400 text-xs mt-1">
                              Open and unblocked. An agent can work on this now.
                            </p>
                          </div>
                        </div>
                        <button
                          onClick={() => handleDelegateSpawn(target)}
                          disabled={spawningDelegation[target.id]}
                          className="bg-pink-500 hover:bg-pink-600 disabled:bg-slate-700 disabled:text-slate-500 text-white rounded-lg px-4 py-2 text-sm transition-colors flex items-center gap-2"
                        >
                          <Icon name="bolt" size={16} />
                          {spawningDelegation[target.id] ? "Starting..." : "Hand off"}
                        </button>
                      </div>
                    ))}
                  </div>
                )}

              </>
            )}
          </>
        )}

        {activeTab === "Permissions" && (
          <>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-white">
                Permission Requests
              </h2>
              <div className="flex gap-2">
                {(["pending", "granted", "denied"] as const).map((f) => (
                  <button
                    key={f}
                    onClick={() => setGrantFilter(f)}
                    className={`text-xs px-3 py-1 rounded-full transition-colors ${
                      grantFilter === f
                        ? "bg-blue-500/20 text-blue-400 border border-blue-500/50"
                        : "bg-slate-800 text-slate-400 border border-slate-700 hover:text-white"
                    }`}
                  >
                    {f === "pending" ? "Waiting" : f === "granted" ? "Approved" : "Denied"}
                  </button>
                ))}
              </div>
            </div>
            <p className="text-sm text-slate-400 mb-6">
              When agents need extra access (like reading a file, using a tool, or changing their budget), their requests show up here for you to approve or deny.
            </p>
            {grantsLoading && grants.length === 0 ? (
              <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400">
                Loading...
              </div>
            ) : grants.filter((g) => g.agent && g.agent !== "unknown").length === 0 ? (
              <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400">
                {grantFilter === "pending"
                  ? "No pending requests. Agents will ask for permission here when they need extra access."
                  : grantFilter === "granted"
                  ? "No approved requests yet."
                  : "No denied requests yet."}
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {grants.filter((g) => g.agent && g.agent !== "unknown").map((grant) => (
                  <div
                    key={grant.id}
                    data-testid="grant-card"
                    className="bg-slate-900/40 border border-slate-800 rounded-xl p-5"
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="flex items-center gap-3 mb-2">
                          <Icon
                            name={
                              grant.type === "file_access"
                                ? "folder_open"
                                : grant.type === "tool"
                                ? "build"
                                : grant.type === "budget"
                                ? "payments"
                                : grant.type === "secret"
                                ? "key"
                                : grant.type === "model_upgrade"
                                ? "upgrade"
                                : "lock_open"
                            }
                            className="text-xl text-slate-400"
                          />
                          <span className="text-white font-medium">
                            {grant.type === "file_access"
                              ? "File access"
                              : grant.type === "tool"
                              ? "Tool usage"
                              : grant.type === "budget"
                              ? "Budget increase"
                              : grant.type === "secret"
                              ? "Secret access"
                              : grant.type === "model_upgrade"
                              ? "Model upgrade"
                              : grant.type}
                          </span>
                          <span className={`text-xs font-bold px-2 py-0.5 rounded ${
                            grant.status === "pending"
                              ? "bg-yellow-500/20 text-yellow-400"
                              : grant.status === "granted"
                              ? "bg-green-500/20 text-green-400"
                              : "bg-red-500/20 text-red-400"
                          }`}>
                            {grant.status === "pending" ? "WAITING" : grant.status === "granted" ? "APPROVED" : "DENIED"}
                          </span>
                        </div>
                        <div className="text-sm text-slate-300 mb-1">
                          <span className="text-slate-500">Agent:</span> {grant.agent}
                        </div>
                        <div className="text-sm text-slate-300 mb-1">
                          <span className="text-slate-500">Requesting:</span> {grant.target}
                        </div>
                        {grant.detail && (
                          <div className="text-xs text-slate-500 mt-1">{grant.detail}</div>
                        )}
                        {grant.requested_at && (
                          <div className="text-xs text-slate-500 mt-1">
                            {new Date(grant.requested_at).toLocaleString()}
                          </div>
                        )}
                      </div>
                      {grant.status === "pending" && (
                        <div className="flex gap-2 ml-4 shrink-0">
                          <button
                            onClick={() => handleApproveGrant(grant.id)}
                            disabled={grantActioning[grant.id]}
                            className="bg-green-600 hover:bg-green-700 disabled:bg-slate-700 disabled:text-slate-500 text-white text-sm rounded-lg px-4 py-2 transition-colors flex items-center gap-1"
                          >
                            <Icon name="check" size={16} />
                            {grantActioning[grant.id] ? "..." : "Approve"}
                          </button>
                          <button
                            onClick={() => handleDenyGrant(grant.id)}
                            disabled={grantActioning[grant.id]}
                            className="border border-slate-700 text-slate-300 text-sm rounded-lg px-4 py-2 hover:border-red-500 hover:text-red-400 disabled:opacity-50 transition-colors flex items-center gap-1"
                          >
                            <Icon name="close" size={16} />
                            {grantActioning[grant.id] ? "..." : "Deny"}
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {activeTab === "Recent" && (() => {
          const terminalStatuses = TERMINAL_AGENT_STATUSES as readonly string[];
          // Statuses the "Hiding cancelled" chip hides from the Recent
          // tab by default. ``failed`` and ``completed`` are intentionally
          // NOT here so genuine successes and failures are always
          // visible, even when the chip is set to hide cancellations.
          // The chip toggles cancellation-synonyms only.
          const inactiveStatuses = CANCELLED_SYNONYM_STATUSES as readonly string[];
          const allRecent = allAgents
            .filter((a) => terminalStatuses.includes(a.status) && isUserSpawnedAgent(a))
            .sort((a, b) => {
              const ta = a.spawned_at || a.timestamp || "";
              const tb = b.spawned_at || b.timestamp || "";
              return tb.localeCompare(ta);
            })
            .slice(0, 20);
          const recentAgents = showInactive
            ? allRecent
            : allRecent.filter((a) => !inactiveStatuses.includes(a.status));
          const hiddenCount = allRecent.length - allRecent.filter((a) => !inactiveStatuses.includes(a.status)).length;
          const canRecover = (a: AgentInfo) =>
            ["failed", "terminated_stale", "abandoned", "cancelled", "stopped"].includes(a.status)
            && (a.recovery_count ?? 0) < (a.max_recoveries ?? 3);
          return (
            <>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-white">
                  Recent Agents
                </h2>
                {(hiddenCount > 0 || showInactive) && (
                  <button
                    type="button"
                    onClick={toggleShowInactive}
                    data-testid="recent-cancelled-toggle"
                    className="inline-flex items-center gap-2 text-xs text-slate-300 bg-slate-800/70 hover:bg-slate-700/70 border border-slate-700 rounded-full px-3 py-1 transition-colors"
                  >
                    {showInactive ? (
                      <>
                        <span>Showing cancelled</span>
                        <span className="text-slate-500">.</span>
                        <span className="text-sky-400">hide</span>
                      </>
                    ) : (
                      <>
                        <span>Hiding cancelled</span>
                        <span className="text-slate-500">.</span>
                        <span className="text-sky-400">show</span>
                      </>
                    )}
                  </button>
                )}
              </div>
              {!agentsLoaded ? (
                <div
                  data-testid="recent-agents-loading"
                  className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400 mb-8"
                >
                  Loading...
                </div>
              ) : recentAgents.length === 0 ? (
                <div className="mb-8">
                  <EmptyState
                    icon="smart_toy"
                    title="No agents have run yet"
                    description='Try asking myOS in chat to do something, like "help me plan my week" or "write a status update."'
                  />
                </div>
              ) : (
                <div className="flex flex-col gap-2 mb-8">
                  {recentAgents.map((agent) => {
                    const isRecentExpanded = expandedAgent === agent.name;
                    const isInactive = inactiveStatuses.includes(agent.status);
                    return (
                      <div
                        key={agent.name}
                        className={`bg-slate-900/40 border border-slate-800 rounded-xl px-5 py-3${showInactive && isInactive ? " opacity-50" : ""}`}
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-3">
                            <span
                              className="flex flex-col"
                              title={agentTitleParts(agent).secondary ? `${agentTitleParts(agent).primary} (${agent.name})` : agent.name}
                            >
                              <span className="text-white font-medium leading-tight">{agentTitleParts(agent).primary}</span>
                              {agentTitleParts(agent).secondary && (
                                <span className="text-slate-400 text-xs leading-tight">{agentTitleParts(agent).secondary}</span>
                              )}
                            </span>
                            <span
                              className={`text-xs font-bold px-2 py-0.5 rounded ${statusColor(agent.status)}`}
                            >
                              {statusLabel(agent.status)}
                            </span>
                            <RecoveryBadge
                              recoveryCount={agent.recovery_count ?? 0}
                              maxRecoveries={agent.max_recoveries ?? 3}
                            />
                          </div>
                          <div className="flex items-center gap-4">
                            {agent.model && (
                              <span className="text-slate-500 text-xs">{agent.model}</span>
                            )}
                            {(agent.spawned_at || agent.timestamp) && (
                              <span className="text-slate-500 text-sm">
                                {new Date(agent.spawned_at || agent.timestamp!).toLocaleString()}
                              </span>
                            )}
                            {canRecover(agent) && (
                              <button
                                onClick={() => handleRecover(agent.name)}
                                disabled={recoveringAgents[agent.name]}
                                className="text-xs text-yellow-400 hover:text-yellow-300 disabled:opacity-50 transition-colors flex items-center gap-1"
                              >
                                <Icon name="replay" size={14} />
                                {recoveringAgents[agent.name] ? "Recovering..." : "Recover"}
                              </button>
                            )}
                            <button
                              onClick={async () => {
                                try {
                                  await api.delete(`/agents/${agent.name}`);
                                } catch {
                                  // may fail if agent is still running
                                }
                                dismissedAgentsRef.current.add(agent.name);
                                setAllAgents((prev) => prev.filter((a) => a.name !== agent.name));
                              }}
                              className="text-slate-600 hover:text-red-400 transition-colors text-xs flex items-center gap-1"
                              title="Remove from history"
                            >
                              <Icon name="close" size={14} />
                            </button>
                            <button
                              onClick={() => {
                                if (!isRecentExpanded) fetchMemory(agent.name);
                                setExpandedAgent(isRecentExpanded ? null : agent.name);
                              }}
                              className="text-slate-500 hover:text-slate-300 transition-colors text-xs flex items-center gap-1"
                            >
                              <Icon name={isRecentExpanded ? "expand_less" : "memory"} size={16} />
                              {isRecentExpanded ? "Hide" : "Memory"}
                            </button>
                          </div>
                        </div>
                        {isRecentExpanded && (
                          <div className="mt-3 pt-3 border-t border-slate-800">
                            <AgentMemorySection
                              agentName={agent.name}
                              memory={agentMemory[agent.name]}
                              clearing={memoryClearing[agent.name] || false}
                              onClear={() => handleClearMemory(agent.name)}
                            />
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          );
        })()}

        {activeTab === "Workspace" && (
          <>
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  Shared Workspace
                  {workspaceMessages.length > 0 && (
                    <span className="text-xs font-bold px-2 py-0.5 rounded bg-blue-500/20 text-blue-400">
                      {workspaceMessages.length}
                    </span>
                  )}
                  <div className="group relative">
                    <Icon name="help_outline" size={16} className="text-slate-500 hover:text-slate-300 cursor-help" />
                    <div className="absolute left-0 top-full mt-1 w-80 bg-slate-800 border border-slate-700 rounded-lg p-3 text-xs text-slate-300 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-lg z-10">
                      <p className="font-semibold text-white mb-1">How agents share context</p>
                      <p className="mb-2">When you run multiple agents on related tasks, they can leave notes here (findings, questions, and results) so other agents don't duplicate work.</p>
                      <p>Every new agent you spawn automatically receives everything in this workspace as context. Clear it when you start a new project or when the notes are no longer relevant.</p>
                    </div>
                  </div>
                </h2>
                <p className="text-sm text-slate-400 mt-1">
                  Notes left by agents for each other. New agents see this automatically when they start.
                </p>
              </div>
              <div className="flex gap-3">
                <button
                  onClick={fetchWorkspace}
                  disabled={workspaceLoading}
                  className="text-sm text-blue-400 hover:text-blue-300 transition-colors flex items-center gap-1 disabled:opacity-50"
                >
                  <Icon name="refresh" size={18} />
                  Refresh
                </button>
                <button
                  onClick={clearWorkspace}
                  disabled={workspaceClearing || workspaceMessages.length === 0}
                  className="text-sm text-red-400 hover:text-red-300 transition-colors flex items-center gap-1 disabled:opacity-50"
                >
                  <Icon name="delete_sweep" size={18} />
                  {workspaceClearing ? "Clearing..." : "Clear workspace"}
                </button>
              </div>
            </div>

            {workspaceLoading && workspaceMessages.length === 0 ? (
              <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400">
                Loading...
              </div>
            ) : workspaceMessages.length === 0 ? (
              <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400">
                No messages yet. Agents post findings here so other agents can pick them up.
              </div>
            ) : (() => {
              const byAgent: Record<string, WorkspaceMessage[]> = {};
              for (const msg of workspaceMessages) {
                if (!byAgent[msg.agent_name]) byAgent[msg.agent_name] = [];
                byAgent[msg.agent_name].push(msg);
              }
              const typeColor = (t: string) => {
                switch (t) {
                  case "finding": return "bg-yellow-500/20 text-yellow-400";
                  case "question": return "bg-purple-500/20 text-purple-400";
                  case "result": return "bg-green-500/20 text-green-400";
                  case "context": return "bg-blue-500/20 text-blue-400";
                  default: return "bg-slate-500/20 text-slate-400";
                }
              };
              return (
                <div className="flex flex-col gap-4">
                  {Object.entries(byAgent).map(([agentName, msgs]) => (
                    <div key={agentName} className="bg-slate-900/40 border border-slate-800 rounded-xl p-5">
                      <div className="flex items-center gap-2 mb-3">
                        <Icon name="smart_toy" className="text-pink-400" size={18} />
                        <span className="text-white font-semibold">{agentName}</span>
                        <span className="text-xs text-slate-500">{msgs.length} message{msgs.length !== 1 ? "s" : ""}</span>
                      </div>
                      <div className="flex flex-col gap-2">
                        {msgs.map((msg) => (
                          <div key={msg.id} className="flex items-start gap-3 bg-slate-950/50 rounded-lg px-3 py-2">
                            <span className={"text-xs font-bold px-2 py-0.5 rounded shrink-0 mt-0.5 " + typeColor(msg.message_type)}>
                              {msg.message_type.toUpperCase()}
                            </span>
                            <div className="flex-1 min-w-0">
                              <p className="text-slate-200 text-sm">{msg.content}</p>
                              <p className="text-slate-600 text-xs mt-1">
                                {new Date(msg.timestamp).toLocaleString()}
                              </p>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              );
            })()}
          </>
        )}

        {activeTab === "Templates" && <div className="mt-8 border-t border-slate-800 pt-8">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white" data-testid="agent-templates-heading">
            Agent Templates
          </h2>
          <button
            onClick={() => {
              setEditorInitial(null);
              setEditorIsNew(true);
              setEditorBuiltInName(null);
              setEditorPromptTemplate(undefined);
              setEditorTemplateId(undefined);
              setEditorSource(undefined);
              setEditorAliases(undefined);
              setEditorCapabilities(null);
              setEditorOpen(true);
            }}
            className="text-sm text-blue-400 hover:text-blue-300 transition-colors flex items-center gap-1"
          >
            <Icon name="add" size={18} />
            New template
          </button>
        </div>
        {/* Unified installed templates: agentfile builtins + persona built-ins + user custom */}
        {!currentPersona && (
          <p className="text-slate-500 text-xs mb-3" data-testid="persona-hint">
            Set your role in Settings to see tailored templates.
          </p>
        )}
        {displayTemplates.length === 0 && pmTemplates.length === 0 && userTemplates.length === 0 ? (
          <div className="border border-dashed border-slate-700 rounded-xl p-10 text-center mb-8" data-testid="agent-templates-empty">
            <Icon name="storefront" className="text-4xl text-slate-600 mb-3" />
            <p className="text-slate-400 text-sm font-medium">You have no templates yet</p>
            <p className="text-slate-500 text-xs mt-1">Browse the Marketplace below to add your first template.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 mb-8" data-testid="installed-templates-grid">
            {displayTemplates.map((tpl) => (
              <TemplateCard
                key={tpl.name}
                name={tpl.name}
                description={tpl.description}
                icon={tpl.icon}
                aliases={tpl.aliases}
                source={tpl.isBuiltIn ? "builtin" : "custom"}
                installed={true}
                onAction={tpl.parseError == null ? () => {
                  const rawContent = tpl.isBuiltIn
                    ? (templates.find(
                        (t) => toTitleCase(t.name) === tpl.name || t.name === tpl.name
                      )?.content ?? "")
                    : "";
                  const promptMatch = rawContent.match(/^PROMPT\s+"(.+)"/m);
                  const extractedPrompt = promptMatch ? promptMatch[1] : tpl.description;
                  setEditorInitial({ name: tpl.name, description: tpl.description, icon: tpl.icon, model: tpl.model, budget: tpl.budget });
                  setEditorIsNew(false);
                  setEditorBuiltInName(tpl.isBuiltIn ? tpl.name : null);
                  setEditorPromptTemplate(extractedPrompt || undefined);
                  setEditorTemplateId(tpl.templateId);
                  setEditorSource(tpl.isBuiltIn ? "builtin" : "custom");
                  setEditorAliases(tpl.aliases);
                  setEditorCapabilities(tpl.capabilities ?? null);
                  setEditorOpen(true);
                } : undefined}
                actionLabel="Use"
                onDelete={!tpl.isBuiltIn ? () => deleteCustomTemplate(tpl.name) : undefined}
                capabilities={tpl.capabilities ?? null}
                parseError={tpl.parseError ?? null}
                testId={`template-card-${tpl.name}`}
              />
            ))}
            {/* Persona-specific built-ins from /agents/persona-templates
                De-dup: skip any template whose name already appears in displayTemplates */}
            {pmTemplates
              .filter((tpl) => !builtInNames.has(tpl.name.toLowerCase().trim()))
              .map((tpl) => (
                <TemplateCard
                  key={`persona-builtin-${tpl.id}`}
                  name={tpl.name}
                  description={tpl.description}
                  icon={tpl.icon}
                  // /agents/persona-templates only returns builtin or
                  // marketplace entries. Never label them "custom" even
                  // if the backend forgets to set source.
                  source={tpl.source ?? (tpl.builtin ? "builtin" : "marketplace")}
                  installed={true}
                  onAction={() => handleUsePmTemplate(tpl)}
                  actionLabel="Use"
                  onDelete={!tpl.builtin ? () => handleDeletePmTemplate(tpl.id) : undefined}
                  testId={`pm-template-card-${tpl.id}`}
                />
              ))}
            {/* User-created templates from /agents/user-templates (persona-agnostic)
                De-dup: skip any template whose name already appears in displayTemplates or pmTemplates */}
            {userTemplates
              .filter((tpl) => {
                const key = tpl.name.toLowerCase().trim();
                return (
                  !builtInNames.has(key) &&
                  !pmTemplates.some((p) => p.name.toLowerCase().trim() === key)
                );
              })
              .map((tpl) => (
                <TemplateCard
                  key={`user-${tpl.id}`}
                  name={tpl.name}
                  description={tpl.description}
                  icon={tpl.icon}
                  source="custom"
                  installed={true}
                  onAction={() => handleUsePmTemplate(tpl)}
                  actionLabel="Use"
                  onDelete={() => handleDeletePmTemplate(tpl.id)}
                  testId={`user-template-card-${tpl.id}`}
                />
              ))}
          </div>
        )}

        {/* PM template editor modal */}
        {pmTemplateEditor.open && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
            <div className="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-lg p-6 shadow-xl">
              <h3 className="text-lg font-semibold text-white mb-4">
                {pmTemplateEditor.isNew ? "New Template" : "Edit Template"}
              </h3>
              <PMTemplateEditorForm
                initial={pmTemplateEditor.template}
                saving={pmTemplateSaving}
                onSave={handleSavePmTemplate}
                onCancel={() => setPmTemplateEditor({ open: false, template: null, isNew: false })}
              />
            </div>
          </div>
        )}

        {/* Fleets panel removed: fleet launching has been folded into the
            Plans page template grid. The backend /agents/fleets/spawn
            endpoint stays alive for backwards compatibility. */}

        {/* Marketplace section (always visible) */}
        <div className="mt-8 bg-slate-900/40 border border-slate-800 rounded-xl p-6" data-testid="marketplace-section">
          <h2 className="text-lg font-semibold text-white mb-4" data-testid="marketplace-heading">Marketplace</h2>
          {AGENT_MARKETPLACE.map((cat) => (
            <div key={cat.id} className="mb-6 last:mb-0">
              <h4 className="text-sm text-slate-400 font-medium mb-3 uppercase tracking-wider">{cat.category}</h4>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {cat.templates.flatMap((mt) => {
                  const alreadyAdded = isCustomTemplate(mt.name) || builtInTemplates.some(
                    (b) => b.name.toLowerCase().trim() === mt.name.toLowerCase().trim()
                  ) || pmTemplates.some(
                    (p) => p.name.toLowerCase().trim() === mt.name.toLowerCase().trim()
                  ) || userTemplates.some(
                    (u) => u.name.toLowerCase().trim() === mt.name.toLowerCase().trim()
                  );
                  // Skip templates already shown in the installed grid above.
                  if (alreadyAdded) return [];
                  return [
                    <TemplateCard
                      key={mt.name}
                      name={mt.name}
                      description={mt.description}
                      icon={mt.icon}
                      source="marketplace"
                      installed={false}
                      onAction={() => addCustomTemplate(mt)}
                      testId={`marketplace-card-${mt.name}`}
                    />
                  ];
                })}
              </div>
            </div>
          ))}
        </div>

        </div>}

        {/* Agent Configurations tab */}
        {activeTab === "Agentfiles" && (
          <AgentfilesTab onLaunch={fetchAgents} />
        )}

        {/* Template Editor Modal */}
        {editorOpen && (
          <TemplateEditorModal
            initial={editorInitial}
            isNew={editorIsNew}
            promptTemplate={editorPromptTemplate}
            templateId={editorTemplateId}
            source={editorSource}
            aliases={editorAliases}
            capabilities={editorCapabilities}
            onSpawn={(t, userMessage) => {
              // If the user typed a message, use it as the prompt. Otherwise
              // fall back to the template description so the agent has context.
              const prompt = userMessage.trim() || t.description;
              // The detail modal is the only path where the user has
              // seen the model picker and confirmed it. Pass the
              // honor_explicit_model flag so demo_mode templates
              // (Roadmap, etc.) respect the chosen model instead of
              // silently reverting to Haiku.
              handleSpawn(
                t.name.toLowerCase().replace(/\s+/g, "-"),
                prompt,
                t.model,
                t.budget,
                null,
                editorBuiltInName || undefined,
                true,
              );
              setEditorOpen(false);
            }}
            onSave={(t) => {
              addCustomTemplate(t);
              setEditorOpen(false);
            }}
            onCancel={() => setEditorOpen(false)}
            onDescriptionSaved={() => {
              // Refresh both persona-backed and user-created template lists so
              // the card under the modal picks up the new description. Custom
              // templates come from fetchUserTemplates, shipped ones from
              // fetchPersonaTemplates, so we run both to be safe.
              fetchPersonaTemplates();
              fetchUserTemplates();
            }}
          />
        )}
      </div>

      {/* Transcript viewer modal */}
      {transcriptModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onClick={(e) => { if (e.target === e.currentTarget) setTranscriptModal(null); }}
        >
          <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-3xl max-h-[85vh] flex flex-col shadow-2xl">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
              <div className="flex items-center gap-2 min-w-0">
                <Icon name="description" className="text-blue-400" size={20} />
                <span className="text-white font-semibold truncate">{transcriptModal.name}</span>
                <span className="text-xs text-slate-500">transcript</span>
              </div>
              <button
                onClick={() => setTranscriptModal(null)}
                className="text-slate-500 hover:text-white transition-colors"
                aria-label="Close transcript"
              >
                <Icon name="close" size={20} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-6 py-4">
              {transcriptModal.loading ? (
                <div className="flex items-center gap-2 text-slate-400 text-sm" role="status" aria-label="Loading transcript">
                  <div className="w-4 h-4 border-2 border-slate-600 border-t-blue-400 rounded-full animate-spin" />
                  Loading transcript...
                </div>
              ) : transcriptModal.error ? (
                <div className="space-y-3" data-testid="transcript-error">
                  <p className="text-slate-400 text-sm">{transcriptModal.error}</p>
                  {transcriptModal.retryable && (
                    <button
                      onClick={() => openTranscript(transcriptModal.name)}
                      className="px-3 py-1.5 text-xs bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors"
                    >
                      Retry
                    </button>
                  )}
                </div>
              ) : (
                <TranscriptContent content={transcriptModal.content} />
              )}
            </div>
          </div>
        </div>
      )}
      <ConfirmModal {...confirmProps} />
    </>
  );
}
