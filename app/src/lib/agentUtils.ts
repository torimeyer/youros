/**
 * Shared agent utilities extracted from Agents.tsx so the page module
 * only contains React components. Vite Fast Refresh requires that any
 * file with a default-export React component exports ONLY components.
 * Having friendlyAgentName as a named export in Agents.tsx caused HMR
 * to invalidate on every edit and eventually crash vite.
 */

/**
 * Background maintenance agent name prefixes. These run autonomously on
 * cron-like schedules and are never user-actionable, so their completion
 * should not produce a bottom-right toast.
 */
const SYSTEM_AGENT_NAME_PREFIXES = [
  'dupe-guard-',
  'stale-complete-',
  'reaper-',
  'roadmap-',
  'brainstorm-',
]

/**
 * Returns true when the agent name matches a known background maintenance
 * pattern. Used by the notification store to silence completion toasts
 * that the user cannot act on.
 */
export function isSystemMaintenanceAgent(name: string): boolean {
  return SYSTEM_AGENT_NAME_PREFIXES.some((prefix) => name.startsWith(prefix))
}

/**
 * Returns true when this agent is the main Claude/Gemini interactive session
 * (the one Tori is actively typing into), NOT a spawned background agent.
 *
 * Detection: inferred session name pattern + description set by the audit
 * log or session-start hook.
 */
export function isMainSession(agent: { name: string; description?: string; source?: string; task?: string }): boolean {
  const isInferred = /^(claude-code-|gemini-cli-mcp-client-)[0-9a-f0-9]+/.test(agent.name)
  if (!isInferred) return false
  // The audit-log watcher or session hook stamps every main process with this prefix
  const desc = agent.description || ""
  const hasMainDesc = desc.startsWith("Claude Code session") || desc.startsWith("Gemini session")
  return hasMainDesc
}

export interface AgentInfo {
  name: string;
  status: string;
  source: string;
  model?: string;
  budget?: string;
  timestamp?: string;
  spawned_at?: string;
  /** Last heartbeat ISO timestamp from the backend. Used by isAgentActive
   *  to keep an agent visible in the Active list when the stale sweep
   *  raced ahead of a still-working agent. */
  last_heartbeat_at?: string;
  transcript_bytes?: number;
  transcript_lines?: number;
  tokens_used?: number;
  token_limit?: number | null;
  token_usage_pct?: number | null;
  cost_estimate?: number;
  recovery_count?: number;
  max_recoveries?: number;
  role?: string;
  fleet_id?: string;
  fleet_name?: string;
  description?: string;
  /** Human-written task description (e.g. "Fix delete-all 403"). When present
   *  this becomes the primary row title. Falls back to description. */
  task?: string;
  /** First user message from a chat session transcript, pre-extracted by the
   *  backend (stored as `prompt` in agent_metadata for source==="chat"). */
  prompt?: string;
  /** Pre-derived chat title threaded through from the list endpoint. */
  chat_title?: string;
  /** Template name stored at spawn time (e.g. "Roadmap", "pm-roadmap"). */
  template?: string;
  /** True when the spawning template had produces_doc=True. */
  template_produces_doc?: boolean;
  /** Plain-language summary generated at completion for template-run agents. */
  actionable_doc?: string;
  /** OS process ID of the agent subprocess. Null when the agent is not a
   *  direct child of the backend (e.g. external claude-code sessions). */
  pid?: number | null;
  /** Last step description POSTed via /heartbeat. Truncate to 60 chars in UI. */
  current_step?: string | null;
}

/**
 * Determines ghost/alive state for a running agent.
 *
 * Ghost detection rule (keep in sync with AgentGhostBadge in Agents.tsx):
 *   - status === 'abandoned'  → ghost
 *   - status === 'running' AND pid is null/undefined  → ghost
 *   - status === 'running' AND last_heartbeat_at AND now - heartbeat > 120 000 ms  → ghost
 *   - status === 'running' AND pid present AND recent heartbeat  → alive
 *   - any other status  → null (badge not shown)
 */
export function computeAgentGhostState(
  agent: Pick<AgentInfo, "status" | "pid" | "last_heartbeat_at">,
  now: number,
): "ghost" | "alive" | null {
  const active = agent.status === "running" || agent.status === "spawned";
  if (!active && agent.status !== "abandoned") return null;
  if (agent.status === "abandoned") return "ghost";
  if (agent.pid === null || agent.pid === undefined) return "ghost";
  if (
    agent.last_heartbeat_at &&
    now - Date.parse(agent.last_heartbeat_at) > 120_000
  )
    return "ghost";
  return "alive";
}

