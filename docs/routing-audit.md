# Model Routing Audit

Date: 2026-04-21

## Call-site table

| Call site | File | Current default | Risk tier | Recommended default |
|-----------|------|----------------|-----------|---------------------|
| `spawn_agent` MODEL_MAP | `api/routers/agents.py:2976` | `"sonnet"` in schema (was `claude-opus-4-6` for opus alias) | routine | Sonnet (done: opus alias now → 4-7) |
| `spawn_fleet` MODEL_MAP | `api/routers/agents.py:4666` | same MODEL_MAP | routine | Sonnet (no change needed) |
| `register-agent.sh` MODEL_MAP | `.claude/hooks/register-agent.sh:97` | reads `.ostk/current_model` as fallback | **was leaking Opus** | fixed: `current_model` now `claude-sonnet-4-6` |
| `session-start.sh` | `.claude/hooks/session-start.sh:31` | reads session model, falls back to `claude-sonnet-4-6` | routine | already correct |
| `stream_anthropic` | `api/services/chat_providers.py:1781` | `claude-sonnet-4-20250514` | user-facing chat | no change (old Sonnet id, not Opus — low risk, can upgrade id separately) |
| `agent_anthropic` | `api/services/chat_providers.py:2045` | `claude-sonnet-4-20250514` | user-facing chat | no change |
| `AC_DRAFT_MODEL` | `api/routers/specs.py:28` | `claude-haiku-4-5` | routine drafting | already Haiku — correct |
| `spec builder spawn` | `api/routers/specs.py:1332` | `cfg_model or "sonnet"` | routine | already Sonnet |
| `DEFAULT_ANTHROPIC_MODEL` | `api/services/anthropic_models.py` | `claude-sonnet-4-6` | user-facing default | already correct |
| `adventures router` | `api/routers/adventures.py:252` | `claude-sonnet-4-20250514` | user-facing | old Sonnet id, not Opus — acceptable |
| `transcripts router` | `api/routers/transcripts.py:181` | `claude-haiku-4-5` | background | correct |
| `onboarding router` | `api/routers/onboarding.py:99` | `claude-sonnet-4-20250514` | user-facing | old Sonnet id — acceptable |

## Top 3 cost leaks (fixed)

1. **`.ostk/current_model` = `claude-opus-4-7`** — every Claude Code subagent spawned via `register-agent.sh` without an explicit model fell back to this file and registered itself as Opus 4.7. This is the single largest driver: all `saa` work ran on Opus. **Fixed:** file now contains `claude-sonnet-4-6`.

2. **`MODEL_MAP` `"opus"` alias pointed to `claude-opus-4-6`** in `agents.py` while `register-agent.sh` used `claude-opus-4-7` — misaligned, two different Opus versions in flight. **Fixed:** `agents.py` MODEL_MAP now uses `claude-opus-4-7` to match the hook.

3. **No explicit Sonnet default enforcement for saa spawns** — callers that omitted `model` got the schema default `"sonnet"` which resolves correctly, but nothing prevented a caller from passing `"opus"` without a genuine need. **Fixed:** new `model_tier` field + `services/model_routing.py` adds a clear API contract with auto-escalation and an audit log.

## Changes made

- `api/services/model_routing.py` (new): `resolve_model(tier)`, `escalation_needed(output, exit_code)`, `escalate_to_opus(agent, reason)` writing to `.ostk/escalation_log.jsonl`.
- `api/models/schemas.py`: added `model_tier: Optional[str]` to `AgentSpawn`.
- `api/routers/agents.py`: `spawn_agent` resolves `model_tier` via `resolve_model` when set; MODEL_MAP `"opus"` → `claude-opus-4-7`, `"haiku"` → `claude-haiku-4-5-20251001`.
- `api/services/anthropic_models.py`: added `claude-opus-4-7` entry.
- `.ostk/current_model`: changed from `claude-opus-4-7` to `claude-sonnet-4-6`.
- `api/tests/test_model_routing.py` (new): full unit + integration-ish test suite.

## What was NOT changed

- `stream_anthropic` / `agent_anthropic` in `chat_providers.py` — hardcoded to `claude-sonnet-4-20250514` (old Sonnet id, not Opus). User-facing chat stays on Sonnet. A follow-up can bump to `claude-sonnet-4-6` id when ready.
- Any call site already using Haiku or old-Sonnet ids for non-Opus work.

## Projected monthly cost impact

Assumptions: 80% of spend ($1,104/mo) is subagent work previously on Opus 4.7 ($15/M input). Same token volume on Sonnet 4.6 ($3/M input) = 5x cheaper. Rough projection: **$220–$350/mo** for subagent work (down from ~$1,100), plus ~$280 unchanged (chat + Haiku). Total: **~$500–$630/mo**, vs $1,380 today. Actual savings depend on escalation rate; check `.ostk/escalation_log.jsonl` line count weekly.
