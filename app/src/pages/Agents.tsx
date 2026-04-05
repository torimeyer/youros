import { useState, useEffect, useRef } from "react";
import TopBar from "../components/TopBar";
import Icon from "../components/Icon";
import { api } from "../lib/api";

const tabs = ["Active", "Recent", "Metrics", "Automation"];

interface AgentInfo {
  name: string;
  status: string;
  source: string;
  model?: string;
  budget?: string;
  timestamp?: string;
}

interface AgentsResponse {
  daemon_running: boolean;
  status: string;
  active: string[];
  agents: AgentInfo[];
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
    default:
      return "bg-slate-500/20 text-slate-400";
  }
};

export default function Agents() {
  const [activeTab, setActiveTab] = useState("Active");
  const [allAgents, setAllAgents] = useState<AgentInfo[]>([]);
  const [activeAgents, setActiveAgents] = useState<string[]>([]);
  const [connectionStatus, setConnectionStatus] = useState("Connecting...");
  const [daemonRunning, setDaemonRunning] = useState(false);
  const [templates, setTemplates] = useState<TemplatesResponse["templates"]>([]);
  const [showNewForm, setShowNewForm] = useState(false);
  const [newAgentName, setNewAgentName] = useState("");
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

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

  const templateIcons: Record<string, string> = {
    Research: "search",
    "Code Review": "code",
    "Write Tests": "bug_report",
    Deploy: "rocket_launch",
  };

  const fetchAgents = async () => {
    try {
      const data = await api.get<AgentsResponse>("/agents");
      setAllAgents(data.agents || []);
      setActiveAgents(data.active || []);
      setDaemonRunning(data.daemon_running ?? false);
      setConnectionStatus(data.daemon_running ? "Connected" : "No daemon");
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

  const handleSpawn = async (name: string) => {
    if (!name.trim()) return;
    try {
      await api.post("/agents/spawn", {
        name: name.trim(),
        prompt: "",
        model: "sonnet",
        budget: 2.0,
      });
      setShowNewForm(false);
      setNewAgentName("");
      await fetchAgents();
    } catch {
      // handle error silently
    }
  };

  const handleKill = async (name: string) => {
    try {
      await api.post(`/agents/${name}/kill`);
      await fetchAgents();
    } catch {
      // handle error silently
    }
  };

  const formatLastUpdate = () => {
    if (!lastUpdate) return "Never";
    const seconds = Math.floor((Date.now() - lastUpdate.getTime()) / 1000);
    if (seconds < 60) return "Just now";
    return `${Math.floor(seconds / 60)} min ago`;
  };

  // Default template icons for API templates that don't match known names
  const getTemplateIcon = (name: string) => {
    return templateIcons[name] || "smart_toy";
  };

  // Fallback templates if none from API
  const displayTemplates =
    templates.length > 0
      ? templates.map((t) => ({
          icon: getTemplateIcon(t.name),
          name: t.name,
          description: t.content ? t.content.slice(0, 50) : "Agent template",
        }))
      : [
          {
            icon: "search",
            name: "Research",
            description: "Search and summarize information",
          },
          {
            icon: "code",
            name: "Code Review",
            description: "Review code for issues and improvements",
          },
          {
            icon: "bug_report",
            name: "Write Tests",
            description: "Generate test cases for your code",
          },
          {
            icon: "rocket_launch",
            name: "Deploy",
            description: "Automate deployment pipelines",
          },
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

        {/* Status bar */}
        <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-4 flex gap-8 mb-8">
          <div className="flex items-center gap-2 text-sm text-slate-300">
            <span
              className={`w-2 h-2 rounded-full ${
                connectionStatus === "Disconnected"
                  ? "bg-red-500"
                  : daemonRunning
                    ? "bg-green-500"
                    : "bg-yellow-500"
              }`}
            />
            {connectionStatus}
          </div>
          <span className="text-sm text-slate-300">
            {activeAgents.length} Active Session{activeAgents.length !== 1 ? "s" : ""}
          </span>
          <span className="text-sm text-slate-300">
            {allAgents.length} Total Agent{allAgents.length !== 1 ? "s" : ""} (from log)
          </span>
          <span className="text-sm text-slate-300">
            Last Update: {formatLastUpdate()}
          </span>
        </div>

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
                  ? "No daemon running. Agents spawned from the command line will appear here once the daemon starts, or when spawned through the app."
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
                    {agent.model && (
                      <div className="text-slate-400 text-xs mb-2">Model: {agent.model}</div>
                    )}
                    <div className="flex items-center gap-2 mb-2">
                      <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                      <span className="text-green-400 text-xs font-bold">
                        {statusLabel(agent.status)}
                      </span>
                      <span className="text-slate-500 text-xs ml-auto">
                        via {agent.source}
                      </span>
                    </div>

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
                    <div className="flex gap-2 mt-3">
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

                    <div className="flex items-center justify-end mt-4">
                      <div className="flex gap-2">
                        <button
                          onClick={() => handleKill(agent.name)}
                          className="border border-slate-700 text-slate-300 text-sm rounded-lg px-3 py-1 hover:border-slate-500 transition-colors"
                        >
                          Pause
                        </button>
                        <button
                          onClick={() => handleKill(agent.name)}
                          className="border border-slate-700 text-slate-300 text-sm rounded-lg px-3 py-1 hover:border-red-500 hover:text-red-400 transition-colors"
                        >
                          Kill
                        </button>
                      </div>
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

        {activeTab === "Metrics" && (
          <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400 mb-8">
            Metrics coming soon.
          </div>
        )}

        {activeTab === "Automation" && (
          <div className="bg-slate-900/40 border border-slate-800 rounded-xl p-8 text-center text-slate-400 mb-8">
            Automation coming soon.
          </div>
        )}

        {/* Agent Templates - always visible */}
        <h2 className="text-lg font-semibold text-white mb-4">
          Agent Templates
        </h2>
        <div className="grid grid-cols-4 gap-4">
          {displayTemplates.map((tpl) => (
            <div
              key={tpl.name}
              onClick={() => handleSpawn(tpl.name.toLowerCase().replace(/\s+/g, "-"))}
              className="bg-slate-900/40 border border-slate-800 rounded-xl p-4 text-center hover:border-blue-500 transition-colors cursor-pointer"
            >
              <Icon
                name={tpl.icon}
                className="text-3xl text-slate-400 mb-2 mx-auto"
              />
              <p className="text-white font-medium mb-1">{tpl.name}</p>
              <p className="text-slate-400 text-xs">{tpl.description}</p>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
