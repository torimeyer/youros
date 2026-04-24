#!/bin/bash
# PostToolUse hook: hint on curl exit 52 against :8000.
# →914 pattern: http vs https mismatch emits exit 52. This hook is
# informational only. It never blocks. Exit 0 always.
set -u
INPUT=$(cat)

RESULT=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
import os, sys, json
raw = os.environ.get("INPUT_JSON", "") or "{}"
try:
    d = json.loads(raw)
except Exception:
    sys.exit(0)

ti = d.get("tool_input") or {}
cmd = (ti.get("command") or "").lower() if isinstance(ti, dict) else ""

tr = d.get("tool_response") or {}
if isinstance(tr, str):
    resp_text = tr
elif isinstance(tr, dict):
    parts = []
    for key in ("stderr", "error", "output", "content", "message", "text"):
        v = tr.get(key)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    t = item.get("text") or ""
                    if t:
                        parts.append(t)
                elif isinstance(item, str):
                    parts.append(item)
    resp_text = "\n".join(parts)
else:
    resp_text = ""

# Must be a curl command targeting :8000
if "curl" not in cmd:
    sys.exit(0)
if "localhost:8000" not in cmd and "127.0.0.1:8000" not in cmd:
    sys.exit(0)

# Detect exit code 52 in the response text (various serialisation forms)
resp_lower = resp_text.lower()
is_52 = (
    "exit:52" in resp_lower
    or "exit code: 52" in resp_lower
    or "exit_code: 52" in resp_lower
    or "returned exit code 52" in resp_lower
    or "(52)" in resp_text
)
if is_52:
    print("hint")
PY
)

if [ "$RESULT" = "hint" ]; then
    echo "Tip: curl exit 52 on :8000 is the →914 HTTP-vs-HTTPS mismatch. Use scripts/api-probe.sh (or curl -k https://...) instead." >&2
fi
exit 0
