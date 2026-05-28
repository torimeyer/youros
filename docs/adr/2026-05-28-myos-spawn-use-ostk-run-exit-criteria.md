# ADR: Exit criteria for MYOS_SPAWN_USE_OSTK_RUN fallback

**Date:** 2026-05-28
**Status:** Accepted
**Task:** →1794
**Spec:** docs/spec/adopt-claude-code-s-good-ideas-into-myos-as-vendor-agnostic-abstractions.md (AC3)

## Context

`api/routers/agents.py` contains a bespoke Claude Code subprocess spawn path that predates the `ostk run <Agentfile>` abstraction. The `MYOS_SPAWN_USE_OSTK_RUN` flag was introduced as an escape hatch (→1305) to route spawns through `ostk.run_agentfile()` instead. Without documented exit criteria the flag lives indefinitely and the phrase "default to ostk run" remains aspirational rather than shipped.

The bespoke path (lines ~4610+ in agents.py) is the fallback when:
- `MYOS_SPAWN_USE_OSTK_RUN` is off and `use_ostk_run=False` (existing default, no behavior change)
- Flag is on but no agentfile resolves for the agent name (silent fallback)
- Flag is on but `ostk run` itself errors (silent fallback)

Per AC3 of the adopt-claude-code spec: "Without this the flag lives forever and 'default' silently isn't."

## Decision

The bespoke path is retired and `MYOS_SPAWN_USE_OSTK_RUN` is deleted when ALL FOUR of the following criteria hold:

### Criterion 1: All 3 isolation modes verified via ostk run

The bespoke path implements three isolation modes: `worktree` (creates `.claude/worktrees/agent-*`), `container`, and `none`. All three must work correctly when routed through `ostk run`:

- `worktree`: agent lands in a git worktree, scaffold-commit watcher fires, reaper picks it up
- `container`: agent runs in an isolated container with correct env injection
- `none`: agent runs in-process with inherited cwd

Verification: spawn one agent with each isolation mode while `MYOS_SPAWN_USE_OSTK_RUN=1`; confirm each lands, commits (if applicable), and completes without silent fallback.

### Criterion 2: Scaffold-commit watcher works on ostk run worktrees

The bespoke path sets `worktree_path` in `agent_metadata` (see `agents.py` around line 1463). The scaffold-commit watcher `_worktree_has_new_work` depends on this metadata to detect new commits. The `ostk run` path bypasses the bespoke worktree creation loop, so `worktree_path` is currently never set for ostk-run agents.

This is closed when `run_agentfile()` in `api/services/ostk.py` threads `worktree_path` back into `agent_metadata` (either via ostk run output or via a pre-spawn write), AND `_worktree_has_new_work` correctly fires for ostk-run worktrees in a live test.

### Criterion 3: Runtime probe passes for each provider

The spawn path must resolve correctly for both current runtime providers:

- `claude_code_provider`: `ostk run` invokes the Claude CLI via Agentfile
- `gemini_cli_provider`: `ostk run` invokes the Gemini CLI via Agentfile

The `RuntimeProvider` interface (AC4 of the same spec) must expose a `features()` set that includes `WORKTREE` and `SUBAGENTS` for each provider, and a probe script must confirm both providers spawn, register, and complete at least one agent without falling back to the bespoke path.

### Criterion 4: scripts/e2e_smoke.sh passes with flag forced on

Run `MYOS_SPAWN_USE_OSTK_RUN=1 scripts/e2e_smoke.sh` and confirm green. This catches silent fallbacks that would otherwise only surface in production. No partial credit: any silent fallback during the smoke run means this criterion is not met.

## Retirement sequence

When all four criteria hold:

1. Set `MYOS_SPAWN_USE_OSTK_RUN` default to `"1"` in `os.environ.get(...)` call (one-character change).
2. Run smoke test again to confirm no regressions.
3. Delete the bespoke Claude Code subprocess path (lines ~4734+ in agents.py at time of writing).
4. Delete the `MYOS_SPAWN_USE_OSTK_RUN` env check and the `_env_use_ostk_run` / `_req_use_ostk_run` / `_use_ostk_run` block.
5. Delete the exit criteria comment block added in →1794.
6. Update this ADR status to `Superseded` and link the commit.

## Consequences

**Positive:**
- Subagent spawn layer is provider-neutral. Gemini CLI users get working agents.
- `ostk run` is the single spawn path; the bespoke path's quirks (env injection, worktree race conditions, pipe-drain bug from spawn_stderr_pipe_fix) are all retired.
- Future providers only need to implement an Agentfile; no changes to agents.py.

**Negative during transition:**
- Until criterion 2 is met, ostk-run agents do not trigger the scaffold-commit watcher, so their worktrees are not auto-merged. Manual merge required.
- Until criterion 3 is met, Gemini CLI subagents silently fall back to the Claude CLI bespoke path (no failure, but not the intended path).

## References

- Spec: `docs/spec/adopt-claude-code-s-good-ideas-into-myos-as-vendor-agnostic-abstractions.md` AC3
- Task: →1794 (this documentation task)
- Escape hatch introduction: →1305
- Pre-flight status comment: `api/routers/agents.py` lines ~4607-4618 (at time of writing)
- `ostk.run_agentfile()`: `api/services/ostk.py` around line 3723
- Spawn stderr pipe fix (related bespoke-path bug): `project_spawn_stderr_pipe_fix.md` in memory
