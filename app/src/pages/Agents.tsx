import { useState, useEffect, useRef, useCallback } from "react";
import TopBar from "../components/TopBar";
import Icon from "../components/Icon";
import { api } from "../lib/api";
import { useNotificationStore } from "../stores/notifications";
import { useAppStore, type CustomAgentTemplate } from "../stores/app";

const BASE_TABS = ["Active", "Recent", "Metrics", "Templates"];
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
        <label className="block text-sm text-slate-400 mb-1">Icon</label>
        <input
          type="text"
          value={icon}
          onChange={(e) => setIcon(e.target.value)}
          placeholder="Material Symbols icon name"
          className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 mb-4"
        />

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

interface TemplatesResponse {
  templates: { name: string; file: string; content: string }[];
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
}

interface NudgeResponse {
  result: string;
  nudge: NudgeRecord;
}

interface NudgesListResponse {
  agent: string;
  nudges: NudgeRecord[];
  session_nudges: NudgeRecord[];
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
        placeholder="Use [placeholders] for parts the user will fill in before spawning"
        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 mb-1 resize-none"
      />
      <p className="text-xs text-slate-500 mb-4">Use [square brackets] for parts the user fills in. Example: Research [topic] and write a summary.</p>

      <label className="block text-sm text-slate-400 mb-1">Icon (Material Symbols name)</label>
      <input
        type="text"
        value={icon}
        onChange={(e) => setIcon(e.target.value)}
        placeholder="smart_toy"
        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 mb-4"
      />

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
  const [allAgents, setAllAgents] = useState<AgentInfo[]>([]);
  const [, setActiveAgents] = useState<string[]>([]);
  const [, setConnectionStatus] = useState("Connecting...");
  const [daemonRunning, setDaemonRunning] = useState(false);
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
  const [showNewForm, setShowNewForm] = useState(false);
  const [newAgentName, setNewAgentName] = useState("");
  const [newAgentPrompt, setNewAgentPrompt] = useState("");
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

  // Nudge state: per-agent input text and message history
  const [nudgeInputs, setNudgeInputs] = useState<Record<string, string>>({});
  const [nudgeHistory, setNudgeHistory] = useState<Record<string, NudgeRecord[]>>({});
  const [nudgeSending, setNudgeSending] = useState<Record<string, boolean>>({});
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  const nudgeEndRef = useRef<Record<string, HTMLDivElement | null>>({});

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

