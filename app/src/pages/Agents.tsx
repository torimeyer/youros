import { useState, useEffect, useRef, useCallback } from "react";
import TopBar from "../components/TopBar";
import Icon from "../components/Icon";
import { api } from "../lib/api";
import { useNotificationStore } from "../stores/notifications";

const tabs = ["Active", "Recent", "Metrics"];

const CUSTOM_TEMPLATES_KEY = "youros-custom-templates";

interface CustomTemplate {
  name: string;
  description: string;
  icon: string;
  model: string;
  budget: number;
}

const marketplaceCategories: { category: string; templates: CustomTemplate[] }[] = [
  {
    category: "Productivity",
    templates: [
      { name: "Summarizer", description: "Summarize documents, articles, or meeting notes into key points", icon: "summarize", model: "sonnet", budget: 2.0 },
      { name: "Daily Planner", description: "Review your tasks and create a focused plan for today", icon: "today", model: "sonnet", budget: 2.0 },
      { name: "Email Drafter", description: "Draft professional emails based on your instructions", icon: "mail", model: "sonnet", budget: 2.0 },
    ],
  },
  {
    category: "Development",
    templates: [
      { name: "Research", description: "Search and summarize information from the web", icon: "search", model: "sonnet", budget: 2.0 },
      { name: "Code Review", description: "Review code for issues, bugs, and improvements", icon: "code", model: "sonnet", budget: 2.0 },
      { name: "Write Tests", description: "Generate test cases for your code", icon: "bug_report", model: "sonnet", budget: 2.0 },
      { name: "Bug Finder", description: "Analyze code for potential bugs and security issues", icon: "pest_control", model: "sonnet", budget: 2.0 },
    ],
  },
  {
    category: "Creative",
    templates: [
      { name: "Brainstorm", description: "Generate creative ideas for any topic or problem", icon: "psychology", model: "sonnet", budget: 2.0 },
      { name: "Writer", description: "Write blog posts, documentation, or creative content", icon: "edit_note", model: "sonnet", budget: 2.0 },
      { name: "Name Generator", description: "Come up with names for projects, features, or products", icon: "label", model: "sonnet", budget: 2.0 },
    ],
  },
];

function loadCustomTemplates(): CustomTemplate[] {
  try {
    const raw = localStorage.getItem(CUSTOM_TEMPLATES_KEY);
    if (raw) return JSON.parse(raw);
  } catch {
    // ignore parse errors
  }
  return [];
}

function saveCustomTemplates(templates: CustomTemplate[]) {
  localStorage.setItem(CUSTOM_TEMPLATES_KEY, JSON.stringify(templates));
}

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

export default function Agents() {
  const [activeTab, setActiveTab] = useState("Active");
  const [allAgents, setAllAgents] = useState<AgentInfo[]>([]);
  const [, setActiveAgents] = useState<string[]>([]);
  const [, setConnectionStatus] = useState("Connecting...");
  const [daemonRunning, setDaemonRunning] = useState(false);
  const [learnedRates, setLearnedRates] = useState<Record<string, number>>({});
  const [templates, setTemplates] = useState<TemplatesResponse["templates"]>([]);
  const [showNewForm, setShowNewForm] = useState(false);
  const [newAgentName, setNewAgentName] = useState("");
  const [, setLastUpdate] = useState<Date | null>(null);

  const addNotification = useNotificationStore((s) => s.addNotification);
  // Track previous agent statuses to detect changes between polls
  const prevStatusRef = useRef<Record<string, string>>({});

  // Nudge state: per-agent input text and message history
  const [nudgeInputs, setNudgeInputs] = useState<Record<string, string>>({});
  const [nudgeHistory, setNudgeHistory] = useState<Record<string, NudgeRecord[]>>({});
  const [nudgeSending, setNudgeSending] = useState<Record<string, boolean>>({});
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  const nudgeEndRef = useRef<Record<string, HTMLDivElement | null>>({});

  // Fetch nudge history when expanding an agent
  useEffect(() => {
    if (expandedAgent) {
      fetchNudges(expandedAgent);
      // Poll nudges while expanded
      const interval = setInterval(() => fetchNudges(expandedAgent), 5000);
      return () => clearInterval(interval);
    }
  }, [expandedAgent]);

  // Template editor modal state
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorInitial, setEditorInitial] = useState<CustomTemplate | null>(null);
  const [editorIsNew, setEditorIsNew] = useState(false);

  // Custom templates from localStorage
  const [customTemplates, setCustomTemplates] = useState<CustomTemplate[]>([]);

  // Marketplace
  const [marketplaceOpen, setMarketplaceOpen] = useState(false);

  // Load custom templates on mount
  useEffect(() => {
    setCustomTemplates(loadCustomTemplates());
  }, []);

  const addCustomTemplate = useCallback((t: CustomTemplate) => {
    setCustomTemplates((prev) => {
      const updated = [...prev, t];
      saveCustomTemplates(updated);
      return updated;
    });
  }, []);

  const deleteCustomTemplate = useCallback((name: string) => {
    setCustomTemplates((prev) => {
      const updated = prev.filter((t) => t.name !== name);
      saveCustomTemplates(updated);
      return updated;
    });
  }, []);

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

    // Poll for agent updates every 5 seconds
    const interval = setInterval(fetchAgents, 5000);
    return () => clearInterval(interval);
  }, []);

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
      <div className="pt-16 p-8">
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
          <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-4 mb-6 flex gap-3 items-center">
            <input
              type="text"
              placeholder="Agent name (e.g. research-agent)"
              value={newAgentName}
              onChange={(e) => setNewAgentName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSpawn(newAgentName);
              }}
              className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-4 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
            />
            <button
              onClick={() => handleSpawn(newAgentName)}
              className="bg-blue-500 hover:bg-blue-600 text-white rounded-lg px-4 py-2 transition-colors"
            >
              Spawn
            </button>
            <button
              onClick={() => {
                setShowNewForm(false);
                setNewAgentName("");
              }}
              className="text-slate-400 hover:text-white transition-colors"
            >
              <Icon name="close" size={20} />
            </button>
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

                    {/* Expanded view with additional details */}
                    {isExpanded && (
                      <div className="mt-4 pt-4 border-t border-slate-800">
                        <div className="grid grid-cols-3 gap-4 text-xs">
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
                      </div>
                    )}

                    <div className="flex items-center justify-between mt-4">
                      <a
                        href="/transcripts"
                        className="text-sm text-blue-400 hover:text-blue-300 transition-colors flex items-center gap-1"
                      >
                        <Icon name="description" size={16} />
                        View Transcript
                      </a>
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

        {activeTab === "Recent" && (
          <>
            <h2 className="text-lg font-semibold text-white mb-4">
              Recent Activity
            </h2>
            {allAgents.length === 0 ? (
              <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400 mb-8">
                No agent history yet. Agents you spawn will appear here.
              </div>
            ) : (
              <div className="flex flex-col gap-2 mb-8">
                {allAgents.map((agent) => (
                  <div
                    key={agent.name}
                    className="flex items-center justify-between bg-slate-900/40 border border-slate-800 rounded-xl px-5 py-3"
                  >
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
                      {agent.timestamp && (
                        <span className="text-slate-500 text-sm">{agent.timestamp}</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

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

        {/* Agent Templates - always visible */}
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
    </>
  );
}
