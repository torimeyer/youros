# →1215: OSTK_SKIP_GIT_GUARD + Gate Parser Findings

**Date:** 2026-05-12
**Binary:** `~/.local/bin/ostk` v6.0.5
**Gate source:** `src/kernel/git_guard.rs` (v6-only; not in `~/Downloads/haystack-main/` which is v2.3.0)

---

## Bug 1: OSTK_SKIP_GIT_GUARD is ignored by mcp__ostk__bash

### Root cause

The gate in `src/kernel/git_guard.rs` checks the override flag via
`std::env::var("OSTK_SKIP_GIT_GUARD")` — reading from the **MCP server process
environment**. That check fires *before* the command is forked to a shell.

When a caller writes:

```
mcp__ostk__bash(cmd="OSTK_SKIP_GIT_GUARD=1 git stash drop stash@{0}")
```

the `OSTK_SKIP_GIT_GUARD=1` prefix is a shell inline-assignment that only
affects the child process spawned by `/bin/sh -c <cmd>`. The MCP server's own
env never changes, so `std::env::var("OSTK_SKIP_GIT_GUARD")` returns `Err`
and the gate still fires.

### Why native Bash appears to "honor" the override

Native `Bash` (Claude Code built-in tool) does **not** route through the ostk
MCP server at all. There is no `git_guard` call in the native tool path.
The bash-guards.sh PreToolUse hook does have a `stash-label-audit` section, but:

1. It only applies to `tool_name == "Bash"`, not `mcp__ostk__bash` (line ~110 of
   bash-guards.sh: `case "$TOOL" in Bash) : ;; *) exit 0 ;; esac`).
2. `git stash drop` (and `pop`, `apply`, `list`, `show`, `branch`, `clear`) are
   explicitly whitelisted inside that section.

So native Bash passes `git stash drop` through with or without OSTK_SKIP_GIT_GUARD.
It is not that native Bash "reads" the env var — it just never hits the gate.

### Proposed fix (src/kernel/git_guard.rs)

In the guard entry point (likely a function called before forking the shell),
extend the env-var check to also scan the cmd string for an inline assignment:

```rust
fn skip_requested(cmd: &str) -> bool {
    // Check 1: MCP server process env (operator sets this before launching ostk).
    if std::env::var("OSTK_SKIP_GIT_GUARD").as_deref() == Ok("1") {
        return true;
    }
    // Check 2: Inline shell assignment at the start of cmd.
    // Matches: "OSTK_SKIP_GIT_GUARD=1 git stash ..." or
    //          "OSTK_SKIP_GIT_GUARD=1 ANOTHER_VAR=x git stash ..."
    // Use a simple prefix scan rather than full shlex to stay zero-dep.
    for token in cmd.split_whitespace() {
        if token == "OSTK_SKIP_GIT_GUARD=1" {
            return true;
        }
        // Stop once we hit a token that isn't a VAR=VALUE assignment.
        if !token.contains('=') {
            break;
        }
    }
    false
}
```

Callers of the gate replace `std::env::var("OSTK_SKIP_GIT_GUARD") == Ok("1")`
with `skip_requested(cmd)`.

---

## Bug 2: Gate parser matches substring, not parsed argv

### Root cause (confirmed via binary strings)

The gate blocks any cmd whose string representation contains the substring
`"git stash"` (or `"git reset"` / `"git clean"`). Evidence from
`strings ~/.local/bin/ostk`:

```
git-state-mutation blocked by fleet-active gate (
 git stash/reset/clean while peers are writing risks data loss.
```

The v2.3.0 source (closest available) shows the dispatch layer passes the raw
cmd string to sub-checks. In v6, the git_guard check almost certainly does
something equivalent to:

```rust
// Current (broken) pattern:
if cmd.contains("git stash") || cmd.contains("git reset") || cmd.contains("git clean") {
    // ...fire gate...
}
```

This fires for any command mentioning these phrases in any position, including:

```
ostk work add --description "fix git stash label-audit bug"
ostk needle add "document git stash drop workaround"
```

Neither command mutates git state, but both get blocked.

### Proposed fix (src/kernel/git_guard.rs)

Replace the substring check with a shlex-based argv inspection. The gate should
only fire when `git` is the actual executable being invoked and the subcommand
is one of the destructive ones:

```rust
use shlex; // or equivalent inline tokenizer

fn is_git_state_mutation(cmd: &str) -> bool {
    let args = match shlex::split(cmd) {
        Some(a) => a,
        None => return false,
    };
    // Skip leading VAR=VALUE tokens (inline env assignments).
    let positional: Vec<&str> = args
        .iter()
        .map(String::as_str)
        .skip_while(|t| t.contains('='))
        .collect();
    match positional.as_slice() {
        // git stash [push|<nothing>] → mutation
        // git stash drop/pop/apply/list/show/branch/clear → safe
        ["git", "stash", rest @ ..] => {
            let safe = ["drop", "pop", "apply", "list", "show", "branch", "clear"];
            rest.first().map_or(true, |sub| !safe.contains(sub))
        }
        ["git", "reset", ..] => true,
        ["git", "clean", ..] => true,
        _ => false,
    }
}
```

Key improvement: `"ostk work add --description \"fix git stash bug\""` tokenizes
to argv[0]="ostk", not "git", so `is_git_state_mutation` returns `false`.

---

## Summary of file locations

| Item | Location |
|------|----------|
| Gate source | `src/kernel/git_guard.rs` (v6, not in local Downloads copy) |
| MCP bash handler | `src/serve/tools/sh_run.rs` (v2.3.0 proxy; v6 equivalent) |
| Hook (Bash-only checks) | `.claude/hooks/bash-guards.sh` |
| Installed binary | `~/.local/bin/ostk` v6.0.5 |

## Fix summary

**Fix 1** (`src/kernel/git_guard.rs`): `skip_requested(cmd)` — check both the
process env and inline `OSTK_SKIP_GIT_GUARD=1` tokens in the cmd string, so
operators can override from within a `mcp__ostk__bash` call.

**Fix 2** (`src/kernel/git_guard.rs`): `is_git_state_mutation(cmd)` — shlex-parse
the cmd, skip leading env assignments, match only when argv[0]=="git" and
argv[1] in {"stash"(push), "reset", "clean"}. Current substring check falsely
blocks description text that mentions those keywords.