/**
 * Returns true when this agent was explicitly spawned by the user or a
 * subagent (i.e. it should appear in the Active Sessions list and count
 * toward the sidebar badge and Cancel-all button).
 *
 * Excluded:
 *  - The main Claude Code interactive session (isMainSession)
 *  - Chat-turn records (source === "chat")
 *  - Audit-log entries (source === "audit")
 *  - Hook auto-files (source === "hook")
 *  - Subscription chat rows (model === "claude-code-subscription")
 *  - Pre-registration placeholder rows (hook_preregister === true) — the
 *    PreToolUse hook inserts these the moment a subagent boots, before it
 *    has done any real work. The row is promoted to a real agent once the
 *    subagent calls /register and the hook_preregister flag is cleared.
 *    Showing these rows immediately makes agents appear in the UI before
 *    they have registered, which is confusing.
 */
export function isUserSpawnedAgent(agent: {
  name: string;
  description?: string;
  source?: string;
  model?: string;
  hook_preregister?: boolean;
}): boolean {
  if (isMainSession(agent)) return false;
  if (agent.name.startsWith("myos-api-")) return false;
  if (agent.source === "chat") return false;
  if (agent.source === "audit") return false;
  if (agent.source === "hook") return false;
  // The ostk kernel daemon is a process-level system agent, not user
  // work. Without this guard the Agents nav badge counted 1 right
  // after onboarding even though the user had not spawned anything —
  // just the daemon row bleeding through. Same reasoning as the
  // chat/audit/hook exclusions above.
  if (agent.source === "daemon") return false;
  if (agent.model === "claude-code-subscription") return false;
  // Pre-registration placeholder: the hook fires before the subagent boots
  // and inserts a row with hook_preregister=true. Hide it until the
  // subagent calls /register and the flag is cleared/merged.
  if (agent.hook_preregister) return false;
  return true;
}

/**
 * How fresh a heartbeat must be to consider an agent "still working" for
 * the Active tab, even if the backend stale-sweep has already moved its
 * status to terminated_stale. Two minutes matches the heartbeat cadence
 * the spawn-time mailbox instructions ask agents to keep.
 */
export const ACTIVE_HEARTBEAT_FRESHNESS_MS = 120_000;

/**
 * Returns true when an agent should appear in the Active Sessions list.
 *
 * Cases:
 *  1. status is one of running, spawned, or starting (the agent is alive
 *     by its own report).
 *  2. status is terminated_stale BUT a heartbeat landed within the last
 *     ACTIVE_HEARTBEAT_FRESHNESS_MS. This covers the race where the
 *     stale-sweep flips the row to terminated_stale moments before the
 *     agent's next heartbeat arrives. Without this override, agents
 *     that are very much alive vanish from the page.
 *  3. cancelled and other terminal statuses always return false so the
 *     Recent tab's cancelled-hide chip keeps working.
 *
 * Pure function: takes an optional `now` for deterministic tests.
 */
export function isAgentActive(
  agent: { status: string; last_heartbeat_at?: string },
  now: number = Date.now()
): boolean {
  const status = agent.status;
  if (status === "running" || status === "spawned" || status === "starting") {
    return true;
  }
  if (status === "terminated_stale" && agent.last_heartbeat_at) {
    const hb = Date.parse(agent.last_heartbeat_at);
    if (!Number.isNaN(hb) && now - hb <= ACTIVE_HEARTBEAT_FRESHNESS_MS) {
      return true;
    }
  }
  return false;
}

/**
 * Build the primary display label and an optional secondary subtitle for an
 * agent row.
 *
 * Priority:
 *  1. Fleet agents             -> primary: "Role · fleet-name · HH:MMam/pm",  secondary: null
 *  2. Main session             -> primary: "Your chat with Claude",            secondary: null
 *  3. Task / description       -> primary: task or description (≤60 chars),   secondary: "Model · HH:MMam/pm"
 *  4. Chat session (source === "chat")
 *                              -> primary: chat_title or prompt (≤60 chars),  secondary: "Model · HH:MMam/pm"
 *  5. Named agents             -> primary: "Name · Model · HH:MMam/pm",       secondary: null
 *  6. Inferred cc session      -> primary: "Claude · Model · Chat · HH:MMam/pm", secondary: null
 *  7. Fallback                 -> primary: raw name,                           secondary: null
 *
 * The raw name is NEVER mutated; callers should put it in a tooltip.
 */
