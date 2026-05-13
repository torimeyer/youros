# →1291: Phantom "agent is running" banner

## Symptom
Chat shows `"<name>-fix is running"`. `curl /api/agents` returns no such agent.
Banner never disappears.

## Root cause
`ChatPanel.tsx` adds the agent name to `trackedAgents` when it observes the
`spawn_agent` `tool_use` frame (line ~1047-1063). It never reconciles the
entry against the matching `tool_result`. If the backend rejects the spawn
(HTTP 409 lock conflict, 400 validation error, name collision with a
previously-completed agent, network error), the agent never registers and
never appears in the running WS snapshot. The `seenRunningRef` gate
(line ~1543) then permanently protects the entry from cleanup, because the
filter only drops names that have been observed running at least once.

## Fix
In the `tool_result` handler, when `matchedToolName === 'spawn_agent'` and
the result string indicates failure (does not start with `"Agent '<name>'
spawned"`), drop the entry from `trackedAgents`. Mirrors the existing
success-detection pattern used for `create_calendar_event` (line ~1160).

## Test
`AgentChatThread.phantom.test.tsx` (or `ChatPanel.phantomAgent.test.tsx`):
fires a `tool_use` frame for `spawn_agent` with name "agents-layout-fix",
then a `tool_result` frame with a failure string, then asserts the
`agent-running-banner` testid is not present.
