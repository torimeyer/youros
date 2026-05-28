# ToriChat Depth vs Claude Code: Root Cause Audit

**Task:** →1781  
**Date:** 2026-05-28  
**Investigator:** diagnose-torichat-depth-1781  
**Trigger:** ToriChat said "I have used 6 search rounds on this and I am not yet done. Tell me the specific file, doc, or Task to look at and I will go straight there." — after a sidebar overlay question that the Claude Code parent solved inline in 3 reads.

---

## TL;DR

ToriChat (Anthropic API path) has a hard cap of **6 read-only tool rounds** per turn. Claude Code (parent) has no cap and richer search tools. A question that needs 6+ targeted lookups hits the wall and produces the "I give up, guide me" message. This is a deliberate cost-control feature (`→1699`) that fires too aggressively on genuine multi-hop investigations.

---

## Finding 1: The Hard Cap — `MAX_TOOL_ROUNDS = 6`

**File:** `api/services/chat_providers.py:840`

```python
# Tighter cap for read-only discovery loops (→1699). When the model has burned
# MAX_TOOL_ROUNDS turns making only read/search calls with no file edits, we
# force a text-only synthesis response instead of allowing it to keep
# rediscovering the same facts across expensive subscription round-trips.
MAX_TOOL_ROUNDS = 6
```

**Where it fires:** `chat_providers.py:3195`

```python
if turn >= MAX_TOOL_ROUNDS and not files_modified:
```

When this triggers, the system:

1. Calls the Anthropic API **without** the `tools=` argument — forcing a text-only answer.
2. If the model produces text, that text is shown (the model usually writes its own "I need more info" paragraph).
3. If the model produces **no text**, the hardcoded fallback fires (`chat_providers.py:3221`):

```python
f"I've used {MAX_TOOL_ROUNDS} search rounds on this and I'm not yet done. "
"Tell me the specific file, doc, or Task to look at and I'll go straight there."
```

Tori saw a slight paraphrase of this — the model itself produced the message in the forced text-only call, drawing on conversation context that showed it had run 6 rounds.

**This cap ONLY applies to the Anthropic API path (`stream_anthropic`).** The Claude Code CLI path (`claude_code_provider.stream_chat`) has no such limit — it runs the subprocess until done, with a 1800-second safety timeout (`claude_code_provider.py:_STREAM_TIMEOUT_SECONDS`).

---

## Finding 2: Tool Quality Gap

ToriChat and Claude Code use different tool sets with very different information density per call.

### ToriChat tools (defined in `api/services/tool_executor.py`)

| Tool | What it does | Limitation |
|------|--------------|------------|
| `search_files` (`tool_executor.py:747`) | `grep -rn` on workspace | Returns only **matching lines**, max **100 lines** (`tool_executor.py:772`). No context lines. No semantic mode. |
| `read_file` (`tool_executor.py:647`) | Reads full file | **50,000 char cap** (`tool_executor.py:660`). No offset/limit — must read the whole file. |
| `run_command` (`tool_executor.py:704`) | Arbitrary shell | 30-second timeout, workspace-boundary restriction. Output capped at 30,000 chars. |
| `list_directory` | Lists dir entries | Shows names only, max 200 entries. |

### Claude Code (parent) tools via ostk MCP

| Tool | What it does | Advantage |
|------|--------------|-----------|
| `mcp__ostk__search` | Regex + semantic + symbol modes | Returns **content blocks** not just matching lines; `expand=callers/semantic` available |
| `mcp__ostk__read` | Reads file with `offset` + `limit` | Can read **targeted sections** — no need to load 4,000 lines to find 20 |
| `mcp__ostk__bash` | Arbitrary shell via kernel | No workspace boundary; compound commands (`find … | grep … | head`) in one call |

### Why 6 rounds is not enough for grep-based tools

A question like "why does torichat stop after 6 searches?" requires this chain on the grep path:

1. `search_files("MAX_TOOL_ROUNDS")` — finds the constant (1 round)
2. `read_file("api/services/chat_providers.py")` — reads the 4,352-line file to get context (1 round, hits 50KB cap, may truncate)
3. `search_files("turn >= MAX_TOOL_ROUNDS")` — finds where it fires (1 round)
4. `read_file(...)` again for the block around line 3195 (1 round, same truncation problem)
5. `search_files("files_modified")` — understands the condition (1 round)
6. `read_file(...)` for the fallback message (1 round)

**Total: 6 rounds. Cap fires. Investigation incomplete.**

Claude Code does the same in ~2 calls:
- `search(query="MAX_TOOL_ROUNDS", mode=content)` — returns content with context lines, hits all relevant sites
- `read(path=..., offset=836, limit=10)` — reads exactly lines 836–845

---

## Finding 3: System Prompt Mixed Signals

