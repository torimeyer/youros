#!/usr/bin/env bash
# youros-doctor.sh: one-command health check for yourOS (→2567)
#
# Prints a green ✓ or red ✗ line per check with the fix command next to each
# red. Exits 0 when all hard checks pass, 1 otherwise.
#
# Usage:
#   scripts/youros-doctor.sh
#
# Overridable env vars (for tests):
#   DOCTOR_BACKEND_URL   — defaults to https://127.0.0.1:8000
#   DOCTOR_FRONTEND_URL  — defaults to http://127.0.0.1:3010
#   DOCTOR_YOUROS_DIR    — defaults to $HOME/.youros
#   DOCTOR_SOCK_PATH     — defaults to <repo_root>/.ostk/ostk.sock

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

DOCTOR_BACKEND_URL="${DOCTOR_BACKEND_URL:-https://127.0.0.1:8000}"
DOCTOR_FRONTEND_URL="${DOCTOR_FRONTEND_URL:-http://127.0.0.1:3010}"
DOCTOR_YOUROS_DIR="${DOCTOR_YOUROS_DIR:-$HOME/.youros}"
DOCTOR_SOCK_PATH="${DOCTOR_SOCK_PATH:-$REPO_DIR/.ostk/ostk.sock}"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

FAILED=0
BACKEND_UP=0

_pass() { echo -e "${GREEN}✓${NC} $1"; }
_fail() { echo -e "${RED}✗${NC} $1"; echo "  fix: $2"; FAILED=1; }
_warn() { echo -e "${YELLOW}⚠${NC} $1"; }

# ── 1. Backend reachable ──────────────────────────────────────────────────────
_http() {
    curl -sk --connect-timeout 3 -m 5 -o /dev/null -w "%{http_code}" "$1" 2>/dev/null \
        || true
}

backend_code="$(_http "$DOCTOR_BACKEND_URL/api/status")"
if [[ "$backend_code" == "200" ]]; then
    _pass "Backend reachable ($DOCTOR_BACKEND_URL)"
    BACKEND_UP=1
else
    _fail "Backend not reachable ($DOCTOR_BACKEND_URL, got $backend_code)" \
          "launchctl kickstart -k gui/\$(id -u)/com.youros.backend"
fi

# ── 2. Frontend reachable (soft check — warn only) ────────────────────────────
frontend_code="$(_http "$DOCTOR_FRONTEND_URL")"
if [[ "$frontend_code" =~ ^2 ]]; then
    _pass "Frontend reachable ($DOCTOR_FRONTEND_URL)"
else
    _warn "Frontend not reachable ($DOCTOR_FRONTEND_URL). Run scripts/dev-frontend.sh to start it"
fi

# ── 3. Settings file ──────────────────────────────────────────────────────────
settings_file="$DOCTOR_YOUROS_DIR/settings.json"
if [[ ! -f "$settings_file" ]]; then
    _fail "settings.json not found ($settings_file)" \
          "mkdir -p \"$DOCTOR_YOUROS_DIR\" && echo '{}' > \"$settings_file\""
elif ! python3 -c "import json, sys; json.load(open(sys.argv[1]))" "$settings_file" 2>/dev/null; then
    _fail "settings.json exists but is not valid JSON ($settings_file)" \
          "cp \"${settings_file}.bak\" \"$settings_file\"  # restore from backup, or: echo '{}' > \"$settings_file\""
else
    _pass "settings.json exists and parses ($settings_file)"
fi

# ── 4. Kernel socket ──────────────────────────────────────────────────────────
if [[ -e "$DOCTOR_SOCK_PATH" ]]; then
    _pass "Kernel socket present ($DOCTOR_SOCK_PATH)"
else
    _fail "Kernel socket not found ($DOCTOR_SOCK_PATH)" \
          "ostk boot  # restarts the kernel and recreates the socket"
fi

# ── 5. OAuth tool status ──────────────────────────────────────────────────────
# Only query tools that appear to be configured locally (marker file/dir exists).
# Format: "Display name|endpoint|local marker (empty = always skip)"
OAUTH_TOOLS=(
    "GitHub|/api/github/status|$DOCTOR_YOUROS_DIR/github_token.json"
    "Google (Gmail, Calendar, Drive)|/api/gmail/auth/status|$DOCTOR_YOUROS_DIR/google_token.json"
    "Slack|/api/slack/status|$DOCTOR_YOUROS_DIR/slack_workspaces"
    "Atlassian|/api/atlassian/status|$DOCTOR_YOUROS_DIR/atlassian_config.json"
)

if [[ $BACKEND_UP -eq 1 ]]; then
    for entry in "${OAUTH_TOOLS[@]}"; do
        IFS='|' read -r tool_name endpoint marker <<< "$entry"
        [[ -n "$marker" && ! -e "$marker" ]] && continue
        # An empty marker directory means the tool was never connected: skip, not fail.
        [[ -n "$marker" && -d "$marker" && -z "$(ls -A "$marker" 2>/dev/null)" ]] && continue

        response="$(curl -sk --connect-timeout 3 -m 5 \
            "$DOCTOR_BACKEND_URL$endpoint" 2>/dev/null || echo "")"
        connected="$(echo "$response" | \
            python3 -c "import json,sys; d=json.load(sys.stdin); print('true' if (d.get('connected') or d.get('authenticated')) else 'false')" \
            2>/dev/null || echo "false")"

        if [[ "$connected" == "true" ]]; then
            _pass "$tool_name connected"
        else
            _fail "$tool_name token missing or expired" \
                  "Open yourOS in the browser and reconnect $tool_name under Settings > Connections"
        fi
    done
fi

# ── 6. ~/.youros/ expected directories ───────────────────────────────────────
for dir in specs drafts; do
    full_dir="$DOCTOR_YOUROS_DIR/$dir"
    if [[ -d "$full_dir" ]]; then
        _pass "~/.youros/$dir directory present"
    else
        _fail "~/.youros/$dir directory missing ($full_dir)" \
              "mkdir -p \"$full_dir\""
    fi
done

# ── result ────────────────────────────────────────────────────────────────────
echo ""
if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}All checks passed.${NC}"
    exit 0
else
    echo -e "${RED}One or more checks failed. See fix commands above.${NC}"
    exit 1
fi
