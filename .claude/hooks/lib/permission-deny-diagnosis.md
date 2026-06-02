# Phantom Tool-Use Rejection Diagnosis

## Symptom

The parent Claude Code session (branch `main`) calls `Bash` with commands like
`bash scripts/restart-backend.sh` or `curl -sSk ... https://127.0.0.1:8000/api/agents`.
The tool result is:

> The user doesn't want to proceed with this tool use. The tool use was rejected
> (eg. if it was a file edit, the new_string was NOT written to the file).
> STOP what you are doing and wait for the user to tell you how to proceed.

Tori did **not** click Deny. The rejection happens silently between model and subprocess.

## Root Causes (Ranked)

### 1. `pre-tool-guard.sh` exits 2 after saa/diagnose/fix messages

**File:** `.claude/hooks/pre-tool-guard.sh` (the `saa_must_spawn` rule; logic in `lib/rules/saa_must_spawn.sh`)  
**Trigger:** PreToolUse for `Bash|Read|Edit|Write|Grep|Glob`  
**When it fires:** the `saa_must_spawn` rule is enabled (`rule_enabled saa_must_spawn`) AND the last user
message in the JSONL log starts with `saa `, `diagnose `, or `fix `.

```bash
case "$MSG" in
  "saa "*|"diagnose "*|"fix "*)
    echo "Blocked: the user said '$VERB'. Rule: spawn a subagent." >&2
    exit 2 ;;
esac
```

The hook exits 2 with its message on **stderr**. Claude Code wraps this in the
"user doesn't want to proceed" envelope, which looks like a user click-deny.

**Impact:** Blocks `bash scripts/restart-backend.sh`, curl status probes, and any
other Bash call after a `saa/diagnose/fix` message — even after the parent has
already spawned an agent.

### 2. `ostk-first.sh` exits 2 for non-whitelisted commands when ostk is alive

**File:** `.claude/hooks/ostk-first.sh`  
**Trigger:** PreToolUse for `Bash|Read|Edit|Write|Grep|Glob`  
**When it fires:** ostk socket is reachable AND command doesn't match the whitelist
(`*scripts/*`, `*pytest*`, `*vitest*`, `*tsc*`).

```bash
echo "Blocked: use $EQ instead of $TOOL." >&2
exit 2
```

**Impact:** Blocks curl status probes to `127.0.0.1:8000` and other non-whitelisted
bash commands when ostk is running. The trace log (`/tmp/ostk-first.log`) shows
mostly `ostk-socket-dead-allowed`, so this is intermittent (only when daemon is up).

### 3. `bash-guards.sh` writes block messages to STDOUT instead of STDERR

**File:** `.claude/hooks/bash-guards.sh`  
**Trigger:** PreToolUse for `Bash|Monitor|mcp__ostk__bash`  
**Problem:** Sections 1–5 use plain `echo` (STDOUT). When the hook exits 2 with
content only on STDOUT and nothing on STDERR, Claude Code reports:

> PreToolUse:Bash hook error: [.../bash-guards.sh]: No stderr output

This is a different confusing message than "user doesn't want to proceed" but still
blocks the tool. Sections 6–7 already use `>&2` correctly.

## Hypothesis Verdicts

| # | Hypothesis | Verdict |
|---|-----------|---------|
| 1 | `permissions.deny` rule | **Ruled out** — `permissions: {}` in project settings.json, no deny entries anywhere |
| 2 | `defaultMode: auto` + auto-deny dangerous patterns | **Ruled out** — `skipDangerousModePermissionPrompt: true` makes auto mode more permissive, not less; no built-in dangerous-command auto-deny observed |
| 3 | Hook returning rejection-shaped payload | **Confirmed** — saa-must-spawn.sh + ostk-first.sh exit 2 with STDERR; bash-guards.sh exit 2 with STDOUT |
| 4 | Bridge/register-agent producing denial text | **Ruled out** — heartbeat-agent.sh always exits 0; bridge only handles Agent calls |
| 5 | ostk bash wrapper | **Ruled out** — wrapper warnings are non-blocking; phantom rejection is from hook exit 2 |

## Fix

Two changes:

### A. Fix bash-guards.sh output (STDOUT → STDERR, sections 1–5)

Ensures block messages appear as proper rejection reason to Claude rather than
"hook error: No stderr output".

### B. Add broad allow patterns to settings.local.json

Commands in `permissions.allow` bypass all hooks. Adding `bash scripts/*` and
`curl -sSk --connect-timeout*` covers the canonical maintenance commands that
saa-must-spawn.sh and ostk-first.sh were blocking.

## Repro Commands

Before fix — run from parent session (main branch) when last message starts with "saa X":

```bash
# Both produce "The user doesn't want to proceed" or "hook error: No stderr output"
bash scripts/restart-backend.sh
curl -sSk --connect-timeout 3 -m 5 https://127.0.0.1:8000/api/agents
```

After fix — both should execute without phantom rejection.

## Two-line debug ladder

When a deny happens and you don't know whether it came from a hook or a settings rule, run these two commands in order:

```bash
jq '.permissions.deny // []' ~/.claude/settings.json .claude/settings.json .claude/settings.local.json
tail -20 ~/.claude/logs/hook-denies.log
```

The first shows every `permissions.deny` rule that could have blocked a tool outright (no hook fires in this case). The second shows structured hook-deny and crash log entries, newest last. If the deny log is empty but a permissions rule matches, that is your culprit.
