# Why standing instructions get missed (and how to make them stick)

## 1. Evidence: where the rules live

Rules are spread across five surfaces, each loaded differently:

| Surface | Path | How it reaches the model |
|---|---|---|
| Project CLAUDE.md | `/Users/torimeyer/claude/torios/CLAUDE.md` | Injected once, top of system prompt |
| Outer CLAUDE.md | `/Users/torimeyer/claude/CLAUDE.md` | Injected once, top of system prompt |
| Memory index | `memory/MEMORY.md` | Injected once; sub-files are links, not auto-loaded |
| Feedback files | `memory/feedback_*.md` (10 files) | NOT injected unless Claude chooses to read them |
| Settings + hooks | `.claude/settings*.json`, `.claude/hooks/*.sh` | Enforced by the harness, not the model |

Load-bearing rules today that have **no hook enforcement**:
- "Always ostk over Bash/Read/Edit/Grep" (`feedback_ostk_over_bash.md`)
- "saa means spawn, no exceptions" (`feedback_saa_rules.md`)
- "If ostk MCP drops, reload via ToolSearch, do not fall through" (same file)
- "If ostk MCP disconnects, tell the user immediately" (`feedback_agent_rules.md`)

Hooks that exist and work: `curl-timeouts.sh`, `no-npm-dev.sh`, `no-open-source.sh`, `safe-vitest.sh`, `register-agent.sh`, `complete-agent.sh`, `check-tsx.sh`. None cover ostk-vs-native or saa.

## 2. Root causes, ranked

1. **Rules are advice, not gates.** For ostk-vs-native and saa, nothing in the runtime refuses the violation. An instruction with zero enforcement cost is a suggestion, and suggestions lose to inline momentum every time.
2. **Runtime system-reminders outrank memory.** A mid-session reminder like "deferred tools are no longer available, do not search for them" was read as authoritative and overrode the memory rule "reload via ToolSearch when MCP drops." Anthropic-emitted reminders feel like the harness speaking. Memory feels like a preference.
3. **Feedback files are not auto-loaded.** `MEMORY.md` is an index of links. The full text of `feedback_ostk_over_bash.md` and `feedback_saa_rules.md` only reaches the model if it chooses to Read them. When the model is mid-task and confident, it skips that step.
4. **Two sources of truth.** `CLAUDE.md` has the short version ("ostk first, raw shell as fallback"). `feedback_ostk_rules.md` has the emphatic version ("PERMANENT RULE, every session"). The short version reads as "prefer," the long version reads as "mandatory." Model sees the short one and acts on the weaker reading.
5. **No audit signal after a violation.** When Bash is used instead of `mcp__ostk__shell`, nothing fires back "you just violated the ostk rule." The next turn has no corrective pressure.

## 3. Proposed hooks

Add two PreToolUse hooks and one UserPromptSubmit hook. Minimal code, closes the biggest leaks.

### 3a. Block native tools when ostk MCP is up

Append to `PreToolUse` in `.claude/settings.json`:

```json
{
  "matcher": "Bash|Read|Edit|Write|Grep|Glob",
  "hooks": [
    { "type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/ostk-first.sh" }
  ]
}
```

`.claude/hooks/ostk-first.sh` (exit 2 blocks with feedback to the model):

```bash
#!/bin/bash
# Block native file/shell tools whenever ostk MCP is reachable.
# Exit 2 returns the message to Claude, who must retry with ostk.
curl -sSk --connect-timeout 1 -m 2 https://127.0.0.1:8000/api/status >/dev/null 2>&1 || exit 0
INPUT=$(cat)
TOOL=$(echo "$INPUT" | python3 -c "import sys,json;print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null)
case "$TOOL" in
  Bash)  EQ="mcp__ostk__shell" ;;
  Read)  EQ="mcp__ostk__fs_read" ;;
  Edit)  EQ="mcp__ostk__edit" ;;
  Write) EQ="mcp__ostk__fs_write" ;;
  Grep|Glob) EQ="mcp__ostk__search" ;;
  *) exit 0 ;;
esac
# Allow pytest / vitest / tsc — those are whitelisted native paths.
CMD=$(echo "$INPUT" | python3 -c "import sys,json;print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null)
case "$CMD" in *pytest*|*vitest*|*tsc*|*scripts/*) exit 0 ;; esac
echo "Blocked: use $EQ instead of $TOOL. Standing rule: ostk first."
echo "If the ostk tool is deferred, load it via ToolSearch('select:$EQ') then retry."
exit 2
```

### 3b. Force saa to spawn

Append to `PreToolUse`:

```json
{
  "matcher": "Bash|Read|Edit|Write|Grep|Glob",
  "hooks": [
    { "type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/saa-must-spawn.sh" }
  ]
}
```

`.claude/hooks/saa-must-spawn.sh`:

```bash
#!/bin/bash
# If the most recent user message started with saa/diagnose/fix,
# the only acceptable next tool is Agent (or Task). Block anything else.
LAST=$(ls -t "$HOME/.claude/projects/-Users-torimeyer-claude-torios/"*.jsonl 2>/dev/null | head -1)
[ -z "$LAST" ] && exit 0
MSG=$(grep '"type":"user"' "$LAST" | tail -1 | python3 -c "import sys,json;d=json.loads(sys.stdin.read());print((d.get('message',{}).get('content') or '').lower())" 2>/dev/null)
case "$MSG" in
  saa\ *|diagnose\ *|"fix "*)
    echo "Blocked: the user said '${MSG%% *}'. Rule: spawn a subagent via Agent, no inline work."
    exit 2 ;;
esac
exit 0
```

### 3c. Inject top rules on every user turn

Add `UserPromptSubmit` to `settings.json` pointing at a hook that echoes the 3 highest-stakes lines:

```bash
#!/bin/bash
cat <<'EOF'
STANDING RULES (non-negotiable this turn):
1. ostk tools first. Bash/Read/Edit/Grep only if ostk MCP is offline.
2. If user says saa/diagnose/fix, spawn a subagent. No inline work.
3. If ostk MCP drops, tell the user immediately. Reload via ToolSearch.
EOF
```

This keeps the rules in-context every turn instead of hoping memory sticks.

## 4. What you must do

1. Review the three hook files above. Adjust wording to taste.
2. Save them under `.claude/hooks/` and `chmod +x`.
3. Edit `.claude/settings.json` to register the three matchers.
4. **Restart Claude Code.** Hooks only load at session start.
5. First test: type `saa anything`. If the model tries Bash before Agent, the hook should block with an exit-2 message.
