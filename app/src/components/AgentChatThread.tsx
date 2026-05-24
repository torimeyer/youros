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
  // "stdin" = message delivered directly to the subprocess stdin now.
  // "file_only" = message saved to the mailbox file for the next poll.
  // Absence means the nudge predates this field (treat as file_only).
  delivery?: "stdin" | "file_only" | string;
}

export interface AgentThreadReply {
  message: string;
  timestamp: string;
  // "ack" = warm canned reply from the ack bot ("On it, give me a
  //   sec.") that confirms receipt within 2s while the real subagent
  //   is mid tool call.
  // "real" = substantive reply from the live subagent.
  // "conversational" = full LLM reply generated server-side in
  //   response to the user's nudge (needle 857 pause-and-chat mode).
  //   Rendered like "real" but with a small "AI" label so the user
  //   knows the answer came from a server-side call, not the subagent.
  // Absent = legacy record from before the field existed; treat as
  //   "real" so we never hide a real answer behind ack styling.
  kind?: "ack" | "real" | "conversational" | string;
}

interface AgentChatThreadProps {
  agentName: string;
  nudges: AgentThreadNudge[];
  replies: AgentThreadReply[];
  onSend: (message: string) => Promise<void> | void;
  onCorrect?: (message: string) => Promise<void> | void;
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
  onCorrect,
  isSending,
  errorMessage,
  agentRegisteredAt,
}: AgentChatThreadProps) {
  const [input, setInput] = useState("");
  const scrollEndRef = useRef<HTMLDivElement>(null);

  // Interleave nudges and replies by timestamp so the transcript reads top
  // to bottom in chronological order. Render-time dedupe is the regression
  // guard for the screenshot Tori captured where a single user message
  // rendered twice in a row and a single ack rendered three times. Even
  // when the parent page's merge logic and the backend ack bot both refuse
  // to emit duplicates, the chat surface itself must never paint the same
  // bubble twice within one render. We dedupe by ``role:timestamp:message``
  // so two genuinely distinct messages with the same text but different
  // timestamps still both render. Identical role+ts+text is treated as
  // the same logical bubble and collapses to one.
  const rawEntries: Entry[] = [
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
  rawEntries.sort((a, b) => a.ts.localeCompare(b.ts));
  const seenKeys = new Set<string>();
  const entries: Entry[] = [];
  for (const entry of rawEntries) {
    const text =
      entry.kind === "nudge" ? entry.data.message : entry.data.message;
    const key = `${entry.kind}:${entry.ts}:${text}`;
    if (seenKeys.has(key)) continue;
    seenKeys.add(key);
    entries.push(entry);
  }

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
  // substantive reply. An ack bot reply (kind="ack") is just a receipt,
  // not an answer, so we keep the dots up until a real or conversational
  // reply lands. Suppressed when the mailbox warning is showing because
  // then we already know the agent will never reply, so dots would lie.
  const lastEntry = entries[entries.length - 1];
  const lastEntryIsAck =
    lastEntry?.kind === "reply" && lastEntry.data.kind === "ack";
  const awaitingReply =
    !showMailboxWarning &&
    (isSending || lastEntry?.kind === "nudge" || lastEntryIsAck);

  // Banner copy under the thinking dots. Prefer the backend's
  // delivery_message when present. It knows the ground truth:
  //   stdin delivery  -> "Sent. The agent should respond shortly."
  //   file_only + parked long-poll -> "Sent. ... within a second."
  //   file_only + no parked poller -> "Saved. The agent will pick this
  //     up on its next mailbox check, up to 60 seconds from now."
  // This avoids the old optimistic lie that promised "within a couple
  // of seconds" for agents that are mid tool chain and not polling.
  // Fallback copy covers old nudges that predate the delivery_message
  // field or rare cases where the backend did not set it.
  const lastNudge =
    lastEntry?.kind === "nudge" ? lastEntry.data : undefined;
  const lastNudgeDelivery = lastNudge?.delivery;
  const backendCopy = lastNudge?.delivery_message;
  const fallbackCopy =
    lastNudgeDelivery === "stdin"
      ? "Sent. The agent has it now."
      : `Sent. ${agentName} will pick this up on its next mailbox check.`;
  const awaitingCopy = backendCopy || fallbackCopy;

  const handleSendClick = async () => {
    const trimmed = input.trim();
    if (!trimmed || isSending) return;
    // Optimistically clear the input so repeated sends feel snappy. If the
    // send fails the parent will surface an error via errorMessage and the
    // user can retype. Matches the main ChatPanel feel.
    setInput("");
    await onSend(trimmed);
  };

  const handleCorrectClick = async () => {
    const trimmed = input.trim();
    if (!trimmed || isSending || !onCorrect) return;
    setInput("");
    await onCorrect(trimmed);
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
            const isCorrection = nudge.message.startsWith("[CORRECTION]");
            const displayMessage = isCorrection
              ? nudge.message.replace(/^\[CORRECTION\]\s*/, "")
              : nudge.message;
            return (
              <div
                key={`n-${nudge.timestamp}-${entry.index}`}
                data-testid="agent-chat-user-row"
                className="group flex flex-col items-end"
              >
                {isCorrection && (
                  <div className="text-[10px] text-amber-400 mr-1 mb-1 flex items-center gap-1">
                    <Icon name="edit" size={12} />
                    Correction
                  </div>
                )}
                <div className="relative ml-auto max-w-[75%] w-fit">
                  <div
                    data-testid="agent-chat-user-bubble"
                    className={`inline-block px-4 py-2.5 rounded-2xl rounded-br-sm text-sm whitespace-pre-wrap break-words ${
                      isCorrection
                        ? "bg-amber-500/20 text-amber-100 border border-amber-500/30"
                        : "bg-blue-500/20 text-blue-100"
                    }`}
                  >
                    {displayMessage}
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
          // Three reply kinds:
          // "ack"            — canned ack bot receipt (2s, lighter bubble)
          // "conversational" — server-side LLM reply (needle 857)
          // "real" / absent  — substantive reply from the live subagent
          const isAck = reply.kind === "ack";
          const isConversational = reply.kind === "conversational";
          if (isAck) {
            return (
              <div
                key={`r-${reply.timestamp}-${entry.index}`}
                data-testid="agent-chat-ack-row"
                className="group"
              >
                <div className="flex items-center gap-1.5 mb-1">
                  <span
                    data-testid="agent-chat-ack-label"
                    className="text-[10px] text-slate-500 font-medium"
                  >
                    Agent received your message
                  </span>
                </div>
                <div className="relative max-w-[75%] w-fit">
                  <div
                    data-testid="agent-chat-ack-bubble"
                    className="inline-block border px-3 py-2 rounded-xl text-xs italic text-slate-400 whitespace-pre-line overflow-hidden break-words bg-slate-900/60 border-slate-800/60"
                  >
                    {reply.message}
                  </div>
                </div>
              </div>
            );
          }
          if (isConversational) {
            return (
              <div
                key={`r-${reply.timestamp}-${entry.index}`}
                data-testid="agent-chat-conversational-row"
                className="group"
              >
                <div className="flex items-center gap-1.5 mb-1">
                  <span className="text-[10px] text-slate-500 font-bold uppercase">
                    {agentName}
                  </span>
                  <span
                    data-testid="agent-chat-conversational-label"
                    className="text-[9px] text-blue-400/70 border border-blue-400/30 rounded px-1 py-0.5 font-medium tracking-wide"
                    title="This reply was generated by AI on behalf of the agent while it works."
                  >
                    AI
                  </span>
                </div>
                <div className="relative max-w-[85%] w-fit">
                  <div
                    data-testid="agent-chat-conversational-bubble"
                    className="inline-block border px-4 py-3 rounded-xl text-sm text-slate-300 overflow-hidden break-words bg-slate-900 border-slate-700/60"
                  >
                    {renderMarkdown(reply.message)}
                  </div>
                </div>
              </div>
            );
          }
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
                  className="inline-block border px-4 py-3 rounded-xl text-sm text-slate-300 overflow-hidden break-words bg-slate-900 border-slate-800"
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
              {awaitingCopy}
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
        {onCorrect && (
          <button
            data-testid={`agent-chat-correct-${agentName}`}
            onClick={() => void handleCorrectClick()}
            disabled={isSending || !input.trim()}
            className="p-2 bg-amber-500/80 hover:bg-amber-500 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1 text-sm border border-amber-400/30"
            title="Send as a correction. The agent will treat this as a course change, not a follow-up."
          >
            <Icon name="edit" className="text-lg" />
            <span>Correct</span>
          </button>
        )}
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
