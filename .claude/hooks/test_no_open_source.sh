#!/bin/bash
# Regression test for no-open-source.sh hook.
#
# Rule (from CLAUDE.md):
#   - Auto-open generated reports/PDFs/HTML/images after creating them.
#   - NEVER auto-open source files (.py, .sh, .ts, .json, etc.).
#
# ALLOWED: .md .html .htm .pdf .png .jpg .jpeg .gif .svg .webp .txt, /tmp paths, URLs
# BLOCKED: .py .ts .tsx .js .jsx .sh .json .yaml .yml .toml .css .scss .cfg .ini .env

set -e
HOOK="$(dirname "$0")/no-open-source.sh"

fail=0
test_case() {
  local desc="$1"; local cmd="$2"; local want="$3"
  local payload rc
  payload=$(python3 -c 'import json,sys; print(json.dumps({"tool_input":{"command": sys.argv[1]}}))' "$cmd")
  echo "$payload" | "$HOOK" > /dev/null 2>&1 && rc=0 || rc=$?
  if [ "$rc" -eq "$want" ]; then
    echo "PASS  [$desc]"
  else
    echo "FAIL  [$desc] got rc=$rc wanted=$want"
    fail=1
  fi
}

# Generated reports / output: MUST be allowed (rc=0)
test_case ".md allowed"            "open /Users/me/docs/MORNING-NOTE.md"       0
test_case ".html allowed"          "open /tmp/report.html"                     0
test_case ".htm allowed"           "open /tmp/report.htm"                      0
test_case ".pdf allowed"           "open /tmp/report.pdf"                      0
test_case ".png allowed"           "open /tmp/chart.png"                       0
test_case ".jpg allowed"           "open /tmp/photo.jpg"                       0
test_case ".svg allowed"           "open /tmp/icon.svg"                        0
test_case ".txt allowed"           "open /tmp/log.txt"                         0
test_case "URL allowed"            "open https://example.com"                  0
test_case "/tmp path allowed"      "open /tmp/anything.py"                     0

# Source files: MUST be blocked (rc=2)
test_case ".py blocked"            "open /Users/x/foo.py"                      2
test_case ".sh blocked"            "open /Users/x/foo.sh"                      2
test_case ".ts blocked"            "open /Users/x/foo.ts"                      2
test_case ".tsx blocked"           "open /Users/x/foo.tsx"                     2
test_case ".js blocked"            "open /Users/x/foo.js"                      2
test_case ".jsx blocked"           "open /Users/x/foo.jsx"                     2
test_case ".json blocked"          "open /Users/x/foo.json"                    2
test_case ".yaml blocked"          "open /Users/x/foo.yaml"                    2
test_case ".yml blocked"           "open /Users/x/foo.yml"                     2
test_case ".toml blocked"          "open /Users/x/foo.toml"                    2
test_case ".css blocked"           "open /Users/x/foo.css"                     2
test_case ".scss blocked"          "open /Users/x/foo.scss"                    2
test_case ".env blocked"           "open /Users/x/.env"                        2

# Non-open commands: passthrough
test_case "ls passthrough"         "ls -la"                                    0
test_case "echo passthrough"       "echo hello"                                0

if [ "$fail" -eq 0 ]; then
  echo "ALL TESTS PASSED"
  exit 0
else
  echo "TESTS FAILED"
  exit 1
fi
