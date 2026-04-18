# Rule-enforcement hooks (installed 2026-04-18)

Three hooks installed from `docs/rule-following-diagnosis.md`:

- `ostk-first.sh` — PreToolUse (matcher `Bash|Read|Edit|Write|Grep|Glob`). Blocks native tools with exit 2 when the ostk backend is reachable, telling Claude to use the equivalent `mcp__ostk__*` tool. Whitelists pytest/vitest/tsc/scripts/*.
- `saa-must-spawn.sh` — PreToolUse (same matcher). Reads the latest transcript and blocks any non-Agent tool when the user's last message starts with `saa `, `diagnose `, or `fix `.
- `standing-rules.sh` — UserPromptSubmit. Prepends the top 3 non-negotiable rules to every user turn.

Existing hooks and permissions preserved. To pick up the settings changes, restart Claude Code.

after this edit, restart Claude Code once to re-register the hook matcher on the running process
