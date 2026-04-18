# Rule-enforcement hooks (installed 2026-04-18)

Three hooks installed from `docs/rule-following-diagnosis.md`:

- `ostk-first.sh` — PreToolUse (matcher `Bash|Read|Edit|Write|Grep|Glob`). Blocks native tools with exit 2 when the ostk backend is reachable, telling Claude to use the equivalent `mcp__ostk__*` tool. Whitelists pytest/vitest/tsc/scripts/*.
- `saa-must-spawn.sh` — PreToolUse (same matcher). Reads the latest transcript and blocks any non-Agent tool when the user's last message starts with `saa `, `diagnose `, or `fix `.
- `standing-rules.sh` — UserPromptSubmit. Prepends the top 3 non-negotiable rules to every user turn.
- `native-block-recovery.sh` — PostToolUse (matcher `Bash|Read|Edit|Write|Grep|Glob`). Detects when `ostk-first.sh` blocked the native tool (response contains `Blocked: use mcp__ostk__`), keeps a per-session counter at `~/.myos/subagents/<session-id>-blocks.count`, and once a subagent has been blocked twice within 60s writes a stronger recovery message to stderr telling Claude to call `ToolSearch('select:mcp__ostk__...')` and retry via `mcp__ostk__*`. Resets after 5 blocks or on the first clean tool response. Targets Agent-tool subagents that bounce between blocked native tools instead of reloading ostk. Logs to `/tmp/native-block-recovery.log` (1MB rotation).

Existing hooks and permissions preserved. To pick up the settings changes, restart Claude Code.

after this edit, restart Claude Code once to re-register the hook matcher on the running process
