/**
 * Chat-agent filter for the Agents Recent tab (→1505).
 *
 * Chat-sourced agents (source==="chat") and bare inferred Claude Code
 * sessions that received no task description (rendered as "Claude · Chat ·
 * time" by agentTitleParts) are transient and clutter the Recent list.
 * This module provides:
 *
 *   useChatFilter()  — React hook; manages show/hide state + persistence
 *   ChatFilterChip   — toggle chip matching the existing "Hiding cancelled"
 *                      style used in Agents.tsx
 *   isChatAgent      — pure predicate; true for agents that should be hidden
 */
import { useState, useCallback } from "react";
import type { AgentInfo } from "../lib/agentUtils";

const STORAGE_KEY = "agents.showChatAgents";

/**
 * Returns true when this agent is a transient chat-turn or bare inferred
 * session that the user typically does not want in their Recent list.
 *
 * Matches:
 *  1. source === "chat"  — registered by register_chat_session()
 *  2. Inferred claude-code-<hash> name with no task/description — these
 *     render as "Claude · Chat · time" in the UI and belong to the chat
 *     sidebar, not to a user-spawned background task.
 */
export function isChatAgent(
  agent: Pick<AgentInfo, "source" | "name" | "task" | "description">
): boolean {
  if (agent.source === "chat") return true;
  // Inferred session name + no human-written task or description
  const isInferred = /^claude-code-[0-9a-f]{4,}/.test(agent.name);
  if (isInferred && !agent.task && !agent.description) return true;
  return false;
}

interface ChatFilterState {
  showChat: boolean;
  toggleShowChat: () => void;
}

/**
 * Hook that manages chat-agent visibility. Default is hidden (false).
 * Persists the user's preference to localStorage.
 */
export function useChatFilter(): ChatFilterState {
  const [showChat, setShowChat] = useState<boolean>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === "true";
    } catch {
      return false;
    }
  });

  const toggleShowChat = useCallback(() => {
    setShowChat((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(STORAGE_KEY, String(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  return { showChat, toggleShowChat };
}

interface ChatFilterChipProps {
  showChat: boolean;
  hiddenCount: number;
  onToggle: () => void;
}

/**
 * Toggle chip that matches the "Hiding cancelled / Showing cancelled"
 * style used in the Agents Recent tab.
 */
export function ChatFilterChip({
  showChat,
  hiddenCount,
  onToggle,
}: ChatFilterChipProps) {
  if (hiddenCount === 0 && !showChat) return null;
  return (
    <button
      type="button"
      onClick={onToggle}
      data-testid="recent-chat-toggle"
      aria-label={showChat ? "Hide chat agents" : `Show ${hiddenCount} hidden chat agent${hiddenCount === 1 ? "" : "s"}`}
      className="inline-flex items-center gap-2 text-xs text-slate-700 dark:text-slate-300 bg-slate-100 dark:bg-slate-800/70 hover:bg-slate-200 dark:hover:bg-slate-700/70 border border-slate-200 dark:border-slate-700 rounded-full px-3 py-1 transition-colors"
    >
      {showChat ? (
        <>
          <span>Showing chat agents</span>
          <span className="text-slate-500">.</span>
          <span className="text-sky-400">hide</span>
        </>
      ) : (
        <>
          <span>Hiding chat agents ({hiddenCount})</span>
          <span className="text-slate-500">.</span>
          <span className="text-sky-400">show</span>
        </>
      )}
    </button>
  );
}
