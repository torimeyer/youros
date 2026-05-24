#!/usr/bin/env bash
# Rule: edit_postwatch
# Replaces: edit-postwatch.sh (PostToolUse Edit|Write)
# Called from: post-tool-watch.sh on Edit|Write
# Assumes: load-rule.sh, log-fire.sh already sourced by caller.
# Non-blocking: always returns 0.

_edit_postwatch_check() {
  local tool="${1:-Edit}" file_path="$2"

  # ---- 1. check-tsx: run tsc on .tsx/.ts files in app/src/ ----
  case "$file_path" in
    */app/src/*.tsx|*/app/src/*.ts)
      local APP_DIR
      APP_DIR=$(echo "$file_path" | sed 's|/src/.*|/|')
      if [ -f "${APP_DIR}tsconfig.json" ] && [ -x "${APP_DIR}node_modules/.bin/tsc" ]; then
        local TSC_OUTPUT
        TSC_OUTPUT=$(cd "$APP_DIR" && npx tsc --noEmit 2>&1) || {
          echo "TypeScript warnings after edit (non-blocking):" >&2
          echo "$TSC_OUTPUT" | grep -E "error TS" | head -10 >&2
        }
      fi
      ;;
  esac

  # ---- 2. saa-after-3-hypotheses: count edits per file, remind after 3+ ----
  if [ -n "$file_path" ]; then
    local STATE="${MYOS_EDIT_CYCLES_STATE:-${HOME}/.myos/hooks/edit-cycles.json}"
    mkdir -p "$(dirname "$STATE")" 2>/dev/null || true
    FILE_PATH="$file_path" HOOK_STATE="$STATE" python3 <<'PYEOF' 2>/dev/null
import os, sys, json, time
file_path = os.environ.get("FILE_PATH", "")
state_file = os.environ.get("HOOK_STATE", "")
if not file_path:
    sys.exit(0)
now = time.time()
WINDOW = 1800
state = {"files": {}}
try:
    with open(state_file) as f:
        state = json.load(f)
except Exception:
    pass
files = {k: v for k, v in state.get("files", {}).items()
         if now - v.get("last_ts", 0) < WINDOW}
entry = files.get(file_path, {"count": 0, "last_ts": now})
entry["count"] += 1
entry["last_ts"] = now
files[file_path] = entry
try:
    with open(state_file, "w") as f:
        json.dump({"files": files}, f)
except Exception:
    pass
count = entry["count"]
if count >= 3:
    print("")
    print("SAA REMINDER (non-blocking -- rule 2 from 2026-04-27 retro, {} edits this session):".format(count))
    print("  File: {}".format(file_path))
    print("  3+ consecutive edits is the inline-diagnosis limit. Spawn a subagent (saa) for")
    print("  remaining hypotheses instead of continuing inline. See feedback_saa_rules.md.")
PYEOF
  fi

  log_rule_fire "edit_postwatch" "$tool" "allow" "postwatch complete for $file_path"
  return 0
}
