# Skill primitive

Version: 1.0  
Status: active  
Module: `api/routers/skills.py`  
Routes: `POST /api/skills/run`

---

## Purpose

The Skill primitive runs a named skill — a reusable instruction set that Claude Code loads and follows. Skills are files that live in `~/.claude/skills/` or inside plugins. When you type `/tdd` or `/brainstorm`, a skill is what gets invoked. The Skill primitive is the one door through which all skill execution passes.

---

## Contract

### Inputs and outputs

```python
from routers.skills import run_skill, SkillRunRequest, SkillRunResponse

# SkillRunRequest — what you send when asking myOS to run a skill
class SkillRunRequest(BaseModel):
    skill_id: str       # the skill name, e.g. "tdd" or "brainstorm"
    args: str = ""      # optional extra text passed to the skill
    job_id: str = ""    # optional caller-assigned ID for tracking

# SkillRunResponse — what comes back immediately
class SkillRunResponse(BaseModel):
    job_id: str         # echoed back (or auto-generated if you didn't provide one)
    status: str         # "queued" — skill runs in the background
```

### HTTP endpoint

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/skills/run` | `SkillRunRequest` JSON | `SkillRunResponse` JSON |

The response arrives immediately. The skill itself runs asynchronously in a Claude Code subprocess. Track progress via the Agents panel.

---

## Events emitted

When a skill starts, a Claude Code subprocess is launched. That subprocess registers itself as an agent via the Heartbeat primitive (`POST /api/agents/register`). From that point on, all progress is visible in the Agents panel.

---

## Versioning history

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-03 | Shipped as part of v3.18.0. `run_skill` with async subprocess invocation. |

---

## Worked examples

### Run a skill via HTTP

```
POST /api/skills/run
{"skill_id": "tdd", "args": "add login button", "job_id": "my-job-1"}

→ {"job_id": "my-job-1", "status": "queued"}
```

### Run a skill from Python

```python
from routers.skills import SkillRunRequest, run_skill

request = SkillRunRequest(skill_id="brainstorm", args="dark mode toggle")
response = await run_skill(request)
print(response.job_id, response.status)  # my-job-id  queued
```

---

## What this primitive is NOT

- **Not a plugin system.** Skills are Markdown instruction files, not installable code packages. They guide Claude; they do not run arbitrary code themselves (the Claude subprocess does that).
- **Not synchronous.** The HTTP response comes back before the skill finishes. The skill runs in the background.
- **Not a task queue.** There is no retry logic, no priority, and no delay scheduling. If you need a queue, use ostk's work/needle system.