  // Template editor modal state
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorInitial, setEditorInitial] = useState<CustomTemplate | null>(null);
  const [editorIsNew, setEditorIsNew] = useState(false);

  // Custom templates live on the server via the app store. localStorage
  // is only a first paint cache.
  const customTemplates = useAppStore((s) => s.customAgentTemplates);
  const setCustomTemplates = useAppStore((s) => s.setCustomAgentTemplates);
  const powerUserMode = useAppStore((s) => s.powerUserMode);
  const tabs = powerUserMode ? [...BASE_TABS, ...POWER_USER_TABS] : BASE_TABS;

  // Marketplace
  const [marketplaceOpen, setMarketplaceOpen] = useState(false);

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

      setAllAgents(newAgents);
      setActiveAgents(data.active || []);
      setDaemonRunning(data.daemon_running ?? false);
      setConnectionStatus(data.daemon_running ? "Connected" : "Standby");
      if (data.avg_min_per_dollar) setLearnedRates(data.avg_min_per_dollar);
      setLastUpdate(new Date());
    } catch {
      setConnectionStatus("Disconnected");
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
      // Merge file-based and session nudges, deduplicate by timestamp
      const all = [...(data.nudges || []), ...(data.session_nudges || [])];
      const seen = new Set<string>();
      const unique = all.filter((n) => {
        const key = `${n.timestamp}-${n.message}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      unique.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
      setNudgeHistory((prev) => ({ ...prev, [agentName]: unique }));
    } catch {
      // keep existing
    }
  };

  const handleNudge = async (agentName: string) => {
    const message = (nudgeInputs[agentName] || "").trim();
    if (!message) return;

    setNudgeSending((prev) => ({ ...prev, [agentName]: true }));
    try {
      const resp = await api.post<NudgeResponse>(`/agents/${agentName}/nudge`, { message });
      // Add to local history immediately
      setNudgeHistory((prev) => ({
        ...prev,
        [agentName]: [...(prev[agentName] || []), resp.nudge],
      }));
      setNudgeInputs((prev) => ({ ...prev, [agentName]: "" }));
    } catch {
      // handle error silently
    } finally {
      setNudgeSending((prev) => ({ ...prev, [agentName]: false }));
    }
  };

  useEffect(() => {
    fetchAgents();
    fetchTemplates();
    fetchPmTemplates();

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

  // Fetch workspace messages when the Workspace tab is selected
  useEffect(() => {
    if (activeTab === "Workspace") {
      fetchWorkspace();
      const interval = setInterval(fetchWorkspace, 5000);
      return () => clearInterval(interval);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  const handleSpawn = async (name: string, prompt?: string, model?: string, budget?: number) => {
    if (!name.trim()) return;
    try {
      await api.post("/agents/spawn", {
        name: name.trim(),
        prompt: prompt || `You are a ${name.trim()} agent. Do your job well.`,
        model: model || "sonnet",
        budget: budget ?? 2.0,
      });
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
      await api.post(`/agents/${name}/kill`);
    } catch {
      // Agent may already be gone, that is fine
    } finally {
      setKillingAgents((prev) => ({ ...prev, [name]: false }));
      await fetchAgents();
    }
  };


  // Default template icons for API templates that don't match known names
  const getTemplateIcon = (name: string) => {
    return templateIcons[name] || "smart_toy";
  };

  // Built-in templates (from API or defaults)
  const builtInTemplates: { icon: string; name: string; description: string; model: string; budget: number; isBuiltIn: boolean }[] =
    templates.length > 0
      ? templates.map((t) => ({
          icon: getTemplateIcon(t.name),
          name: t.name,
          description: t.content ? t.content.slice(0, 50) : "Agent template",
          model: "sonnet",
          budget: 2.0,
          isBuiltIn: true,
        }))
      : [
          { icon: "search", name: "Research", description: "Search and summarize information", model: "sonnet", budget: 2.0, isBuiltIn: true },
          { icon: "code", name: "Code Review", description: "Review code for issues and improvements", model: "sonnet", budget: 2.0, isBuiltIn: true },
          { icon: "bug_report", name: "Write Tests", description: "Generate test cases for your code", model: "sonnet", budget: 2.0, isBuiltIn: true },
          { icon: "rocket_launch", name: "Deploy", description: "Automate deployment pipelines", model: "sonnet", budget: 2.0, isBuiltIn: true },
        ];

  // Merge built-in + custom templates
  const displayTemplates = [
    ...builtInTemplates,
    ...customTemplates.map((ct) => ({ ...ct, isBuiltIn: false })),
  ];

  return (
    <>
      <TopBar title="Agents" />
      <div data-tour="agents" className="pt-20 p-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-6">
            <h1 className="text-2xl font-bold text-white">Agents</h1>
            <div className="flex gap-4">
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
            className="bg-pink-500 text-white rounded-lg px-4 py-2 flex items-center gap-2 hover:bg-pink-600 transition-colors"
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
                  if (e.key === "Enter" && !newAgentPrompt) handleSpawn(newAgentName, newAgentPrompt || undefined);
                }}
                className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
              />
              <button
                onClick={() => handleSpawn(newAgentName, newAgentPrompt || undefined)}
                className="bg-blue-500 hover:bg-blue-600 text-white rounded-lg px-4 py-2 transition-colors"
              >
                Spawn
              </button>
              <button
                onClick={() => {
                  setShowNewForm(false);
                  setNewAgentName("");
                  setNewAgentPrompt("");
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
          </div>
        )}


        {/* Tab content */}
        {activeTab === "Active" && (
          <>
            {/* Active Sessions */}
            <h2 className="text-lg font-semibold text-white mb-4">
              Active Sessions
            </h2>
            {allAgents.filter((a) => a.status === "running" || a.status === "spawned").length === 0 ? (
              <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400 mb-8">
                {!daemonRunning
                  ? "No active agents. Click a template below or use the New Agent button to get started."
                  : "No active agents. Spawn one to get started."}
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-6 mb-8">
                {allAgents
                  .filter((a) => a.status === "running" || a.status === "spawned")
                  .map((agent) => {
                    const isExpanded = expandedAgent === agent.name;
                    const agentNudges = nudgeHistory[agent.name] || [];
                    const nudgeInput = nudgeInputs[agent.name] || "";
                    const isSending = nudgeSending[agent.name] || false;
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
                                  Wants to {grant.type === "secret" ? "access secret" : grant.type === "file_access" ? "read file" : grant.type === "tool" ? "use tool" : grant.type === "budget" ? "raise budget" : grant.type === "model_upgrade" ? "upgrade model" : grant.type}: <span className="font-mono text-yellow-300">{grant.target}</span>
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

                    {/* Session output area with nudge history */}
                    <div className="bg-slate-950 rounded-lg p-3 font-mono text-xs mt-3 max-h-64 overflow-y-auto">
                      <div className="text-green-400">Agent is active...</div>
                      {agentNudges.map((nudge, i) => (
                        <div key={`${nudge.timestamp}-${i}`} className="mt-2">
                          <div className="text-blue-400">
                            <span className="text-slate-500 text-[10px]">
                              [{new Date(nudge.timestamp).toLocaleTimeString()}]
                            </span>{" "}
                            <span className="text-pink-400 font-bold">You:</span>{" "}
                            {nudge.message}
                          </div>
                          {nudge.stdin_delivered && (
                            <div className="text-slate-600 text-[10px] ml-4">
                              Delivered to agent stdin
                            </div>
                          )}
                        </div>
                      ))}
                      <div
                        ref={(el) => { nudgeEndRef.current[agent.name] = el; }}
                      />
                    </div>

                    {/* Nudge input. Always visible for active agents. */}
                    <p className="text-[10px] text-slate-600 mt-3 mb-1">
                      Send a message to this agent. Check Transcripts to see its full output.
                    </p>
                    <div className="flex gap-2">
                      <input
                        type="text"
                        placeholder="Send a message to this agent..."
                        value={nudgeInput}
                        onChange={(e) =>
                          setNudgeInputs((prev) => ({
                            ...prev,
                            [agent.name]: e.target.value,
                          }))
                        }
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && !isSending) handleNudge(agent.name);
                        }}
                        disabled={isSending}
                        className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm placeholder-slate-500 focus:outline-none focus:border-blue-500 disabled:opacity-50"
                      />
                      <button
                        onClick={() => handleNudge(agent.name)}
                        disabled={isSending || !nudgeInput.trim()}
                        className="bg-blue-500 hover:bg-blue-600 disabled:bg-slate-700 disabled:text-slate-500 text-white rounded-lg px-3 py-2 transition-colors flex items-center gap-1 text-sm"
                      >
                        <Icon name="send" size={16} />
                        {isSending ? "Sending..." : "Send"}
                      </button>
                    </div>

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
          const recentAgents = allAgents
            .filter((a) => a.status === "completed")
            .sort((a, b) => {
              const ta = a.spawned_at || a.timestamp || "";
              const tb = b.spawned_at || b.timestamp || "";
              return tb.localeCompare(ta);
            })
            .slice(0, 20);
          return (
            <>
              <h2 className="text-lg font-semibold text-white mb-4">
                Completed Agents
              </h2>
              {recentAgents.length === 0 ? (
                <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400 mb-8">
                  No completed agents yet. Agents you spawn will appear here once they finish.
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
                          <span className="text-white font-medium">{agent.name}</span>
                          <div className="flex items-center gap-4">
                            <span
                              className={`text-xs font-bold px-2 py-0.5 rounded ${statusColor(agent.status)}`}
                            >
                              {statusLabel(agent.status)}
                            </span>
                            {agent.model && (
                              <span className="text-slate-500 text-xs">{agent.model}</span>
                            )}
                            {(agent.spawned_at || agent.timestamp) && (
                              <span className="text-slate-500 text-sm">
                                {new Date(agent.spawned_at || agent.timestamp!).toLocaleString()}
                              </span>
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

        {activeTab === "Metrics" && (() => {
          const totalSpawned = allAgents.length;
          const completedAgents = allAgents.filter((a) => a.status === "completed");
          const failedAgents = allAgents.filter((a) => a.status === "failed");
          const stoppedAgents = allAgents.filter((a) => a.status === "stopped" || a.status === "killed");
          const runningAgents = allAgents.filter((a) => a.status === "running" || a.status === "spawned");

          // Compute average duration from learned rates (avg_min_per_dollar)
          const rateEntries = Object.entries(learnedRates);
          const avgDuration = rateEntries.length > 0
            ? (rateEntries.reduce((sum, [, rate]) => sum + rate, 0) / rateEntries.length).toFixed(1)
            : null;

          // Recent completions (completed or failed, sorted newest first)
          const recentDone = allAgents
            .filter((a) => a.status === "completed" || a.status === "failed")
            .sort((a, b) => {
              const ta = a.spawned_at || a.timestamp || "";
              const tb = b.spawned_at || b.timestamp || "";
              return tb.localeCompare(ta);
            })
            .slice(0, 10);

          return (
            <>
              {/* Stat cards */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
                <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-5">
                  <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">Total Spawned</p>
                  <p className="text-3xl font-bold text-white">{totalSpawned}</p>
                </div>
                <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-5">
                  <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">Completed</p>
                  <p className="text-3xl font-bold text-blue-400">{completedAgents.length}</p>
                </div>
                <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-5">
                  <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">Failed</p>
                  <p className="text-3xl font-bold text-red-400">{failedAgents.length}</p>
                </div>
                <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-5">
                  <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">Stopped / Cancelled</p>
                  <p className="text-3xl font-bold text-orange-400">{stoppedAgents.length}</p>
                </div>
              </div>

              {/* Secondary stats */}
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-8">
                <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-5">
                  <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">Currently Running</p>
                  <p className="text-2xl font-bold text-green-400">{runningAgents.length}</p>
                </div>
                <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-5">
                  <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">Avg Duration per $1</p>
                  <p className="text-2xl font-bold text-white">
                    {avgDuration ? `${avgDuration} min` : "No data yet"}
                  </p>
                  <p className="text-slate-500 text-xs mt-1">Based on completed agents</p>
                </div>
                <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-5">
                  <p className="text-slate-400 text-xs uppercase tracking-wider mb-1">Success Rate</p>
                  <p className="text-2xl font-bold text-white">
                    {completedAgents.length + failedAgents.length > 0
                      ? `${Math.round((completedAgents.length / (completedAgents.length + failedAgents.length)) * 100)}%`
                      : "No data yet"}
                  </p>
                </div>
              </div>

              {/* Recent completions table */}
              <h2 className="text-lg font-semibold text-white mb-4">Recent Completions</h2>
              {recentDone.length === 0 ? (
                <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400 mb-8">
                  No completed agents yet. Metrics will appear here as agents finish.
                </div>
              ) : (
                <div className="bg-slate-900/40 border border-slate-800 rounded-xl overflow-hidden mb-8">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-800">
                        <th className="text-left text-slate-400 text-xs uppercase tracking-wider px-5 py-3">Agent</th>
                        <th className="text-left text-slate-400 text-xs uppercase tracking-wider px-5 py-3">Status</th>
                        <th className="text-left text-slate-400 text-xs uppercase tracking-wider px-5 py-3">Model</th>
                        <th className="text-left text-slate-400 text-xs uppercase tracking-wider px-5 py-3">Budget</th>
                        <th className="text-left text-slate-400 text-xs uppercase tracking-wider px-5 py-3">Started</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentDone.map((agent) => (
                        <tr key={agent.name} className="border-b border-slate-800/50 last:border-b-0">
                          <td className="px-5 py-3 text-white font-medium">{agent.name}</td>
                          <td className="px-5 py-3">
                            <span className={`text-xs font-bold px-2 py-0.5 rounded ${statusColor(agent.status)}`}>
                              {statusLabel(agent.status)}
                            </span>
                          </td>
                          <td className="px-5 py-3 text-slate-400">{formatModelShort(agent.model)}</td>
                          <td className="px-5 py-3 text-slate-400">{agent.budget ? `$${agent.budget}` : "N/A"}</td>
                          <td className="px-5 py-3 text-slate-400">
                            {agent.spawned_at
                              ? new Date(agent.spawned_at).toLocaleString()
                              : agent.timestamp || "Unknown"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
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
          {displayTemplates.map((tpl) => (
            <div
              key={tpl.name}
              onClick={() => {
                setEditorInitial({ name: tpl.name, description: tpl.description, icon: tpl.icon, model: tpl.model, budget: tpl.budget });
                setEditorIsNew(false);
                setEditorOpen(true);
              }}
              className="group relative bg-slate-900/40 border border-slate-800 rounded-xl p-4 text-center hover:border-blue-500 transition-colors cursor-pointer"
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
              <Icon
                name={tpl.icon}
                className="text-3xl text-slate-400 mb-2 mx-auto"
              />
              <p className="text-white font-medium mb-1">{tpl.name}</p>
              <p className="text-slate-400 text-xs">{tpl.description}</p>
            </div>
          ))}

          {/* + New Template card */}
          <div
            onClick={() => {
              setEditorInitial(null);
              setEditorIsNew(true);
              setEditorOpen(true);
            }}
            className="bg-slate-900/20 border-2 border-dashed border-slate-700 rounded-xl p-4 text-center hover:border-blue-500 transition-colors cursor-pointer flex flex-col items-center justify-center"
          >
            <Icon name="add" className="text-3xl text-slate-500 mb-2" />
            <p className="text-slate-400 font-medium">New Template</p>
          </div>
        </div>

        {/* Marketplace section (collapsible) */}
        {marketplaceOpen && (
          <div className="mt-8 bg-slate-900/40 border border-slate-800 rounded-xl p-6">
            <h3 className="text-white font-semibold mb-4">Template Marketplace</h3>
            {marketplaceCategories.map((cat) => (
              <div key={cat.category} className="mb-6 last:mb-0">
                <h4 className="text-sm text-slate-400 font-medium mb-3 uppercase tracking-wider">{cat.category}</h4>
                <div className="grid grid-cols-3 gap-3">
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
                          <p className="text-slate-400 text-xs mt-0.5 line-clamp-2">{mt.description}</p>
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

        </div>}

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
                t.budget
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
