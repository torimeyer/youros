#!/usr/bin/env bash
# scripts/scheduled/reaper.sh
#
# Periodic wrapper: run worktree-reaper.sh --apply (remove absorbed
# agent worktrees) then call the yourOS backend to sweep stale agent rows.
# Intended to fire every 15 minutes via launchd, cron, or systemd.
#
# DO NOT install a launchd plist automatically. Copy the snippet below,
# edit the paths, and run `launchctl load` yourself.
#
# ── macOS launchd (recommended) ──────────────────────────────────────
# Save as ~/Library/LaunchAgents/com.youros.reaper.plist, then:
#   launchctl load ~/Library/LaunchAgents/com.youros.reaper.plist
#
#   <?xml version="1.0" encoding="UTF-8"?>
#   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
#       "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
#   <plist version="1.0">
#   <dict>
#     <key>Label</key>             <string>com.youros.reaper</string>
#     <key>ProgramArguments</key>
#     <array>
#       <string>/bin/bash</string>
#       <string>/ABSOLUTE/PATH/TO/scripts/scheduled/reaper.sh</string>
#     </array>
#     <key>StartInterval</key>     <integer>900</integer>
#     <key>StandardOutPath</key>   <string>/tmp/youros-reaper.log</string>
#     <key>StandardErrorPath</key> <string>/tmp/youros-reaper.log</string>
#     <key>EnvironmentVariables</key>
#     <dict>
#       <key>YOUROS_REPO_ROOT</key>  <string>/ABSOLUTE/PATH/TO/REPO</string>
#     </dict>
#   </dict>
#   </plist>
#
# ── Linux systemd (alternative) ──────────────────────────────────────
# ~/.config/systemd/user/youros-reaper.service:
#   [Unit]   Description=yourOS worktree + agent reaper
#   [Service]
#   ExecStart=/bin/bash /ABSOLUTE/PATH/TO/scripts/scheduled/reaper.sh
#   Environment=YOUROS_REPO_ROOT=/ABSOLUTE/PATH/TO/REPO
#   [Install] WantedBy=default.target
#
# ~/.config/systemd/user/youros-reaper.timer:
#   [Unit]   Description=Run youros-reaper every 15 min
#   [Timer]  OnBootSec=60  OnUnitActiveSec=900
#   [Install] WantedBy=timers.target
#
# systemctl --user enable --now youros-reaper.timer
#
# ── Environment variables ─────────────────────────────────────────────
#   YOUROS_REPO_ROOT       path to the torios repo (auto-detected if unset)
#   YOUROS_REAPER_SCRIPT   override worktree-reaper.sh path (for tests)
#   YOUROS_BACKEND_URL     override backend URL (default: https://127.0.0.1:8000)
#   YOUROS_SKIP_REAP_API   set to "1" to skip the agent-reap API call

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${YOUROS_REPO_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

REAPER="${YOUROS_REAPER_SCRIPT:-$REPO_ROOT/scripts/worktree-reaper.sh}"
BACKEND_URL="${YOUROS_BACKEND_URL:-https://127.0.0.1:8000}"
SKIP_REAP_API="${YOUROS_SKIP_REAP_API:-0}"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

echo "[$(ts)] reaper: starting (repo=$REPO_ROOT)"

# ── Step 1: remove absorbed worktrees ────────────────────────────────
exit_code=0
if "$REAPER" --apply; then
  echo "[$(ts)] reaper: worktree sweep ok"
else
  exit_code=$?
  echo "[$(ts)] reaper: worktree sweep exited $exit_code" >&2
fi

# ── Step 2: sweep stale agent registry rows ───────────────────────────
if [ "$SKIP_REAP_API" != "1" ]; then
  if curl --connect-timeout 3 -m 10 -sSk \
      -X POST "$BACKEND_URL/api/agents/reap" \
      -H "Content-Type: application/json" \
      -d '{}' >/dev/null 2>&1; then
    echo "[$(ts)] reaper: agent-reap ok"
  else
    echo "[$(ts)] reaper: agent-reap skipped (backend unreachable or endpoint absent)" >&2
  fi
fi

echo "[$(ts)] reaper: done"
exit "$exit_code"
