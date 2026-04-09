import { useState, useEffect, useRef, type KeyboardEvent } from "react";
import Icon from "./Icon";
import { renderMarkdown } from "../lib/markdown";

// AgentChatThread renders a chat transcript for a single agent using the
// same bubble look the main ChatPanel uses: right aligned blue bubbles for
// the user and left aligned bordered bubbles for the agent, with inline
// markdown. It also owns the textarea and Send button at the bottom so the
// Agents page can drop this in where the old inline UI lived.
//
// This exists because the old Agents page rendered nudges as plain text
// lines like "[12:20:08 AM] You: how's it going?" inside a monospace box,
// and replies the same way. Tori asked: "why these arent just regular
// threads like any other chat". They should feel exactly like ChatPanel.

export interface AgentThreadNudge {
  message: string;
  timestamp: string;
  delivery_message?: string;
}

export interface AgentThreadReply {
  message: string;
  timestamp: string;
}

interface AgentChatThreadProps {
  agentName: string;
  nudges: AgentThreadNudge[];
  replies: AgentThreadReply[];
  onSend: (message: string) => Promise<void> | void;
  isSending: boolean;
  errorMessage?: string | null;
  // Timestamp the agent was first registered. Used by the mailbox warning
  // heuristic. Optional so callers that do not track this can skip it.
  agentRegisteredAt?: string;
}

type Entry =
  | { kind: "nudge"; ts: string; data: AgentThreadNudge; index: number }
  | { kind: "reply"; ts: string; data: AgentThreadReply; index: number };

// Heuristic for the "this agent will never reply" warning. If the user has
// sent at least 2 messages, the agent was registered more than 10 minutes
// ago, and there are zero replies, we show an honest warning. The data we
// have is what the backend exposes today. Do not add a backend field.
const MAILBOX_WARNING_MIN_NUDGES = 2;
const MAILBOX_WARNING_MIN_AGE_MS = 10 * 60 * 1000;

function shouldShowMailboxWarning(
  nudges: AgentThreadNudge[],
  replies: AgentThreadReply[],
  agentRegisteredAt?: string,
): boolean {
  if (!agentRegisteredAt) return false;
  if (replies.length > 0) return false;
  if (nudges.length < MAILBOX_WARNING_MIN_NUDGES) return false;
  const registered = Date.parse(agentRegisteredAt);
  if (Number.isNaN(registered)) return false;
  const ageMs = Date.now() - registered;
  return ageMs >= MAILBOX_WARNING_MIN_AGE_MS;
}

