import { useState, useEffect, useRef, useCallback } from "react";
import TopBar from "../components/TopBar";
import Icon from "../components/Icon";
import { AgentChatThread } from "../components/AgentChatThread";
import { api, ApiError, ApiTimeoutError } from "../lib/api";
import { useNotificationStore } from "../stores/notifications";
import { useAppStore, type CustomAgentTemplate } from "../stores/app";

const BASE_TABS = ["Active", "Recent", "Insights", "Templates"];

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

interface AutoTemplate { id: string; name: string; description: string; icon: string; steps: { name: string; prompt: string }[] }

function AutomationTemplatesList() {
  const [templates, setTemplates] = useState<AutoTemplate[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [running, setRunning] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ templates: AutoTemplate[] }>('/workflows/templates')
      .then((res) => setTemplates(res.templates ?? []))
      .catch(() => {});
  }, []);

  const runTemplate = async (t: AutoTemplate) => {
    setRunning(t.id);
    try {
      await api.post('/workflows', {
        name: t.name,
        steps: t.steps.map((s, i) => ({
          id: `step-${i}`,
          agent_name: s.name.toLowerCase().replace(/\s+/g, '-'),
          prompt: s.prompt,
          model: 'sonnet',
          budget: 2.0,
          depends_on: i > 0 ? [`step-${i - 1}`] : [],
        })),
      });
    } catch (e) {
      console.error('Failed to run workflow:', e);
    } finally {
      setRunning(null);
    }
  };

  if (templates.length === 0) return <p className="text-sm text-slate-500">Loading templates...</p>;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {templates.map((t) => {
        const isExpanded = expanded === t.id;
        return (
          <div key={t.id} className="bg-slate-900/40 border border-slate-800 rounded-xl p-4">
            <div className="flex items-start gap-3 cursor-pointer" onClick={() => setExpanded(isExpanded ? null : t.id)}>
              <Icon name={t.icon} className="text-2xl text-blue-400 mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-white text-sm font-medium">{t.name}</p>
                <p className="text-slate-400 text-xs mt-0.5">{t.description}</p>
                <p className="text-slate-500 text-[10px] mt-1">{t.steps.length} steps</p>
              </div>
              <Icon name={isExpanded ? 'expand_less' : 'expand_more'} className="text-slate-500 shrink-0" size={20} />
            </div>
            {isExpanded && (
              <div className="mt-3 pt-3 border-t border-slate-700/50 space-y-2">
                {t.steps.map((s, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs text-slate-400">
                    <span className="w-5 h-5 rounded-full bg-slate-700 flex items-center justify-center text-[10px] text-slate-300 shrink-0">{i + 1}</span>
                    {s.name}
                  </div>
                ))}
                <button
                  onClick={() => runTemplate(t)}
                  disabled={running === t.id}
                  className="w-full mt-2 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg text-sm font-medium transition-colors"
                >
                  {running === t.id ? 'Running...' : 'Run this workflow'}
                </button>
              </div>
            )}
          </div>
        );
      })}
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

  if (loading) return <p className="text-sm text-slate-500">Loading agent configs...</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h3 className="text-white font-semibold">Agent Configurations</h3>
          <p className="text-xs text-slate-500 mt-1">Ready-made agent setups you can start with one click</p>
        </div>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="flex items-center gap-2 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition-colors"
        >
          <Icon name={showCreate ? "close" : "add"} className="text-base" />
          {showCreate ? "Cancel" : "New Config"}
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
              placeholder="What does this agent do?"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500"
            />
          </div>
          <div className="mb-4">
            <label className="block text-sm text-slate-400 mb-1">Prompt</label>
            <textarea
              value={createForm.prompt}
              onChange={(e) => setCreateForm({ ...createForm, prompt: e.target.value })}
              rows={3}
              placeholder="The instructions this agent follows when it runs..."
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 resize-none"
            />
          </div>
          <button
            onClick={handleCreate}
            disabled={creating || !createForm.name.trim() || !createForm.prompt.trim()}
            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg px-4 py-2 text-sm font-medium transition-colors"
          >
            {creating ? "Creating..." : "Create Config"}
          </button>
        </div>
      )}

      {/* Agentfile cards */}
      {agentfiles.length === 0 ? (
        <div className="text-center py-12">
          <Icon name="description" className="text-4xl text-slate-600 mb-3" />
          <p className="text-slate-400 text-sm">No agent configurations found</p>
          <p className="text-slate-500 text-xs mt-1">Create one above to get started.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {agentfiles.map((af) => {
            const isExpanded = expanded === af.name;
            const icon = AGENTFILE_ICONS[af.name] || "smart_toy";
            return (
              <div key={af.name} className="bg-slate-900/40 border border-slate-800 rounded-xl p-4">
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
                    <p className="text-slate-400 text-xs mt-0.5 line-clamp-3">{af.description || "No description added yet"}</p>
                    <div className="flex gap-3 mt-1.5">
                      <span className="text-[10px] text-slate-500">model: {af.model}</span>
                      <span className="text-[10px] text-slate-500">capabilities: {af.tools.length}</span>
                      {af.token_limit > 0 && (
                        <span className="text-[10px] text-slate-500">{(af.token_limit / 1000).toFixed(0)}k tokens</span>
                      )}
                    </div>
                  </div>
                  <Icon name={isExpanded ? "expand_less" : "expand_more"} className="text-slate-500 shrink-0" size={20} />
                </div>

                {isExpanded && (
                  <div className="mt-3 pt-3 border-t border-slate-700/50">
                    <div className="mb-3">
                      <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Prompt</p>
                      <p className="text-xs text-slate-300 leading-relaxed">{af.prompt || "No instructions set"}</p>
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
                    <button
                      onClick={() => handleLaunch(af.name)}
                      disabled={launching === af.name}
                      className="w-full mt-2 py-2 bg-pink-500 hover:bg-pink-600 disabled:opacity-50 rounded-lg text-sm font-medium transition-colors"
                    >
                      {launching === af.name ? "Launching..." : "Launch Agent"}
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const POWER_USER_TABS = ["Delegate", "Workspace"];

type CustomTemplate = CustomAgentTemplate;

const marketplaceCategories: { category: string; templates: CustomTemplate[] }[] = [
  {
    category: "For everyone",
    templates: [
      { name: "Summarizer", description: "Summarize documents, articles, or meeting notes into key points.", icon: "summarize", model: "sonnet", budget: 2.0 },
      { name: "Daily Planner", description: "Review your tasks and create a focused plan for today.", icon: "today", model: "sonnet", budget: 2.0 },
      { name: "Email Drafter", description: "Draft a clear, friendly email based on your instructions.", icon: "mail", model: "sonnet", budget: 2.0 },
      { name: "Brainstorm", description: "Generate ideas for any topic or problem you are stuck on.", icon: "psychology", model: "sonnet", budget: 2.0 },
      { name: "Research", description: "Search the web, read multiple sources, and write a short summary.", icon: "search", model: "sonnet", budget: 2.0 },
    ],
  },
  {
    category: "Product managers",
    templates: [
      { name: "Competitive Scan", description: "Research what competitors are shipping in a product area.", icon: "monitor_heart", model: "sonnet", budget: 3.0 },
      { name: "PRD Draft", description: "Turn a rough idea into a product requirements doc.", icon: "article", model: "sonnet", budget: 3.0 },
      { name: "Customer Interview Notes", description: "Turn raw interview notes into themes and insights.", icon: "record_voice_over", model: "sonnet", budget: 2.0 },
      { name: "Launch Checklist", description: "Generate a launch checklist for a new feature.", icon: "checklist", model: "sonnet", budget: 2.0 },
      { name: "Stakeholder Update", description: "Write a weekly update for your leadership team.", icon: "campaign", model: "sonnet", budget: 2.0 },
    ],
  },
  {
    category: "Engineers",
    templates: [
      { name: "Code Review", description: "Review code for issues, bugs, and improvements.", icon: "code", model: "sonnet", budget: 2.0 },
      { name: "Write Tests", description: "Generate test cases for your code.", icon: "bug_report", model: "sonnet", budget: 2.0 },
      { name: "Bug Finder", description: "Analyze code for potential bugs and security issues.", icon: "pest_control", model: "sonnet", budget: 2.0 },
      { name: "Debug Helper", description: "Read an error log, find the root cause, and suggest a fix.", icon: "bug_report", model: "sonnet", budget: 2.0 },
      { name: "Refactor Plan", description: "Review messy code and propose a clean refactoring plan.", icon: "auto_fix_high", model: "sonnet", budget: 3.0 },
    ],
  },
  {
    category: "Sales and customer success",
    templates: [
      { name: "Prospect Research", description: "Dig into a company and decision maker before an outreach call.", icon: "business", model: "sonnet", budget: 3.0 },
      { name: "Cold Outreach Draft", description: "Draft a personalized outreach email to a prospect.", icon: "outgoing_mail", model: "sonnet", budget: 2.0 },
      { name: "Call Prep", description: "Build a 1-page call brief for an upcoming customer meeting.", icon: "support_agent", model: "sonnet", budget: 2.0 },
      { name: "Follow Up", description: "Turn a call into a recap email and next steps.", icon: "forward_to_inbox", model: "sonnet", budget: 2.0 },
      { name: "Objection Handling", description: "Help you prep answers to common customer objections.", icon: "question_answer", model: "sonnet", budget: 2.0 },
    ],
  },
  {
    category: "Writers and creators",
    templates: [
      { name: "Blog Post", description: "Write a draft blog post from an outline or rough idea.", icon: "edit_note", model: "sonnet", budget: 3.0 },
      { name: "Social Post", description: "Turn a long post into short, punchy social versions.", icon: "share", model: "sonnet", budget: 2.0 },
      { name: "Headline Generator", description: "Write 10 headline options for the same piece of content.", icon: "title", model: "sonnet", budget: 2.0 },
      { name: "Proofreader", description: "Catch typos, grammar issues, and awkward phrasing.", icon: "spellcheck", model: "sonnet", budget: 2.0 },
      { name: "Name Generator", description: "Come up with names for projects, features, or products.", icon: "label", model: "sonnet", budget: 2.0 },
    ],
  },
  {
    category: "Home and family",
    templates: [
      { name: "Meal Planner", description: "Plan a week of meals based on what is in the fridge.", icon: "restaurant", model: "sonnet", budget: 2.0 },
      { name: "Grocery List", description: "Turn a meal plan into an organized shopping list.", icon: "shopping_cart", model: "sonnet", budget: 2.0 },
      { name: "Trip Planner", description: "Plan a day trip or vacation with budget and time constraints.", icon: "flight_takeoff", model: "sonnet", budget: 3.0 },
      { name: "Gift Finder", description: "Suggest gift ideas for a specific person, budget, and occasion.", icon: "redeem", model: "sonnet", budget: 2.0 },
      { name: "Homework Helper", description: "Walk a kid through a tricky homework problem step by step.", icon: "school", model: "sonnet", budget: 2.0 },
    ],
  },
  {
    category: "Students",
    templates: [
      { name: "Study Guide", description: "Turn class notes into a study guide with key concepts and example questions.", icon: "menu_book", model: "sonnet", budget: 2.0 },
      { name: "Essay Outline", description: "Build an outline for a paper based on a prompt or topic.", icon: "format_list_numbered", model: "sonnet", budget: 2.0 },
      { name: "Flash Cards", description: "Turn a reading into a set of flash-card style Q&A pairs.", icon: "quiz", model: "sonnet", budget: 2.0 },
      { name: "Citation Helper", description: "Format sources in APA, MLA, or Chicago style.", icon: "format_quote", model: "sonnet", budget: 2.0 },
    ],
  },
];

/* ---------- Template Editor Modal ---------- */
function TemplateEditorModal({
  initial,
  isNew,
  onSpawn,
  onSave,
  onCancel,
}: {
  initial: CustomTemplate | null;
  isNew: boolean;
  onSpawn: (t: CustomTemplate) => void;
  onSave: (t: CustomTemplate) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [icon, setIcon] = useState(initial?.icon ?? "smart_toy");
  const [model, setModel] = useState(initial?.model ?? "sonnet");
  const [budget, setBudget] = useState(initial?.budget ?? 2.0);

  const current: CustomTemplate = { name, description, icon, model, budget };

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
          placeholder="e.g. Research Agent"
          className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 mb-4"
        />

        {/* Description / Prompt */}
        <label className="block text-sm text-slate-400 mb-1">Description / Prompt</label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={3}
          placeholder="What should this agent do?"
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
            onClick={() => { if (name.trim()) onSpawn(current); }}
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

interface AgentInfo {
  name: string;
  status: string;
  source: string;
  model?: string;
  budget?: string;
  timestamp?: string;
  spawned_at?: string;
  transcript_bytes?: number;
  transcript_lines?: number;
  tokens_used?: number;
  token_limit?: number | null;
  token_usage_pct?: number | null;
  cost_estimate?: number;
  recovery_count?: number;
  max_recoveries?: number;
}

// First-paint cache (needle 299).
//
// See the matching block in Tasks.tsx for the full rationale. Short
// version: Tori sees a blank Active Sessions panel for several seconds
// every time she opens /agents cold, so we seed the list from the last
// good /agents response stashed in localStorage. The live poll still
// runs and overwrites the cache as soon as fresh data arrives.
const AGENTS_CACHE_KEY = "myos.agentsCache.v1";

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

// Pattern recognition (Insights tab)
interface PatternRecommendation {
  type: string;
  severity: "info" | "tip" | "warning";
  message: string;
  related_template_id?: string | null;
  suggested_value?: number | string | null;
}

interface PatternTemplateStat {
  template_id: string;
  template_name: string;
  spawn_count: number;
  completed_count: number;
  success_rate: number;
  median_duration_sec: number | null;
  median_cost: number | null;
  best_model: string | null;
}

interface ProvenTemplate {
  template_name: string;
  template_id: string;
  model: string;
  budget: number;
  success_rate: number;
  spawn_count: number;
  promoted_at: string;
}

interface SuggestedAdjustment {
  template_name: string;
  template_id: string;
  consecutive_failures: number;
  suggestions: { type: string; value?: string | number; reason: string }[];
}

// Default estimates (used when no historical data exists)
const DEFAULT_MIN_PER_DOLLAR: Record<string, number> = {
  "claude-sonnet-4-6": 3,
  "claude-sonnet-4-5-20250929": 3,
  "claude-opus-4-6": 5,
  "claude-haiku-4-5": 2,
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

function formatModelShort(model?: string): string {
  if (!model) return "---";
  if (model.includes("sonnet")) return "sonnet";
  if (model.includes("opus")) return "opus";
  if (model.includes("haiku")) return "haiku";
  return model.split("-")[0] || model;
}

function AgentStatusBar({ spawnedAt, budget, model, learnedRates, transcriptBytes, transcriptLines }: {
  spawnedAt: string;
  budget?: string;
  model?: string;
  learnedRates?: Record<string, number>;
  transcriptBytes?: number;
  transcriptLines?: number;
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

  // Estimate ETA from budget and model
  let etaText = "";
  if (budget && model) {
    const rate = learnedRates?.[model] ?? DEFAULT_MIN_PER_DOLLAR[model] ?? 3;
    const estimatedTotalMin = parseFloat(budget) * rate;
    const remainingMin = Math.max(0, Math.round(estimatedTotalMin - elapsedSec / 60));
    if (remainingMin > 0) {
      etaText = `~${remainingMin}m left`;
    } else {
      etaText = "wrapping up";
    }
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
        <span className="text-slate-400">{(tokensUsed / 1000).toFixed(1)}k tokens used</span>
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
          {(tokensUsed / 1000).toFixed(1)}k / {(tokenLimit / 1000).toFixed(0)}k tokens ({pct}%)
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
}

interface NudgeReplyRecord {
  message: string;
  timestamp: string;
  source: string;
  in_reply_to?: string | null;
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

  return (
    <>
      <label className="block text-sm text-slate-400 mb-1">Name</label>
      <input
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="e.g. Competitive analysis"
        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 mb-4"
      />

      <label className="block text-sm text-slate-400 mb-1">Description</label>
      <input
        type="text"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="One line summary of what this template does"
        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 mb-4"
      />

      <label className="block text-sm text-slate-400 mb-1">Prompt template</label>
      <textarea
        value={promptTemplate}
        onChange={(e) => setPromptTemplate(e.target.value)}
        rows={4}
        placeholder="Use [placeholders] for parts you will fill in before spawning"
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
          onClick={() => {
            if (name.trim()) {
              onSave({ name, description, prompt_template: promptTemplate, icon, model, budget });
            }
          }}
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
  // Seed from the localStorage cache so the first paint shows the last
  // known list of agents instead of the empty "Loading..." state.
  // Needle 299. The daemon-filter matches the runtime filter in
  // fetchAgents so a stale cached daemon row never sneaks back in.
  const [allAgents, setAllAgents] = useState<AgentInfo[]>(() =>
    readAgentsCache().filter((a) => a.name !== 'daemon'),
  );
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
  const [learnedRates, setLearnedRates] = useState<Record<string, number>>({});
  const [templates, setTemplates] = useState<TemplatesResponse["templates"]>([]);
  const [pmTemplates, setPmTemplates] = useState<PMAgentTemplate[]>([]);
  const [pmTemplateSearch, setPmTemplateSearch] = useState("");
  const [pmTemplateEditor, setPmTemplateEditor] = useState<{
    open: boolean;
    template: PMAgentTemplate | null;
    isNew: boolean;
  }>({ open: false, template: null, isNew: false });
  const [pmTemplateSaving, setPmTemplateSaving] = useState(false);
  const [orgTemplates, setOrgTemplates] = useState<PMAgentTemplate[]>([]);
  const [showNewForm, setShowNewForm] = useState(false);
  const [newAgentName, setNewAgentName] = useState("");
  const [newAgentPrompt, setNewAgentPrompt] = useState("");
  const [newAgentTokenLimit, setNewAgentTokenLimit] = useState<string>("");
  const [recoveringAgents, setRecoveringAgents] = useState<Record<string, boolean>>({});
  const [, setLastUpdate] = useState<Date | null>(null);
  const [transcriptModal, setTranscriptModal] = useState<{name: string; content: string; loading: boolean} | null>(null);

  const openTranscript = async (name: string) => {
    setTranscriptModal({name, content: "", loading: true});
    try {
      const data = await api.get<{content: string}>(`/agents/${encodeURIComponent(name)}/transcript`);
      setTranscriptModal({name, content: data.content, loading: false});
    } catch {
      setTranscriptModal({name, content: "No transcript available for this agent yet.", loading: false});
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

  // Insights (pattern recognition) state
  const [insightsRecs, setInsightsRecs] = useState<PatternRecommendation[]>([]);
  const [insightsStats, setInsightsStats] = useState<PatternTemplateStat[]>([]);
  const [insightsLoading, setInsightsLoading] = useState(false);
  const [insightsError, setInsightsError] = useState<string>("");
  const [provenTemplates, setProvenTemplates] = useState<ProvenTemplate[]>([]);
  const [adjustments, setAdjustments] = useState<SuggestedAdjustment[]>([]);

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

  // Custom templates live on the server via the app store. localStorage
  // is only a first paint cache.
  const customTemplates = useAppStore((s) => s.customAgentTemplates);
  const setCustomTemplates = useAppStore((s) => s.setCustomAgentTemplates);
  const powerUserMode = useAppStore((s) => s.powerUserMode);
  const tabs = powerUserMode ? [...BASE_TABS, ...POWER_USER_TABS] : BASE_TABS;

  // Marketplace
  const [marketplaceOpen, setMarketplaceOpen] = useState(false);

  // Fleets
  interface FleetMember { role: string; icon: string; prompt: string }
  interface FleetTemplate { id: string; name: string; description: string; icon: string; members: FleetMember[] }
  const [fleets, setFleets] = useState<FleetTemplate[]>([]);
  const [fleetSpawning, setFleetSpawning] = useState<string | null>(null);
  const [fleetContext, setFleetContext] = useState('');
  const [fleetExpandedId, setFleetExpandedId] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ fleets: FleetTemplate[] }>('/agents/fleets')
      .then((res) => setFleets(res.fleets ?? []))
      .catch(() => {});
  }, []);

  const spawnFleet = async (fleetId: string) => {
    setFleetSpawning(fleetId);
    try {
      await api.post('/agents/fleets/spawn', {
        fleet_id: fleetId,
        context: fleetContext,
        model: 'sonnet',
        budget: 2.0,
      });
      setFleetContext('');
      setFleetExpandedId(null);
      fetchAgents();
    } catch (e) {
      console.error('Failed to spawn fleet:', e);
    } finally {
      setFleetSpawning(null);
    }
  };

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

  const templateIcons: Record<string, string> = {
    Research: "search",
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
  };

  const fetchAgents = async () => {
    try {
      const data = await api.get<AgentsResponse>("/agents");
      const newAgents = data.agents || [];

      // Detect status changes and fire notifications
      const prev = prevStatusRef.current;
      for (const agent of newAgents) {
        const prevStatus = prev[agent.name];
        const newStatus = agent.status;
        if (prevStatus !== undefined && prevStatus !== newStatus) {
          const wasActive = prevStatus === "running" || prevStatus === "spawned";
          const isDone =
            newStatus === "completed" ||
            newStatus === "failed" ||
            newStatus === "killed" ||
            newStatus === "stopped";
          if (wasActive && isDone) {
            addNotification(agent.name, prevStatus, newStatus);
            tryBrowserNotification(agent.name, newStatus);
          }
        }
      }
      prevStatusRef.current = Object.fromEntries(newAgents.map((a) => [a.name, a.status]));

      const filtered = newAgents.filter((a) => a.name !== 'daemon');
      setAllAgents(filtered);
      // Persist the last good response so the next cold visit paints
      // real rows from the first render. Needle 299.
      writeAgentsCache(filtered);
      setActiveAgents(data.active || []);
      setDaemonRunning(data.daemon_running ?? false);
      setConnectionStatus(data.daemon_running ? "Connected" : "Standby");
      if (data.avg_min_per_dollar) setLearnedRates(data.avg_min_per_dollar);
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
      setTemplates(data.templates || []);
    } catch {
      // keep empty
    }
  };

  const fetchPmTemplates = async () => {
    try {
      const data = await api.get<PMTemplatesResponse>("/agents/pm-templates");
      setPmTemplates(data.templates || []);
    } catch {
      // keep empty
    }
  };

  const fetchOrgTemplates = async () => {
    try {
      const data = await api.get<{ templates: Array<{ id: string; name: string; description: string; icon: string; prompt_template: string; model: string; budget: number }> }>("/enterprise/templates");
      setOrgTemplates((data.templates || []).map((t) => ({ ...t, builtin: false })));
    } catch {
      // Not in enterprise mode or no templates, keep empty
    }
  };

  const handleUsePmTemplate = (tpl: PMAgentTemplate) => {
    setNewAgentName(tpl.name.toLowerCase().replace(/\s+/g, "-"));
    setNewAgentPrompt(tpl.prompt_template);
    setShowNewForm(true);
    setActiveTab("Active");
  };

  const handleSavePmTemplate = async (d: Partial<PMAgentTemplate>) => {
    setPmTemplateSaving(true);
    try {
      if (pmTemplateEditor.isNew) {
        await api.post("/agents/pm-templates", d);
      } else if (pmTemplateEditor.template) {
        await api.put(`/agents/pm-templates/${pmTemplateEditor.template.id}`, d);
      }
      await fetchPmTemplates();
      setPmTemplateEditor({ open: false, template: null, isNew: false });
    } catch {
      // keep
    } finally {
      setPmTemplateSaving(false);
    }
  };

  const handleDeletePmTemplate = async (templateId: string) => {
    try {
      await api.delete(`/agents/pm-templates/${templateId}`);
      await fetchPmTemplates();
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
      setActiveTab("Active");
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
    fetchPmTemplates();
    fetchOrgTemplates();

    // Poll for agent updates every 5 seconds
    const interval = setInterval(fetchAgents, 5000);
    return () => clearInterval(interval);
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
      const running = allAgents.filter(
        (a) => a.status === "running" || a.status === "spawned"
      );
      for (const agent of running) {
        fetchContextPressure(agent.name);
      }
      const interval = setInterval(() => {
        const current = allAgents.filter(
          (a) => a.status === "running" || a.status === "spawned"
        );
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

  // Fetch insights (recommendations + per-template stats + proven + adjustments) when the Insights tab is selected
  useEffect(() => {
    if (activeTab !== "Insights") return;
    let cancelled = false;
    const fetchInsights = async () => {
      setInsightsLoading(true);
      setInsightsError("");
      try {
        const [recsResp, statsResp, provenResp, adjResp] = await Promise.all([
          api.get<{ recommendations: PatternRecommendation[] }>("/agent-patterns/recommendations"),
          api.get<{ stats: PatternTemplateStat[] }>("/agent-patterns/template-stats"),
          api.get<{ proven: ProvenTemplate[] }>("/agent-patterns/proven").catch(() => ({ proven: [] })),
          api.get<{ adjustments: SuggestedAdjustment[] }>("/agent-patterns/adjustments").catch(() => ({ adjustments: [] })),
        ]);
        if (cancelled) return;
        setInsightsRecs(recsResp.recommendations || []);
        setInsightsStats(statsResp.stats || []);
        setProvenTemplates(provenResp.proven || []);
        setAdjustments(adjResp.adjustments || []);
      } catch {
        if (cancelled) return;
        setInsightsError("Could not load insights. Try again in a moment.");
      } finally {
        if (!cancelled) setInsightsLoading(false);
      }
    };
    fetchInsights();
    return () => { cancelled = true; };
  }, [activeTab]);

  // Listen for the dashboard "Spawn Agent" quick launch so the form
  // opens the moment the user lands on this page.
  useEffect(() => {
    const handler = () => {
      setActiveTab("Active");
      setShowNewForm(true);
    };
    window.addEventListener('myos-quick-spawn-agent', handler);
    return () => window.removeEventListener('myos-quick-spawn-agent', handler);
  }, []);

  const handleSpawn = async (name: string, prompt?: string, model?: string, budget?: number, tokenLimit?: number | null, template?: string) => {
    if (!name.trim()) return;
    try {
      const spawnPayload: Record<string, unknown> = {
        name: name.trim(),
        prompt: prompt || `You are a ${name.trim()} agent. Do your job well.`,
        model: model || "sonnet",
        budget: budget ?? 2.0,
      };
      if (tokenLimit && tokenLimit > 0) {
        spawnPayload.token_limit = tokenLimit;
      }
      if (template) {
        spawnPayload.template = template;
      }
      await api.post("/agents/spawn", spawnPayload);
      setShowNewForm(false);
      setNewAgentName("");
      setEditorOpen(false);
      await fetchAgents();
    } catch {
      // handle error silently
    }
  };

  const [killingAgents, setKillingAgents] = useState<Record<string, boolean>>({});

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
      // Refresh immediately so the user sees the change without waiting
      // for the 5-second polling tick.
      await fetchAgents();
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
    capabilities: TemplateCapabilities | null;
    parseError: string | null;
  };

  const builtInTemplates: DisplayTemplate[] =
    templates.length > 0
      ? templates.map((t) => ({
          icon: getTemplateIcon(t.name),
          name: t.name,
          description: t.description || (t.content ? t.content.slice(0, 50) : "Agent template"),
          model: "sonnet",
          budget: 2.0,
          isBuiltIn: true,
          capabilities: t.capabilities ?? null,
          parseError: t.parse_error ?? null,
        }))
      : [
          { icon: "search", name: "Research", description: "Search and summarize information", model: "sonnet", budget: 2.0, isBuiltIn: true, capabilities: null, parseError: null },
          { icon: "code", name: "Code Review", description: "Review code for issues and improvements", model: "sonnet", budget: 2.0, isBuiltIn: true, capabilities: null, parseError: null },
          { icon: "bug_report", name: "Write Tests", description: "Generate test cases for your code", model: "sonnet", budget: 2.0, isBuiltIn: true, capabilities: null, parseError: null },
          { icon: "rocket_launch", name: "Deploy", description: "Automate deployment pipelines", model: "sonnet", budget: 2.0, isBuiltIn: true, capabilities: null, parseError: null },
        ];

  // Merge built-in + custom templates
  const displayTemplates: DisplayTemplate[] = [
    ...builtInTemplates,
    ...customTemplates.map((ct) => ({
      ...ct,
      isBuiltIn: false,
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
                  onClick={() => setActiveTab(tab)}
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
          <button
            onClick={() => setShowNewForm(!showNewForm)}
            className="bg-pink-500 text-white rounded-lg px-4 py-2 min-h-[44px] flex items-center gap-2 hover:bg-pink-600 transition-colors"
          >
            <Icon name="add" className="text-lg" />
            New Agent
          </button>
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
                  if (e.key === "Enter" && !newAgentPrompt) handleSpawn(newAgentName, newAgentPrompt || undefined, undefined, undefined, newAgentTokenLimit ? parseInt(newAgentTokenLimit, 10) : null);
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
                rows={3}
                placeholder="What should this agent do? (Pre-filled from template. Edit the [placeholders] before spawning.)"
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
              <label className="text-sm text-slate-400 whitespace-nowrap">Token usage</label>
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
            <h2 className="text-lg font-semibold text-white mb-4">
              Active Sessions
            </h2>
            {!agentsLoaded ? (
              <div
                data-testid="active-agents-loading"
                className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400 mb-8"
              >
                Loading...
              </div>
            ) : allAgents.filter((a) => a.status === "running" || a.status === "spawned").length === 0 ? (
              <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center mb-8">
                <Icon name="smart_toy" className="text-4xl text-slate-700 mb-2" />
                <p className="text-sm text-slate-400 mb-1">No agents running right now.</p>
                <p className="text-xs text-slate-600">Click a template below, launch a fleet, or use the New Agent button to get started.</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-6 mb-8">
                {allAgents
                  .filter((a) => a.status === "running" || a.status === "spawned")
                  // Extra belt: keep terminated_stale and cancelled out of
                  // Active Sessions even if an upstream join ever puts them
                  // in with a stale "running" label.
                  .filter((a) => a.status !== "terminated_stale" && a.status !== "cancelled")
                  .map((agent) => {
                    const isExpanded = expandedAgent === agent.name;
                    const agentNudges = nudgeHistory[agent.name] || [];
                    const agentReplies = nudgeReplies[agent.name] || [];
                    const isSending = nudgeSending[agent.name] || false;
                    const nudgeError = nudgeErrors[agent.name] || "";
                    const pendingGrants = grants.filter(
                      (g) => g.status === "pending" && g.agent === agent.name
                    );

                    return (
                  <div
                    key={agent.name}
                    className="bg-slate-900/40 border border-slate-800 rounded-xl p-5"
                  >
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-3">
                        <span className="text-white font-semibold">{agent.name}</span>
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
                      </div>
                      <button
                        onClick={() => setExpandedAgent(isExpanded ? null : agent.name)}
                        className="text-slate-400 hover:text-white transition-colors flex items-center gap-1 text-sm"
                        title={isExpanded ? "Collapse session" : "Expand session"}
                      >
                        <Icon name={isExpanded ? "expand_less" : "expand_more"} size={20} />
                        {isExpanded ? "Collapse" : "Expand"}
                      </button>
                    </div>
                    {(agent.spawned_at || agent.timestamp) && (
                      <AgentStatusBar
                        spawnedAt={agent.spawned_at || agent.timestamp!}
                        budget={agent.budget}
                        model={agent.model}
                        learnedRates={learnedRates}
                        transcriptBytes={agent.transcript_bytes}
                        transcriptLines={agent.transcript_lines}
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

                    {/* Expanded view with additional details and memory */}
                    {isExpanded && (
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
                    )}

                    <div className="flex items-center justify-between mt-4">
                      <button
                        onClick={() => openTranscript(agent.name)}
                        className="text-sm text-blue-400 hover:text-blue-300 transition-colors flex items-center gap-1"
                      >
                        <Icon name="description" size={16} />
                        View Transcript
                      </button>
                      <button
                        onClick={() => handleKill(agent.name)}
                        disabled={killingAgents[agent.name]}
                        className="border border-slate-700 text-slate-300 text-sm rounded-lg px-3 py-1 hover:border-red-500 hover:text-red-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                      >
                        {killingAgents[agent.name] ? "Cancelling..." : "Cancel"}
                      </button>
                    </div>
                  </div>
                    );
                  })}
              </div>
            )}
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
          const terminalStatuses = ["completed", "failed", "terminated_stale", "abandoned", "cancelled", "killed", "stopped"];
          const recentAgents = allAgents
            .filter((a) => terminalStatuses.includes(a.status))
            .sort((a, b) => {
              const ta = a.spawned_at || a.timestamp || "";
              const tb = b.spawned_at || b.timestamp || "";
              return tb.localeCompare(ta);
            })
            .slice(0, 20);
          const canRecover = (a: AgentInfo) =>
            ["failed", "terminated_stale", "abandoned", "cancelled", "stopped"].includes(a.status)
            && (a.recovery_count ?? 0) < (a.max_recoveries ?? 3);
          return (
            <>
              <h2 className="text-lg font-semibold text-white mb-4">
                Recent Agents
              </h2>
              {!agentsLoaded ? (
                <div
                  data-testid="recent-agents-loading"
                  className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400 mb-8"
                >
                  Loading...
                </div>
              ) : recentAgents.length === 0 ? (
                <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center mb-8">
                  <Icon name="smart_toy" className="text-4xl text-slate-700 mb-2" />
                  <p className="text-sm text-slate-400 mb-1">No agents have run yet.</p>
                  <p className="text-xs text-slate-600">Try asking myOS in chat to do something, like "help me plan my week" or "write a status update."</p>
                </div>
              ) : (
                <div className="flex flex-col gap-2 mb-8">
                  {recentAgents.map((agent) => {
                    const isRecentExpanded = expandedAgent === agent.name;
                    return (
                      <div
                        key={agent.name}
                        className="bg-slate-900/40 border border-slate-800 rounded-xl px-5 py-3"
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-3">
                            <span className="text-white font-medium">{agent.name}</span>
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

        {activeTab === "Insights" && (() => {
          const warnings = insightsRecs.filter((r) => r.severity === "warning");
          const tips = insightsRecs.filter((r) => r.severity === "tip");
          const infos = insightsRecs.filter((r) => r.severity === "info");

          const highSuccessTemplates = [...insightsStats]
            .filter((s) => s.spawn_count >= 2)
            .sort((a, b) => {
              if (b.success_rate !== a.success_rate) return b.success_rate - a.success_rate;
              return b.spawn_count - a.spawn_count;
            })
            .slice(0, 3);

          const severityStyles = (sev: string) => {
            if (sev === "warning") {
              return {
                wrapper: "bg-amber-500/10 border-amber-500/40",
                icon: "warning",
                iconColor: "text-amber-400",
                label: "Needs attention",
                labelColor: "text-amber-300",
              };
            }
            if (sev === "tip") {
              return {
                wrapper: "bg-blue-500/10 border-blue-500/40",
                icon: "lightbulb",
                iconColor: "text-blue-400",
                label: "Tip",
                labelColor: "text-blue-300",
              };
            }
            return {
              wrapper: "bg-slate-800/40 border-slate-700",
              icon: "info",
              iconColor: "text-slate-400",
              label: "Good to know",
              labelColor: "text-slate-300",
            };
          };

          const goToTemplate = (tid?: string | null) => {
            if (!tid) return;
            setActiveTab("Templates");
          };

          const applySuggestedBudget = async (tid: string | null | undefined, value: number | string | null | undefined) => {
            if (!tid || typeof value !== "number") return;
            const tmpl = pmTemplates.find((t) => t.id === tid);
            if (!tmpl) {
              setInsightsError("That template was not found. It may have been deleted.");
              return;
            }
            try {
              await api.put(`/agents/pm-templates/${tid}`, {
                name: tmpl.name,
                description: tmpl.description,
                icon: tmpl.icon,
                prompt_template: tmpl.prompt_template,
                model: tmpl.model,
                budget: value,
              });
              setInsightsError("");
              // Refresh templates so the UI reflects the new budget
              const data = await api.get<{ templates: PMAgentTemplate[] }>("/agents/pm-templates");
              setPmTemplates(data.templates || []);
            } catch {
              setInsightsError("Could not update the template budget. Try again.");
            }
          };

          const applySuggestedModel = async (tid: string | null | undefined, value: number | string | null | undefined) => {
            if (!tid || typeof value !== "string") return;
            const tmpl = pmTemplates.find((t) => t.id === tid);
            if (!tmpl) {
              setInsightsError("That template was not found. It may have been deleted.");
              return;
            }
            try {
              await api.put(`/agents/pm-templates/${tid}`, {
                name: tmpl.name,
                description: tmpl.description,
                icon: tmpl.icon,
                prompt_template: tmpl.prompt_template,
                model: value,
                budget: tmpl.budget,
              });
              setInsightsError("");
              const data = await api.get<{ templates: PMAgentTemplate[] }>("/agents/pm-templates");
              setPmTemplates(data.templates || []);
            } catch {
              setInsightsError("Could not update the template model. Try again.");
            }
          };

          const renderRec = (rec: PatternRecommendation, idx: number) => {
            const style = severityStyles(rec.severity);
            const actionable =
              (rec.type === "underbudgeted" || rec.type === "overbudgeted") && typeof rec.suggested_value === "number";
            const modelAction = rec.type === "wrong_model" && typeof rec.suggested_value === "string";
            return (
              <div
                key={`${rec.type}-${idx}`}
                className={`rounded-xl border p-4 mb-3 ${style.wrapper}`}
                data-testid={`insight-rec-${rec.severity}`}
              >
                <div className="flex items-start gap-3">
                  <Icon name={style.icon} size={20} className={style.iconColor} />
                  <div className="flex-1 min-w-0">
                    <p className={`text-xs uppercase tracking-wider font-semibold mb-1 ${style.labelColor}`}>
                      {style.label}
                    </p>
                    <p className="text-sm text-slate-100">{rec.message}</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {rec.related_template_id && (
                        <button
                          onClick={() => goToTemplate(rec.related_template_id)}
                          className="text-xs text-slate-300 hover:text-white underline underline-offset-2"
                        >
                          View template
                        </button>
                      )}
                      {actionable && (
                        <button
                          onClick={() => applySuggestedBudget(rec.related_template_id, rec.suggested_value)}
                          className="text-xs bg-blue-500 hover:bg-blue-600 text-white rounded px-3 py-1"
                        >
                          Apply suggested budget
                        </button>
                      )}
                      {modelAction && (
                        <button
                          onClick={() => applySuggestedModel(rec.related_template_id, rec.suggested_value)}
                          className="text-xs bg-blue-500 hover:bg-blue-600 text-white rounded px-3 py-1"
                        >
                          Switch to {String(rec.suggested_value)}
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            );
          };

          return (
            <div>
              <h2 className="text-lg font-semibold text-white mb-1">Insights</h2>
              <p className="text-slate-400 text-sm mb-6">
                What is working, what is not, and how to get more out of your agents.
              </p>

              {insightsLoading && (
                <div className="text-slate-400 text-sm mb-4">Loading insights...</div>
              )}
              {insightsError && (
                <div className="bg-red-500/10 border border-red-500/40 text-red-300 text-sm rounded-xl p-3 mb-4">
                  {insightsError}
                </div>
              )}

              {/* Top section: high-success template cards */}
              {highSuccessTemplates.length > 0 && (
                <div className="mb-8">
                  <h3 className="text-sm text-slate-400 uppercase tracking-wider mb-3">What is working</h3>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    {highSuccessTemplates.map((s) => (
                      <div
                        key={s.template_id}
                        className="bg-slate-900/40 border border-slate-800 rounded-xl p-4"
                        data-testid="insight-high-success-card"
                      >
                        <p className="text-white font-medium mb-1">{s.template_name}</p>
                        <p className="text-emerald-400 text-2xl font-bold">
                          {Math.round(s.success_rate * 100)}%
                        </p>
                        <p className="text-slate-500 text-xs mt-1">
                          succeeds across {s.spawn_count} {s.spawn_count === 1 ? "run" : "runs"}
                          {s.best_model ? ` - best on ${s.best_model}` : ""}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Middle section: recommendations grouped by severity */}
              <div className="mb-8">
                <h3 className="text-sm text-slate-400 uppercase tracking-wider mb-3">Recommendations</h3>
                {insightsRecs.length === 0 && !insightsLoading ? (
                  <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-6 text-center text-slate-400 text-sm">
                    No recommendations yet. Run a few agents and check back.
                  </div>
                ) : (
                  <>
                    {warnings.length > 0 && (
                      <div className="mb-4" data-testid="insight-warnings">
                        {warnings.map((rec, idx) => renderRec(rec, idx))}
                      </div>
                    )}
                    {tips.length > 0 && (
                      <div className="mb-4" data-testid="insight-tips">
                        {tips.map((rec, idx) => renderRec(rec, idx + warnings.length))}
                      </div>
                    )}
                    {infos.length > 0 && (
                      <div className="mb-4" data-testid="insight-infos">
                        {infos.map((rec, idx) => renderRec(rec, idx + warnings.length + tips.length))}
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* Proven templates section */}
              {provenTemplates.length > 0 && (
                <div className="mb-8">
                  <h3 className="text-sm text-slate-400 uppercase tracking-wider mb-3">Proven templates</h3>
                  <p className="text-slate-500 text-xs mb-3">
                    These templates have a strong track record. Use them with confidence.
                  </p>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {provenTemplates.map((pt) => (
                      <div
                        key={pt.template_id}
                        className="bg-slate-900/40 border border-emerald-500/30 rounded-xl p-4"
                        data-testid="proven-template-card"
                      >
                        <div className="flex items-center gap-2 mb-2">
                          <Icon name="verified" className="text-emerald-400" size={18} />
                          <p className="text-white font-medium">{pt.template_name}</p>
                        </div>
                        <div className="flex items-center gap-4 text-xs text-slate-400">
                          <span>{Math.round(pt.success_rate * 100)}% success</span>
                          <span>{pt.spawn_count} runs</span>
                          <span>Best on {pt.model}</span>
                          <span>${pt.budget.toFixed(2)} spending limit</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Suggested adjustments section */}
              {adjustments.length > 0 && (
                <div className="mb-8">
                  <h3 className="text-sm text-slate-400 uppercase tracking-wider mb-3">Needs attention</h3>
                  <p className="text-slate-500 text-xs mb-3">
                    These templates keep failing. Here is what to try.
                  </p>
                  <div className="space-y-4">
                    {adjustments.map((adj) => (
                      <div
                        key={adj.template_id}
                        className="bg-slate-900/40 border border-amber-500/30 rounded-xl p-4"
                        data-testid="adjustment-card"
                      >
                        <div className="flex items-center gap-2 mb-2">
                          <Icon name="warning" className="text-amber-400" size={18} />
                          <p className="text-white font-medium">{adj.template_name}</p>
                          <span className="text-xs text-amber-400 ml-auto">
                            {adj.consecutive_failures} failures in a row
                          </span>
                        </div>
                        <div className="space-y-2">
                          {adj.suggestions.map((sug, i) => (
                            <div key={i} className="flex items-start gap-2 text-sm">
                              <Icon
                                name={
                                  sug.type === "switch_model" ? "swap_horiz"
                                    : sug.type === "increase_budget" ? "trending_up"
                                    : "edit_note"
                                }
                                className="text-slate-400 mt-0.5"
                                size={14}
                              />
                              <span className="text-slate-300">{sug.reason}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Bottom section: per-template runtime and success rate */}
              {insightsStats.length > 0 && (
                <div className="mb-8">
                  <h3 className="text-sm text-slate-400 uppercase tracking-wider mb-3">Per template</h3>
                  <div className="bg-slate-900/40 border border-slate-800 rounded-xl overflow-hidden">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-slate-800">
                          <th className="text-left text-slate-400 text-xs uppercase tracking-wider px-5 py-3">Template</th>
                          <th className="text-left text-slate-400 text-xs uppercase tracking-wider px-5 py-3">Runs</th>
                          <th className="text-left text-slate-400 text-xs uppercase tracking-wider px-5 py-3">Success</th>
                          <th className="text-left text-slate-400 text-xs uppercase tracking-wider px-5 py-3">Median time</th>
                        </tr>
                      </thead>
                      <tbody>
                        {insightsStats.map((s) => {
                          const pct = Math.round(s.success_rate * 100);
                          const mins = s.median_duration_sec != null ? (s.median_duration_sec / 60).toFixed(1) : null;
                          return (
                            <tr key={s.template_id} className="border-b border-slate-800/50 last:border-b-0">
                              <td className="px-5 py-3 text-white font-medium">{s.template_name}</td>
                              <td className="px-5 py-3 text-slate-300">{s.spawn_count}</td>
                              <td className="px-5 py-3">
                                <div className="flex items-center gap-3">
                                  <div className="flex-1 bg-slate-800 rounded h-2 overflow-hidden max-w-[120px]">
                                    <div
                                      className="bg-emerald-500 h-full"
                                      style={{ width: `${pct}%` }}
                                    />
                                  </div>
                                  <span className="text-slate-300 text-xs tabular-nums">{pct}%</span>
                                </div>
                              </td>
                              <td className="px-5 py-3 text-slate-300">
                                {mins ? `${mins} min` : "-"}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          );
        })()}


        {activeTab === "Templates" && (
          <>
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-lg font-semibold text-white">PM Templates</h2>
                <p className="text-sm text-slate-400 mt-1">
                  Ready-made prompts for common PM tasks. Click "Use" to pre-fill the spawn form.
                </p>
              </div>
              <input
                type="text"
                value={pmTemplateSearch}
                onChange={(e) => setPmTemplateSearch(e.target.value)}
                placeholder="Find a template..."
                className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-1.5 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 w-48"
              />
            </div>
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Built-in</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8" data-testid="pm-builtin-templates">
              {pmTemplates
                .filter((t) => t.builtin)
                .filter(
                  (t) =>
                    !pmTemplateSearch ||
                    t.name.toLowerCase().includes(pmTemplateSearch.toLowerCase()) ||
                    t.description.toLowerCase().includes(pmTemplateSearch.toLowerCase()),
                )
                .map((tpl) => (
                  <div
                    key={tpl.id}
                    className="bg-slate-900/40 border border-slate-800 rounded-xl p-5 flex items-start gap-4 hover:border-slate-700 transition-colors"
                  >
                    <div className="w-10 h-10 rounded-lg bg-blue-500/10 border border-blue-500/20 flex items-center justify-center shrink-0">
                      <Icon name={tpl.icon} className="text-blue-400" size={22} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-white font-medium">{tpl.name}</p>
                      <p className="text-slate-400 text-xs mt-0.5">{tpl.description}</p>
                      <div className="mt-2 bg-slate-950 border border-slate-800 rounded px-2 py-2 font-mono text-xs text-slate-100 whitespace-pre-wrap break-words">
                        {tpl.prompt_template}
                      </div>
                    </div>
                    <button
                      onClick={() => handleUsePmTemplate(tpl)}
                      className="shrink-0 bg-pink-500 hover:bg-pink-600 text-white rounded-lg px-3 py-1.5 text-sm transition-colors"
                      data-testid="use-pm-template"
                    >
                      Use
                    </button>
                  </div>
                ))}
            </div>
            {/* Team (org) templates */}
            {orgTemplates.length > 0 && (
              <>
                <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Team</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8" data-testid="org-templates">
                  {orgTemplates
                    .filter(
                      (t) =>
                        !pmTemplateSearch ||
                        t.name.toLowerCase().includes(pmTemplateSearch.toLowerCase()) ||
                        t.description.toLowerCase().includes(pmTemplateSearch.toLowerCase()),
                    )
                    .map((tpl) => (
                      <div
                        key={tpl.id}
                        className="bg-slate-900/40 border border-slate-800 rounded-xl p-5 flex items-start gap-4 hover:border-slate-700 transition-colors"
                      >
                        <div className="w-10 h-10 rounded-lg bg-teal-500/10 border border-teal-500/20 flex items-center justify-center shrink-0">
                          <Icon name={tpl.icon} className="text-teal-400" size={22} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <p className="text-white font-medium">{tpl.name}</p>
                            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-teal-500/20 text-teal-400">
                              Team
                            </span>
                          </div>
                          <p className="text-slate-400 text-xs mt-0.5">{tpl.description}</p>
                          <div className="mt-2 bg-slate-950 border border-slate-800 rounded px-2 py-2 font-mono text-xs text-slate-100 whitespace-pre-wrap break-words">
                            {tpl.prompt_template}
                          </div>
                        </div>
                        <button
                          onClick={() => handleUsePmTemplate(tpl)}
                          className="shrink-0 bg-pink-500 hover:bg-pink-600 text-white rounded-lg px-3 py-1.5 text-sm transition-colors"
                        >
                          Use
                        </button>
                      </div>
                    ))}
                </div>
              </>
            )}

            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Your templates</h3>
              <button
                onClick={() => setPmTemplateEditor({ open: true, template: null, isNew: true })}
                className="text-sm text-blue-400 hover:text-blue-300 transition-colors flex items-center gap-1"
              >
                <Icon name="add" size={18} />
                New template
              </button>
            </div>
            {pmTemplates.filter((t) => !t.builtin).length === 0 ? (
              <div className="border border-dashed border-slate-700 rounded-xl p-8 text-center text-slate-500 mb-8">
                No custom templates yet. Click "New template" to create one.
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
                {pmTemplates
                  .filter((t) => !t.builtin)
                  .filter(
                    (t) =>
                      !pmTemplateSearch ||
                      t.name.toLowerCase().includes(pmTemplateSearch.toLowerCase()) ||
                      t.description.toLowerCase().includes(pmTemplateSearch.toLowerCase()),
                  )
                  .map((tpl) => (
                    <div
                      key={tpl.id}
                      className="bg-slate-900/40 border border-slate-800 rounded-xl p-5 flex items-start gap-4 hover:border-slate-700 transition-colors"
                    >
                      <div className="w-10 h-10 rounded-lg bg-purple-500/10 border border-purple-500/20 flex items-center justify-center shrink-0">
                        <Icon name={tpl.icon} className="text-purple-400" size={22} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-white font-medium">{tpl.name}</p>
                        <p className="text-slate-400 text-xs mt-0.5">{tpl.description}</p>
                        <div className="mt-2 bg-slate-950 border border-slate-800 rounded px-2 py-2 font-mono text-xs text-slate-100 whitespace-pre-wrap break-words">
                          {tpl.prompt_template}
                        </div>
                      </div>
                      <div className="flex flex-col gap-1 shrink-0">
                        <button
                          onClick={() => handleUsePmTemplate(tpl)}
                          className="bg-pink-500 hover:bg-pink-600 text-white rounded-lg px-3 py-1.5 text-sm transition-colors"
                        >
                          Use
                        </button>
                        <button
                          onClick={() => setPmTemplateEditor({ open: true, template: tpl, isNew: false })}
                          className="text-slate-400 hover:text-white rounded-lg px-3 py-1.5 text-sm transition-colors border border-slate-700"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => handleDeletePmTemplate(tpl.id)}
                          className="text-slate-500 hover:text-red-400 rounded-lg px-3 py-1.5 text-sm transition-colors"
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  ))}
              </div>
            )}
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
          </>
        )}

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
          <h2 className="text-lg font-semibold text-white">
            Agent Templates
          </h2>
          <button
            onClick={() => setMarketplaceOpen(!marketplaceOpen)}
            className="text-sm text-blue-400 hover:text-blue-300 transition-colors flex items-center gap-1"
          >
            <Icon name="storefront" size={18} />
            {marketplaceOpen ? "Hide Marketplace" : "Browse Marketplace"}
          </button>
        </div>
        <div className="grid grid-cols-4 gap-4">
          {displayTemplates.map((tpl) => {
            const hasParseError = tpl.parseError != null;
            const caps = tpl.capabilities;
            return (
              <div
                key={tpl.name}
                data-testid={`template-card-${tpl.name}`}
                onClick={() => {
                  if (hasParseError) return;
                  setEditorInitial({ name: tpl.name, description: tpl.description, icon: tpl.icon, model: tpl.model, budget: tpl.budget });
                  setEditorIsNew(false);
                  setEditorBuiltInName(tpl.isBuiltIn ? tpl.name : null);
                  setEditorOpen(true);
                }}
                className={`group relative bg-slate-900/40 border rounded-xl p-4 text-left transition-colors ${
                  hasParseError
                    ? "border-amber-500/40 cursor-not-allowed"
                    : "border-slate-800 hover:border-blue-500 cursor-pointer"
                }`}
              >
                {/* Delete button for custom templates */}
                {!tpl.isBuiltIn && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteCustomTemplate(tpl.name);
                    }}
                    className="absolute top-2 right-2 text-slate-600 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
                    title="Remove template"
                  >
                    <Icon name="delete" size={16} />
                  </button>
                )}
                <div className="text-center">
                  <Icon
                    name={tpl.icon}
                    className="text-3xl text-slate-400 mb-2 mx-auto"
                  />
                  <p className="text-white font-medium mb-1">{tpl.name}</p>
                  <p className="text-slate-400 text-xs line-clamp-2">{tpl.description}</p>
                </div>

                {/* Capabilities panel */}
                {hasParseError ? (
                  <div
                    data-testid={`template-capabilities-error-${tpl.name}`}
                    className="mt-3 pt-3 border-t border-slate-700/50"
                  >
                    <p className="text-xs text-amber-400">
                      Could not read capabilities for this template. Fix the .agent file.
                    </p>
                    <button
                      type="button"
                      disabled
                      data-testid={`template-spawn-${tpl.name}`}
                      className="w-full mt-2 py-1.5 bg-slate-800 text-slate-500 rounded-lg text-xs cursor-not-allowed"
                    >
                      Spawn
                    </button>
                  </div>
                ) : caps ? (
                  <div
                    data-testid={`template-capabilities-${tpl.name}`}
                    className="mt-3 pt-3 border-t border-slate-700/50 space-y-1 text-left"
                  >
                    <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Capabilities</p>
                    <div className="text-[11px] text-slate-300">
                      <span className="text-slate-500">Writes to: </span>
                      <span>{caps.writes_to}</span>
                    </div>
                    <div className="text-[11px] text-slate-300">
                      <span className="text-slate-500">Cannot touch: </span>
                      <span>{caps.cannot_touch}</span>
                    </div>
                    <div className="text-[11px] text-slate-300">
                      <span className="text-slate-500">Budget: </span>
                      <span>{caps.budget}</span>
                    </div>
                    <div className="text-[11px] text-slate-300">
                      <span className="text-slate-500">Time limit: </span>
                      <span>{caps.time_limit}</span>
                    </div>
                    <div className="text-[11px] text-slate-300">
                      <span className="text-slate-500">Sandbox: </span>
                      <span>{caps.sandbox}</span>
                    </div>
                  </div>
                ) : null}
              </div>
            );
          })}

          {/* + New Template card */}
          <div
            onClick={() => {
              setEditorInitial(null);
              setEditorIsNew(true);
              setEditorBuiltInName(null);
              setEditorOpen(true);
            }}
            className="bg-slate-900/20 border-2 border-dashed border-slate-700 rounded-xl p-4 text-center hover:border-blue-500 transition-colors cursor-pointer flex flex-col items-center justify-center"
          >
            <Icon name="add" className="text-3xl text-slate-500 mb-2" />
            <p className="text-slate-400 font-medium">New Template</p>
          </div>
        </div>

        {/* Fleet templates */}
        {fleets.length > 0 && (
          <div className="mt-8 bg-slate-900/40 border border-slate-800 rounded-xl p-6">
            <div className="flex items-center gap-2 mb-4">
              <Icon name="groups" className="text-blue-400" size={20} />
              <h3 className="text-white font-semibold">Fleets</h3>
              <span className="text-xs text-slate-500">Spawn a team of agents with different roles</span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {fleets.map((fleet) => {
                const isExpanded = fleetExpandedId === fleet.id;
                const isSpawning = fleetSpawning === fleet.id;
                return (
                  <div
                    key={fleet.id}
                    className="bg-slate-800/60 border border-slate-700 rounded-lg p-4"
                  >
                    <div
                      className="flex items-start gap-3 cursor-pointer"
                      onClick={() => setFleetExpandedId(isExpanded ? null : fleet.id)}
                    >
                      <Icon name={fleet.icon} className="text-2xl text-blue-400 mt-0.5 shrink-0" />
                      <div className="flex-1 min-w-0">
                        <p className="text-white text-sm font-medium">{fleet.name}</p>
                        <p className="text-slate-400 text-xs mt-0.5">{fleet.description}</p>
                        <div className="flex flex-wrap gap-1.5 mt-2">
                          {fleet.members.map((m) => (
                            <span key={m.role} className="inline-flex items-center gap-1 text-[10px] text-slate-400 bg-slate-700/50 px-1.5 py-0.5 rounded">
                              <Icon name={m.icon} className="text-[10px]" />
                              {m.role}
                            </span>
                          ))}
                        </div>
                      </div>
                      <Icon name={isExpanded ? 'expand_less' : 'expand_more'} className="text-slate-500 shrink-0" size={20} />
                    </div>
                    {isExpanded && (
                      <div className="mt-3 pt-3 border-t border-slate-700/50">
                        <input
                          type="text"
                          value={fleetContext}
                          onChange={(e) => setFleetContext(e.target.value)}
                          placeholder="What should this team work on?"
                          className="w-full bg-slate-900/60 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 placeholder-slate-600 focus:outline-none focus:border-blue-500 mb-3"
                        />
                        <button
                          onClick={() => spawnFleet(fleet.id)}
                          disabled={isSpawning || !fleetContext.trim()}
                          className="w-full py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg text-sm font-medium transition-colors"
                        >
                          {isSpawning ? 'Launching team...' : `Launch ${fleet.members.length} agents`}
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Marketplace section (collapsible) */}
        {marketplaceOpen && (
          <div className="mt-8 bg-slate-900/40 border border-slate-800 rounded-xl p-6">
            <h3 className="text-white font-semibold mb-4">Template Marketplace</h3>
            {marketplaceCategories.map((cat) => (
              <div key={cat.category} className="mb-6 last:mb-0">
                <h4 className="text-sm text-slate-400 font-medium mb-3 uppercase tracking-wider">{cat.category}</h4>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {cat.templates.map((mt) => {
                    const alreadyAdded = isCustomTemplate(mt.name) || builtInTemplates.some((b) => b.name === mt.name);
                    return (
                      <div
                        key={mt.name}
                        className="bg-slate-800/60 border border-slate-700 rounded-lg p-3 flex items-start gap-3"
                      >
                        <Icon name={mt.icon} className="text-2xl text-slate-400 mt-0.5 shrink-0" />
                        <div className="flex-1 min-w-0">
                          <p className="text-white text-sm font-medium">{mt.name}</p>
                          <p className="text-slate-400 text-xs mt-0.5 line-clamp-3">{mt.description}</p>
                        </div>
                        {alreadyAdded ? (
                          <span className="text-green-400 shrink-0 mt-0.5" title="Already added">
                            <Icon name="check_circle" size={20} />
                          </span>
                        ) : (
                          <button
                            onClick={() => addCustomTemplate(mt)}
                            className="text-blue-400 hover:text-blue-300 shrink-0 mt-0.5 transition-colors"
                            title="Add to your templates"
                          >
                            <Icon name="add_circle" size={20} />
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Workflows section within Templates */}
        <div className="mt-8 border-t border-slate-800 pt-8">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h3 className="text-white font-semibold">Workflows</h3>
              <p className="text-xs text-slate-500 mt-1">Multi-step agent pipelines you can run with one click</p>
            </div>
            <a
              href="/workflows/builder"
              className="flex items-center gap-2 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition-colors"
            >
              <Icon name="add" className="text-base" />
              Build custom workflow
            </a>
          </div>
          <AutomationTemplatesList />
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
            onSpawn={(t) => {
              handleSpawn(
                t.name.toLowerCase().replace(/\s+/g, "-"),
                t.description,
                t.model,
                t.budget,
                null,
                editorBuiltInName || undefined
              );
              setEditorOpen(false);
            }}
            onSave={(t) => {
              addCustomTemplate(t);
              setEditorOpen(false);
            }}
            onCancel={() => setEditorOpen(false)}
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
                <div className="text-slate-400 text-sm">Loading...</div>
              ) : (
                <pre className="text-xs text-slate-300 font-mono whitespace-pre-wrap break-words">{transcriptModal.content}</pre>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
