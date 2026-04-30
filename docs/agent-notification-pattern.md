# Agent Notification Pattern

When a parent session spawns a background agent and says "I'll let you know when it commits," it needs to arm a Monitor. Without one, the parent session has no way to receive a notification and must wait for the user to ask.

## The gap that caused the 901 miss

Agent `custom-verbs-ui-in-settings-901-3def5b` committed its work and finished, but the parent was never notified. The parent said "I'll let you know when it commits" — but no Monitor was armed, so no notification arrived. The user had to ask before the parent checked.

## How notifications work

The backend (`/api/agents/{name}/complete`) stores completion status and emits an audit event, but it does NOT push any notification to the parent session. There is no WebSocket or SSE channel the parent subscribes to. Notifications reach the parent only through a Monitor tool call that polls until done.

## Correct pattern

Immediately after spawning a background agent, arm a Monitor in the same turn:

```
Monitor command:
  bash scripts/spawn-monitor.sh <agent-name> <agent-name> 60

Monitor timeout_ms: <estimated max runtime in ms>
```

`spawn-monitor.sh` is a thin wrapper over `scripts/monitor-agent.sh`. It exits (with DONE in output) when:

1. The API reports `status=completed` for the agent, OR
2. The agent leaves the running list AND `git log` contains the agent name as a commit sentinel

Both signals are checked so a commit that lands before the API row clears still triggers the notification.

## Auto-mode (bridge-spawned agents)

When `task-isolation-bridge.sh` redirects a Task call, it writes the agent name to `.ostk/pending-monitor-spawns.jsonl`. You can arm a monitor without knowing the name explicitly:

```
Monitor command:
  bash scripts/spawn-monitor.sh
```

This reads the latest entry from the pending spawns file.

## Hook reminder

`auto-monitor-spawn.sh` fires on every PostToolUse:Agent call and prints a banner reminding you to arm a Monitor. The banner includes the exact command to copy.

## Why not arm the Monitor automatically?

The parent session needs to know the agent's expected runtime to set `timeout_ms` correctly. A generic auto-arm would either time out too early (for long agents) or hold the Monitor forever (for stuck agents). The parent is best placed to set this based on what it just spawned.
