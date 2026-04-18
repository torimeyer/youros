# Inline Agent Chat: 1-2s Reply Feasibility

Research question: can a message typed into an active agent from the
Agents page chat bubble or ChatPanel produce a visible reply within
1-2 seconds, without hurting the agent's real work?

## End-to-end path today

1. User hits send in `app/src/pages/Agents.tsx` (`handleNudge`), which
   calls `POST /api/agents/{name}/nudge`.
2. `nudge_agent` in `api/routers/agents.py:5362` writes the nudge to
   disk, touches a signal file, wakes any long-poll waiters, and tries
   to write the message to the subagent's stdin if the pipe is still
   open.
3. The subagent (one-shot `claude --print`) only checks its mailbox
   between tool calls. Poll cadence is adaptive: `MAILBOX_FAST_POLL =
   10s`, `MAILBOX_SLOW_POLL = 60s` (agents.py:214-215).
4. A side coroutine (`api/services/chat_ack_bot.py`) polls `/nudges`
   every 2s and writes a canned warm reply the moment a new nudge
   lands (`ACK_POLL_INTERVAL_SECONDS = 2.0`).
5. Real reply: the subagent eventually calls `POST /reply`, which
   wakes long-poll waiters and writes to disk.
6. Frontend fetches `/nudges` on a short poll every 3s (active agents)
   or 5s (expanded card). It does NOT use the backend's `wait=`
   long-poll parameter (Agents.tsx:2427, 1985, 2022).

## Where the 1-2s budget breaks

- **Frontend poll is the biggest hole.** It short-polls every 3-5s, so
  even after a reply lands on disk instantly, the UI can take up to
  3-5s to show it. Add network jitter and the realistic ceiling today
  is ~5s for ack, much longer for the real reply.
- **The subagent's real reply is bounded by its current tool.** If it
  is mid-pytest, mid-tsc, or mid-large-Edit, it physically cannot
  check the mailbox. That window is tens of seconds to minutes. Claude
  Code subagents have no async-interrupt hook: the model only reads
  input between inference turns, not mid-tool.
- **Envelope on stdin helps but does not guarantee anything.** The
  model still has to reach the next turn boundary before it acts.

## Feasibility verdict

**CONDITIONAL YES** for a visible "received, working on it" response.
**NO** for the substantive reply within 1-2s.

Reasoning:

- The canned ack path already hits the 2s bar server-side through
  `chat_ack_bot`. The only thing blocking the user from seeing it
  within 1-2s is the frontend's 3-5s short-poll. Switching the
  frontend to use the backend's existing `wait=30&since=<ts>`
  long-poll closes that gap to sub-second for ack delivery.
- A substantive reply from the agent itself in 1-2s is physically
  impossible whenever the agent is inside any tool call. Claude Code
  does not expose a mid-tool interrupt.

## Tradeoffs

- **Does not hurt the agent**: frontend long-poll, ack bot (separate
  coroutine, separate canned text, zero tokens from the main agent),
  stdin envelope (already exists).
- **Hurts the agent**: cancelling its current tool to make it answer
  immediately, pausing inference, or routing its turn budget through
  a fast-responder. Any of these eat the real work.
- **Grey zone**: a parallel Haiku "fast-responder" that reads the
  nudge + recent transcript and drafts a plausible answer. Costs
  extra tokens but runs in its own process, so the main agent is
  untouched. Risk: the fast answer can contradict what the main
  agent actually does, which is worse than a slower honest reply.

## Minimum-viable experiment

Switch `fetchNudges` in `Agents.tsx:2427` from plain GET to the
existing long-poll (`?wait=30&since=<latest_ts>`) for one agent, send
a nudge from the UI, and time from send-click to ack bubble visible.
Expected: under 1 second end-to-end. No backend changes needed, no
agent behavior changes, no token cost. If that hits the target, the
1-2s ceiling for "received" is reachable today. The substantive reply
stays bounded by whatever tool the agent is in, which is the
unavoidable floor.