`api/services/chat_providers.py:1654` defines `_system_prompt()`. Three instructions are in tension:

**"Use minimum tools"** (lines 1796–1800):
> TOOL CONSERVATION: For chat questions, use the MINIMUM number of tool calls needed. Do NOT browse directories exploratorily. Do NOT run multiple searches when one will do.

**"Stop when running low"** (lines 1808–1811):
> STEP LIMIT: If you are running low on steps and the task is not yet done, stop and tell the user what you found. Ask them to narrow the request or give you the exact file path.

**"Always investigate before editing"** (lines 1782–1790):
> INVESTIGATE BEFORE EDITING: you MUST read the actual component file before making any edit.

TOOL CONSERVATION causes the model to start with narrow 1-2 call searches. Grep returns only matching lines with no surrounding context, so the model cannot build a complete picture and must search again. By turn 5 it knows it is running low (STEP LIMIT). On turn 6 the cap fires and forces the "I am not done yet" message. The instructions push the model toward exactly the search pattern that runs out the fastest.

---

## Finding 4: Which Path Triggers This

The `MAX_TOOL_ROUNDS` cap lives inside `stream_anthropic` (line 2293). This method runs when:
- Backend resolves to `anthropic_api` (API key configured, or `force_api=True`)
- Backend is NOT `claude_code` (CLI unavailable, or message contains images that force API path)

When backend resolves to `claude_code` (`chat_providers.py:2345`), the call goes to `claude_code_provider.stream_chat` which has zero turn limit. The CLI manages its own agent loop with no imposed ceiling.

---

## Fix Proposals

### Fix A — Raise `MAX_TOOL_ROUNDS` from 6 to 15 (`chat_providers.py:840`)

**Effort:** 1 line. **Risk:** Low.

```python
MAX_TOOL_ROUNDS = 15  # was 6 (→1699); raised because 6 fires on genuine multi-hop investigations
```

Tradeoff: up to 15 Anthropic API calls per pure-discovery turn instead of 6. At cached prompt rates the cost increase is small. The existing `MAX_AGENT_TURNS = 40` outer cap prevents runaway loops.

**Filed as Task →1782.**

### Fix B — Add context lines to `search_files` (`tool_executor.py:747–773`)

**Effort:** ~5 lines. **Risk:** Low.

Change grep to include `-A 3 -B 3` (3 context lines per match) and raise the result limit from 100 to 250 lines. More information per round means fewer rounds needed. A single `search_files("MAX_TOOL_ROUNDS")` with context would show the constant AND the comment explaining it — cutting the first two steps to one call.

**Filed as Task →1783.**

### Fix C — Expose `mcp__ostk__search` as a ToriChat tool when ostk is available (`chat_providers.py`, `tool_executor.py`)

**Effort:** ~50 lines. **Risk:** Medium (requires ostk socket at runtime for uvicorn worker).

Add a `semantic_search` tool to `TOOL_DEFINITIONS` that calls `mcp__ostk__search` via the ostk socket when available. A single `semantic_search("read-only discovery cap")` would surface `chat_providers.py:840` directly with full context — collapsing 6 rounds to 1. Highest leverage but requires the ostk socket to be alive in the API process.

**Filed as Task →1784.**

---

## Summary Table

| Dimension | ToriChat (Anthropic API path) | Claude Code (parent) |
|-----------|-------------------------------|----------------------|
| Tool round cap | **6** read-only rounds | **None** |
| Search quality | grep regex, 100 lines, no context | regex + semantic + symbol + content blocks |
| Read strategy | Full file, 50KB cap, no offset | Offset + limit, targeted sections |
| Shell access | `run_command`, 30s, workspace bound | `mcp__ostk__bash`, unrestricted |
| Rounds needed for complex investigation | 6–10 | 2–3 |
| What happens at cap | Forced text-only call, asks user for guidance | Never fires |

---

## Verified against the codebase

| Claim | Evidence |
|-------|----------|
| `MAX_TOOL_ROUNDS = 6` | `api/services/chat_providers.py:840` |
| Cap fires at `turn >= 6` with no file edits | `api/services/chat_providers.py:3195` |
| Fallback message text | `api/services/chat_providers.py:3221–3222` |
| Claude Code path has no cap | `api/services/claude_code_provider.py:_STREAM_TIMEOUT_SECONDS = 1800.0` |
| `search_files` is grep-only, 100-line limit | `api/services/tool_executor.py:747–773` |
| `read_file` has no offset/limit | `api/services/tool_executor.py:647–679` |
| System prompt TOOL CONSERVATION instruction | `api/services/chat_providers.py:1796–1800` |
| System prompt STEP LIMIT instruction | `api/services/chat_providers.py:1808–1811` |
| `MAX_AGENT_TURNS = 40` (outer cap, unrelated) | `api/services/chat_providers.py:834` |
