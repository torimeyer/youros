#!/bin/bash
# UserPromptSubmit: detect settings.json-level denies.
#
# Claude Code shows the canned string "user doesn't want to proceed with this
# tool use" for BOTH hook-based denies and permissions.deny rules. This hook
# tells them apart: if a tool-result file from the last 60s contains the
# canned string but there is no matching hook-deny log entry within ±2 seconds
# of the file's mtime, the deny came from a settings.json permissions rule.
#
# Emits a SETTINGS-LEVEL DENY DETECTED block on stdout (injected into model
# context by the UserPromptSubmit harness). Exits 0 always.

INPUT=$(cat)  # consume stdin; JSON payload not needed for this check

CANNED="user doesn't want to proceed with this tool use"
DENY_LOG="${DENY_LOG:-${HOME}/.claude/logs/hook-denies.log}"
NOW=$(date +%s)
CUTOFF=$((NOW - 60))
EMITTED=0

# Discover all tool-results directories under ~/.claude/projects
TOOL_RESULT_DIRS=$(find "${HOME}/.claude/projects" -name 'tool-results' -type d 2>/dev/null)

for DIR in $TOOL_RESULT_DIRS; do
    [ -d "$DIR" ] || continue
    [ "$EMITTED" -eq 1 ] && break

    while IFS= read -r -d '' FILE; do
        # Check modification time using python3 for portability (macOS/Linux).
        FILE_MTIME=$(python3 -c "import os; print(int(os.path.getmtime('$FILE')))" 2>/dev/null || echo 0)
        [ "$FILE_MTIME" -ge "$CUTOFF" ] || continue

        # Check for the canned deny string.
        grep -qF "$CANNED" "$FILE" 2>/dev/null || continue

        # Cross-check hook-denies.log: is there an entry within ±2 seconds?
        if [ -f "$DENY_LOG" ] && [ -s "$DENY_LOG" ]; then
            MATCH=$(DENY_LOG="$DENY_LOG" FILE_MTIME="$FILE_MTIME" python3 <<'PY' 2>/dev/null
import os, json
from datetime import datetime

file_mtime = int(os.environ["FILE_MTIME"])
deny_log = os.environ["DENY_LOG"]
found = False
try:
    with open(deny_log) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line, strict=False)
            except Exception:
                continue
            ts_raw = entry.get("ts", "")
            try:
                dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                ts = int(dt.timestamp())
            except Exception:
                continue
            if abs(ts - file_mtime) <= 2:
                found = True
                break
except Exception:
    pass
print("yes" if found else "no")
PY
            )
            [ "$MATCH" = "yes" ] && continue
        fi

        # No hook entry found — this is a settings-level deny.
        EMITTED=1
        printf 'SETTINGS-LEVEL DENY DETECTED:\n'
        printf 'A tool was denied by a permissions rule in settings.json, not a hook.\n'
        printf 'Check ~/.claude/settings.json and .claude/settings.local.json permissions.deny.\n'
        printf 'Run: jq '"'"'.permissions.deny // []'"'"' ~/.claude/settings.json .claude/settings.json .claude/settings.local.json\n'
        break
    done < <(find "$DIR" -maxdepth 1 -name '*.txt' -print0 2>/dev/null)
done

exit 0
