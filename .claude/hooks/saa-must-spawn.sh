#!/bin/bash
# If the most recent user message started with saa/diagnose/fix,
# the only acceptable next tool is Agent (or Task). Block anything else.
#
# Tori-personal gate: vocabulary is specific to tori's workflow. NR users
# saying "fix this bug" should not be silently rejected. Hook is a no-op
# unless ~/.myos/config.json has "enable_tori_rules": true.
TORI_CONFIG="${MYOS_CONFIG_PATH:-$HOME/.myos/config.json}"
if [ ! -f "$TORI_CONFIG" ] || ! grep -q '"enable_tori_rules"[[:space:]]*:[[:space:]]*true' "$TORI_CONFIG" 2>/dev/null; then
    exit 0
fi
INPUT=$(cat)
TOOL=$(echo "$INPUT" | python3 -c "import sys,json;print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null)
case "$TOOL" in
  Agent|Task) exit 0 ;;
esac
LAST=$(ls -t "$HOME/.claude/projects/-Users-torimeyer-claude-torios/"*.jsonl 2>/dev/null | head -1)
[ -z "$LAST" ] && exit 0
MSG=$(grep '"type":"user"' "$LAST" | tail -1 | python3 -c "import sys,json;d=json.loads(sys.stdin.read());c=d.get('message',{}).get('content');print((c if isinstance(c,str) else '').lower())" 2>/dev/null)
case "$MSG" in
  "saa "*|"diagnose "*|"fix "*)
    VERB="${MSG%% *}"
    echo "Blocked: the user said '$VERB'. Rule: spawn a subagent via Agent, no inline work." >&2
    exit 2 ;;
esac
exit 0
