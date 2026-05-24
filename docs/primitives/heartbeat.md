# Heartbeat primitive

A myOS primitive (v1.0). Liveness signal for agents: when an agent registered, when it last pinged, when it finished.

## Purpose

Tell other parts of myOS which agents are alive right now, when each was last seen, and when each finished. The agent panel, the auto-completion sweeper, and the ghost reaper all read from heartbeat state.

## Contract

Module: `routers.agents` · Version: v1.0 · Status: active.

Agent lifecycle has three fields stored in `agent_state.json`:

- `spawned_at`: ISO-8601 when the agent registered.
- `last_heartbeat_at`: ISO-8601 when the agent last called /heartbeat.
- `completed_at`: ISO-8601 when the agent called /complete or was auto-swept.

HTTP surface:

- `POST /api/agents/register` → mark the agent running; sets `spawned_at` + initial `last_heartbeat_at`.
- `POST /api/agents/{name}/heartbeat` → refresh `last_heartbeat_at`.
- `POST /api/agents/{name}/complete` → set `completed_at` and final status (`completed`, `failed`, `cancelled`).

## Events emitted

Every register / heartbeat / complete writes a row into the ostk audit stream. The auto-completion sweeper (`agent_watchdog.py`) reads heartbeat staleness; agents whose `last_heartbeat_at` is older than the threshold are marked `completed` with a summary noting the sweep.

## Versioning history

- **v1.0** (2026-05-16): formalized as a primitive. The endpoints have existed for many sessions; this contract freezes them. Pre-existing scars documented in `agents.py:501` (ghost reaper PID check) and the 45-minute heartbeat loop pattern in `register-agent.sh`.

## Worked examples

```bash
# Agent's own registration (from a shell hook)
curl -sk -X POST https://127.0.0.1:8000/api/agents/register \
  -H 'Content-Type: application/json' \
  -d '{"name":"my-agent","task":"do the thing","source":"chat"}'

# Heartbeat every 25 seconds (background loop)
curl -sk -X POST https://127.0.0.1:8000/api/agents/my-agent/heartbeat

# On clean exit
curl -sk -X POST https://127.0.0.1:8000/api/agents/my-agent/complete \
  -H 'Content-Type: application/json' \
  -d '{"status":"completed","summary":"shipped X"}'
```

## What this primitive is NOT

- **Not a process supervisor.** Heartbeat records liveness; it does not start or stop processes.
- **Not transcript storage.** Transcripts live in `transcripts/<name>.md`; heartbeat fields are metadata only.
- **Not time-of-day metadata.** Heartbeat says "the agent was alive this many seconds ago," not "this agent is busy."
- **Not Time.** Time tells you ETA for an op; Heartbeat tells you whether the worker behind that op is still alive. Both can be true at once.
