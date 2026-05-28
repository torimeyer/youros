# Skill primitive

A yourOS primitive (v1.0). Named, invocable capabilities surfaced as slash commands in chat and callable via the API.

## Purpose

Make discrete actions ("paste a giphy", "clear chat history", "open my memory") first-class and consistent. A skill is a name, a description, and a backend handler. Chat exposes them as `/<name>` slash commands; other surfaces can call the same handler over HTTP.

## Contract

Module: `routers.skills` · Version: v1.0 · Status: active.

```python
# Backend
router = APIRouter(tags=["skills"])

@router.post("/skills/run", response_model=SkillRunResponse)
async def run_skill(payload: SkillRunRequest) -> SkillRunResponse
```

Skill manifest (frontend, frozen here for documentation): `app/src/lib/slashCommands.ts`. Adding a skill = manifest entry + backend handler in `routers/skills.py`.

HTTP surface:

- `POST /api/skills/run` → executes a named skill, returns the result the chat should render.

## Events emitted

Each skill run writes an audit row through ostk `note`. Successful results are returned to the caller; failures raise HTTPException with the failure detail.

## Versioning history

- **v1.0** (2026-05-16, →1394): initial release. 10 built-in skills: `/giphy`, `/memory`, `/clear`, plus 7 others. Slash-command popover with type-ahead matching.

## Worked examples

```python
# From the chat backend, when a user types "/giphy cats"
import httpx
resp = await httpx.AsyncClient().post(
    "https://127.0.0.1:8000/api/skills/run",
    json={"name": "giphy", "args": {"query": "cats"}},
)
result = resp.json()  # {"render": "...", "kind": "image" | "text"}

# From a subagent that wants to clear its own chat:
await httpx.AsyncClient().post(
    "https://127.0.0.1:8000/api/skills/run",
    json={"name": "clear"},
)
```

## What this primitive is NOT

- **Not the slash-command UI itself.** The popover is a renderer; the skill is the action.
- **Not an arbitrary code-runner.** Each skill is a named, registered handler. You can't pass arbitrary code through `/skills/run`.
- **Not a workflow.** A skill is one shot. Multi-step automation belongs to the workflow primitive (future).
