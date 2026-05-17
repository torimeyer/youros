# Heartbeat primitive

Version: 1.0  
Status: active  
Module: `api/routers/agents.py`  
Routes: `POST /api/agents/register`, `POST /api/agents/{name}/heartbeat`

---

## Purpose

The Heartbeat primitive is how agents tell myOS they exist and that they are still alive. When an agent starts, it registers itself. Every minute or so while it works, it sends a heartbeat. When the Agents page shows you a list of running agents, it is reading Heartbeat data.

Without Heartbeat, myOS has no way to know which agents are working, which have finished, and which have silently crashed.

---

## Contract

### Key functions (callable from Python)

```python
from routers.agents import agent_mailbox_instruction, agent_mailbox_instruction_short

# Returns the full mailbox instruction block — the text injected into every agent
# prompt that tells it how to register, send heartbeats, check its mailbox, and
# signal completion. Include this in every agent spawn prompt.
agent_mailbox_instruction(agent_name: str, model: str = "sonnet") -> str

# Returns a shorter version of the same instructions.
# Use when the prompt length budget is tight.
agent_mailbox_instruction_short(agent_name: str, model: str = "sonnet") -> str
```

### HTTP endpoints

| Method | Path | Body | What it does |
|---|---|---|---|
| POST | `/api/agents/register` | `{name, model, task, source}` | Announces that an agent has started |
| POST | `/api/agents/{name}/heartbeat` | `{step}` | Confirms the agent is still working |

### Registration body fields

| Field | Type | Meaning |
|---|---|---|
| `name` | str | Unique agent name (e.g. `"my-agent-abc123"`) |
| `model` | str | Model used (e.g. `"sonnet"`) |
| `task` | str | One-line description of what the agent is doing |
| `source` | str | Always `"claude-code"` |

### Heartbeat body fields

| Field | Type | Meaning |
|---|---|---|
| `step` | str | What the agent is doing right now (shown in the Agents panel) |

---

## Events emitted

Each registration and each heartbeat writes a state snapshot to `~/.myos/agents.json`. The Agents page polls this file. If an agent goes 15 minutes without a heartbeat, the Agents page marks it as stopped.

---

## Versioning history

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-02 | Initial release. Register, heartbeat, complete, cancel, nudge. |

---

## Worked examples

### Agent registers and sends heartbeats

```bash
# Register
curl -sSk -X POST https://127.0.0.1:8000/api/agents/register \
  -H 'Content-Type: application/json' \
  -d '{"name": "my-agent-abc", "model": "sonnet", "task": "run tests", "source": "claude-code"}'

# Heartbeat every ~60 seconds
curl -sSk -X POST https://127.0.0.1:8000/api/agents/my-agent-abc/heartbeat \
  -H 'Content-Type: application/json' \
  -d '{"step": "running pytest"}'
```

### Include mailbox instructions in a spawn prompt

```python
from routers.agents import agent_mailbox_instruction

agent_name = "my-agent-abc"
prompt = f"Do the thing.\n\n{agent_mailbox_instruction(agent_name)}"
```

---

## What this primitive is NOT

- **Not a process monitor.** Heartbeat does not check whether the agent's OS process is alive. It only tracks whether the agent has posted a heartbeat recently.
- **Not authentication.** Any code that knows the agent name can send a heartbeat. There is no token or secret.
- **Not a message queue.** For sending messages to a running agent, use the nudge endpoints (`POST /api/agents/{name}/nudge`, `GET /api/agents/{name}/nudges`).