export function agentTitleParts(
  agent: Pick<
    AgentInfo,
    | "name"
    | "model"
    | "role"
    | "fleet_name"
    | "fleet_id"
    | "source"
    | "spawned_at"
    | "timestamp"
    | "task"
    | "description"
    | "prompt"
    | "chat_title"
  >
): { primary: string; secondary: string | null } {
  const ts = agent.spawned_at || agent.timestamp;
  const timePart = ts
    ? (() => {
        const d = new Date(ts);
        const h = d.getHours();
        const m = d.getMinutes().toString().padStart(2, "0");
        const ampm = h >= 12 ? "pm" : "am";
        const h12 = ((h % 12) || 12).toString();
        return `${h12}:${m}${ampm}`;
      })()
    : null;

  const modelLabel = (() => {
    const m = agent.model || "";
    if (m.includes("sonnet")) return "Sonnet";
    if (m.includes("opus")) return "Opus";
    if (m.includes("haiku")) return "Haiku";
    return null;
  })();

  // 1. Fleet agent: has role + fleet info
  if (agent.role && (agent.fleet_name || agent.fleet_id)) {
    const parts: string[] = [agent.role, agent.fleet_name || agent.fleet_id!];
    if (timePart) parts.push(timePart);
    return { primary: parts.join(" · "), secondary: null };
  }

  // Helper: build secondary "Model · HH:MMam/pm" string
  const secondaryParts: string[] = [];
  if (modelLabel) secondaryParts.push(modelLabel);
  if (timePart) secondaryParts.push(timePart);
  const secondary = secondaryParts.length > 0 ? secondaryParts.join(" · ") : null;

  // 2. Main session: inferred name + "Claude Code session" description.
  //    Must run before the task/description path so the generic audit-log
  //    description ("Claude Code session (cwd: ...)") never leaks as the title.
  if (isMainSession(agent)) {
    const primary = agent.name.startsWith("gemini-cli-") ? "Your chat with Gemini" : "Your chat with Claude";
    return { primary, secondary: null };
  }

  // 3. Task / description present (explicit human-written title)
  const taskText = (agent.task || agent.description || "").trim();
  if (taskText) {
    const primary = taskText.length > 60 ? taskText.slice(0, 60) : taskText;
    return { primary, secondary };
  }

  // 4. Chat session: use chat_title or prompt as the title
  if (agent.source === "chat") {
    const chatRaw = (agent.chat_title || agent.prompt || "").trim();
    if (chatRaw) {
      const primary = chatRaw.length > 60 ? chatRaw.slice(0, 60) : chatRaw;
      return { primary, secondary };
    }
    // No title yet: fall back to "Chat · Model · time"
    const parts: string[] = ["Chat"];
    if (modelLabel) parts.push(modelLabel);
    if (timePart) parts.push(timePart);
    return { primary: parts.join(" · "), secondary: null };
  }

  // 5. Named (non-inferred) agent registered by name
  const isInferred = /^claude-code-[0-9a-f]{4,}/.test(agent.name);
  if (!isInferred) {
    const parts: string[] = [agent.name];
    if (modelLabel) parts.push(modelLabel);
    if (timePart) parts.push(timePart);
    // Only return a transformed label when there is extra info to add
    if (parts.length > 1) return { primary: parts.join(" · "), secondary: null };
    return { primary: agent.name, secondary: null };
  }

  // 6. Inferred Claude Code session (background subagent without description)
  const labelParts: string[] = ["Claude"];
  if (modelLabel) labelParts.push(modelLabel);
  labelParts.push("Chat");
  if (timePart) labelParts.push(timePart);
  return { primary: labelParts.join(" · "), secondary: null };
}

/**
 * Build a human-readable label for an agent row (single string, for
 * backwards compatibility and simple tooltips).
 *
 * When an agent has a task/description this returns just the primary portion
 * (the secondary subtitle is available via agentTitleParts).
 */
export function friendlyAgentName(
  agent: Pick<
    AgentInfo,
    | "name"
    | "model"
    | "role"
    | "fleet_name"
    | "fleet_id"
    | "source"
    | "spawned_at"
    | "timestamp"
    | "task"
    | "description"
    | "prompt"
    | "chat_title"
  >
): string {
  return agentTitleParts(agent).primary;
}