export function AgentChatThread({
  agentName,
  nudges,
  replies,
  onSend,
  isSending,
  errorMessage,
  agentRegisteredAt,
}: AgentChatThreadProps) {
  const [input, setInput] = useState("");
  const scrollEndRef = useRef<HTMLDivElement>(null);

  // Interleave nudges and replies by timestamp so the transcript reads top
  // to bottom in chronological order.
  const entries: Entry[] = [
    ...nudges.map((n, index) => ({
      kind: "nudge" as const,
      ts: n.timestamp,
      data: n,
      index,
    })),
    ...replies.map((r, index) => ({
      kind: "reply" as const,
      ts: r.timestamp,
      data: r,
      index,
    })),
  ];
  entries.sort((a, b) => a.ts.localeCompare(b.ts));

  // Scroll the thread to the bottom when new entries arrive so the latest
  // bubble is always visible without making the user scroll. Guarded
  // because jsdom does not implement scrollIntoView and tests would
  // otherwise throw on every mount.
  useEffect(() => {
    if (typeof scrollEndRef.current?.scrollIntoView === "function") {
      scrollEndRef.current.scrollIntoView({ block: "end" });
    }
  }, [entries.length]);

  const showMailboxWarning = shouldShowMailboxWarning(
    nudges,
    replies,
    agentRegisteredAt,
  );

  // Show a thinking indicator when the user is waiting on a reply.
  // True when either a send is in flight (the optimistic nudge is not
  // in the list yet) OR the most recent entry is a nudge with no newer
  // reply. Suppressed when the mailbox warning is showing because then
  // we already know the agent will never reply, so dots would lie.
  // Tori added this after sending "hi!" to a running agent and seeing
  // no indicator at all, thinking she was being ignored.
  const lastEntry = entries[entries.length - 1];
  const awaitingReply =
    !showMailboxWarning && (isSending || lastEntry?.kind === "nudge");

  const handleSendClick = async () => {
    const trimmed = input.trim();
    if (!trimmed || isSending) return;
    // Optimistically clear the input so repeated sends feel snappy. If the
    // send fails the parent will surface an error via errorMessage and the
    // user can retype. Matches the main ChatPanel feel.
    setInput("");
    await onSend(trimmed);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends, Shift+Enter inserts a newline. Matches Slack and the
    // main chat panel.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSendClick();
    }
  };

  return (
    <div
      data-testid={`agent-chat-thread-${agentName}`}
      className="mt-3 flex flex-col"
    >
      {/* Scrollable transcript area. Max height keeps the card from blowing
          out when a long conversation accumulates. */}
      <div
        data-testid={`agent-chat-thread-scroll-${agentName}`}
        className="flex-1 overflow-y-auto px-2 py-3 space-y-3 bg-slate-900/40 border border-slate-800 rounded-lg"
        style={{ maxHeight: "60vh", minHeight: "120px" }}
      >
        {entries.length === 0 && (
          <div className="text-center py-6">
            <Icon name="chat" className="text-3xl text-slate-400 mb-1" />
            <p className="text-slate-400 text-sm">
              Say hi to {agentName}. Messages go straight to the agent.
            </p>
          </div>
        )}

        {entries.map((entry) => {
          if (entry.kind === "nudge") {
            const nudge = entry.data;
            return (
              <div
                key={`n-${nudge.timestamp}-${entry.index}`}
                data-testid="agent-chat-user-row"
                className="group flex flex-col items-end"
              >
                <div className="relative ml-auto max-w-[75%] w-fit">
                  <div
                    data-testid="agent-chat-user-bubble"
                    className="inline-block bg-blue-500/20 text-blue-100 px-4 py-2.5 rounded-2xl rounded-br-sm text-sm whitespace-pre-wrap break-words"
                  >
                    {nudge.message}
                  </div>
                </div>
                {nudge.delivery_message && (
                  <div
                    data-testid="nudge-delivery-status"
                    className="text-[10px] text-slate-500 mt-1 mr-1"
                  >
                    {nudge.delivery_message}
                  </div>
                )}
              </div>
            );
          }
          const reply = entry.data;
          return (
            <div
              key={`r-${reply.timestamp}-${entry.index}`}
              data-testid="agent-chat-assistant-row"
              className="group"
            >
              <div className="flex items-center gap-1.5 mb-1">
                <span className="text-[10px] text-slate-500 font-bold uppercase">
                  {agentName}
                </span>
              </div>
              <div className="relative max-w-[85%] w-fit">
                <div
                  data-testid="agent-chat-assistant-bubble"
                  className="inline-block border px-4 py-3 rounded-xl text-sm text-slate-300 whitespace-pre-line overflow-hidden break-words bg-slate-900 border-slate-800"
                >
                  {renderMarkdown(reply.message)}
                </div>
              </div>
            </div>
          );
        })}
        {awaitingReply && (
          <div
            data-testid="agent-chat-thinking"
            className="flex items-center gap-2 mt-1"
            aria-label={`${agentName} is thinking`}
          >
            <div className="flex items-center gap-1 px-3 py-2 rounded-2xl border border-slate-800 bg-slate-900">
              <span
                className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce"
                style={{ animationDelay: "0ms" }}
              />
              <span
                className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce"
                style={{ animationDelay: "150ms" }}
              />
              <span
                className="w-1.5 h-1.5 bg-slate-400 rounded-full animate-bounce"
                style={{ animationDelay: "300ms" }}
              />
            </div>
            <span className="text-[10px] text-slate-500">
              {agentName} will see this within about a minute
            </span>
          </div>
        )}
        <div ref={scrollEndRef} />
      </div>

      {/* Mailbox warning. Rendered above the input so the user sees it
          before typing. Only shows when our heuristic says the agent will
          never respond, i.e. old agent with multiple unanswered messages. */}
      {showMailboxWarning && (
        <div
          data-testid="agent-chat-mailbox-warning"
          className="mt-2 px-3 py-2 bg-amber-500/10 border border-amber-500/30 rounded-lg text-xs text-amber-300"
        >
          This agent was started before mailbox checking was added. It will
          not see your messages. Cancel and spawn a new one to talk to it.
        </div>
      )}

      {/* Input area. Matches the look of the main chat panel: rounded
          background, focus ring, and a blue Send button that disables when
          the input is empty or while a send is in flight. */}
      <div className="mt-2 flex items-end gap-2">
        <textarea
          data-testid={`agent-chat-input-${agentName}`}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isSending}
          rows={1}
          placeholder={`Message ${agentName}...`}
          className="flex-1 resize-none bg-slate-900 border border-slate-800 rounded-lg px-4 py-2 text-sm text-slate-300 placeholder-slate-500 outline-none focus:ring-2 focus:ring-blue-500/50 disabled:opacity-50"
          style={{ minHeight: "40px", maxHeight: "160px" }}
        />
        <button
          data-testid={`agent-chat-send-${agentName}`}
          onClick={() => void handleSendClick()}
          disabled={isSending || !input.trim()}
          className="p-2 bg-blue-500 hover:bg-blue-600 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1 text-sm"
        >
          {isSending ? (
            <>
              <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              <span>Sending...</span>
            </>
          ) : (
            <>
              <Icon name="send" className="text-lg" />
              <span>Send</span>
            </>
          )}
        </button>
      </div>

      {/* Inline error. Red, under the input, never silent. Per
          feedback_chat_response_silent.md the error path must be visible. */}
      {errorMessage && (
        <p
          data-testid="nudge-error"
          className="text-[11px] text-red-400 mt-1"
          role="alert"
        >
          {errorMessage}
        </p>
      )}
    </div>
  );
}

export default AgentChatThread;
