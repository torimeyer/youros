# tori() - launch ToriOS
# Canonical, tracked source for the tori shell function.
# ~/.zshrc sources this file; do NOT redefine tori() in ~/.zshrc.
#
# Why this file exists:
#   ~/.zshrc is not tracked in this repo, so fixes (like the zsh
#   _response/_http_code leak fix) did not propagate to fresh-machine
#   setups. Regression tests in scripts/test_tori*.sh now guard THIS
#   file directly via the TORI_ZSH env var, so a broken tori() fails
#   CI instead of only being caught on the author's laptop.
#
# Agent launch is OPT-IN. myOS is generic by default. Set
# TORI_LAUNCH_AGENT=claude to auto-run `claude update` during startup
# and launch the Claude CLI after the splash. Unset or any other value
# exits cleanly after the readiness probes with no agent launched.
#
# Parallel updates, then splash, then servers, then (optional) agent.
tori() {
  cd ~/claude/torios || return 1
  setopt LOCAL_OPTIONS NO_MONITOR

  # --- Updates (parallel, non-blocking) ---
  # Claude update takes ~60s but only applies next launch.
  # Opt-in: only runs when TORI_LAUNCH_AGENT=claude, matching the
  # launch gate below. Default tori stays agent-agnostic.
  # Fire-and-forget: script(1) gives the child its own pty so
  # /dev/tty writes don't cause "suspended (tty output)".
  if [[ "${TORI_LAUNCH_AGENT:-}" == "claude" ]]; then
    /usr/bin/script -q /dev/null sh -c 'command claude update' </dev/null >/dev/null 2>&1 &!
  fi

  # ostk + git checks run in parallel (~0.5s each).
  local _ostk_tmp=$(mktemp) _git_tmp=$(mktemp)

  (
    ostk_current=$(~/.local/bin/ostk --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+[.0-9]*' | head -1)
    ostk_latest=$(curl -s --connect-timeout 3 -m 5 https://api.github.com/repos/os-tack/ostk.ai/releases/latest 2>/dev/null | grep '"tag_name"' | sed -E 's/.*"v?([^"]*)".*/\1/')
    if [[ -n "$ostk_latest" && "$ostk_current" != "$ostk_latest" ]]; then
      printf '\033[38;2;140;140;140m  Updating ostk %s -> %s...\033[0m\n' "$ostk_current" "$ostk_latest"
      # v3.0.0+ uses darwin-universal with no v-prefix in filename;
      # older releases used v-prefixed aarch64-apple-darwin.
      # Timeout budget: the darwin-universal asset is ~28MB. Earlier 15s ceiling
      # caused silent failures when GitHub was slow (→user rule: tori MUST land
      # latest; -m 15 left users on the old binary with no warning). 120s is
      # generous for typical home connections; --connect-timeout 5 still bails
      # fast when GitHub is unreachable.
      local url="https://github.com/os-tack/ostk.ai/releases/download/v${ostk_latest}/ostk-${ostk_latest}-darwin-universal.tar.gz"
      local _dl_err=""
      if ! curl -fsL --connect-timeout 5 -m 120 "$url" -o /tmp/ostk-update.tar.gz 2>/tmp/ostk-update.err; then
        _dl_err=$(cat /tmp/ostk-update.err 2>/dev/null)
        url="https://github.com/os-tack/ostk.ai/releases/download/v${ostk_latest}/ostk-v${ostk_latest}-aarch64-apple-darwin.tar.gz"
        if ! curl -fsL --connect-timeout 5 -m 120 "$url" -o /tmp/ostk-update.tar.gz 2>/tmp/ostk-update.err; then
          _dl_err="$_dl_err; fallback: $(cat /tmp/ostk-update.err 2>/dev/null)"
          printf '\033[38;2;255;80;80m  ✗ ostk download failed: %s\033[0m\n' "$_dl_err"
        fi
      fi
      rm -f /tmp/ostk-update.err
      # Extract into a scratch dir and find the single executable inside.
      # v4.0.0+ ships 'ostk-macos'; earlier releases shipped 'ostk'. Handle both.
      local _scratch=$(mktemp -d)
      if tar -xzf /tmp/ostk-update.tar.gz -C "$_scratch" 2>/dev/null; then
        local _bin=$(find "$_scratch" -maxdepth 2 -type f -perm -u+x | head -1)
        if [[ -n "$_bin" ]]; then
          cp "$_bin" "$HOME/.local/bin/ostk" && \
            chmod +x "$HOME/.local/bin/ostk"
          # Clear all extended attributes (quarantine, provenance) and
          # ad-hoc sign so macOS Gatekeeper does not kill the binary.
          xattr -c "$HOME/.local/bin/ostk" 2>/dev/null
          codesign --force -s - "$HOME/.local/bin/ostk" >/dev/null 2>&1
        fi
      fi
      rm -rf "$_scratch" /tmp/ostk-update.tar.gz
      ostk_current=$(~/.local/bin/ostk --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+[.0-9]*' | head -1)
      # Post-upgrade verification: per the tori-always-updates rule, the
      # splash version line is a contract. If we entered the upgrade branch
      # but the binary still reports the old version, the install path
      # silently failed (download timeout, tar layout mismatch, codesign
      # rejection). Surface it instead of letting the splash lie.
      if [[ "$ostk_current" != "$ostk_latest" ]]; then
        printf '\033[38;2;255;80;80m  ✗ ostk upgrade did not take: still %s, wanted %s\033[0m\n' "$ostk_current" "$ostk_latest"
      fi
    fi
    echo "$ostk_current" > "$_ostk_tmp"
  ) &
  local _ostk_pid=$!

  (
    myos_behind=$(git -C ~/claude/torios fetch origin main 2>/dev/null && git -C ~/claude/torios rev-list HEAD..origin/main --count 2>/dev/null)
    if [[ "$myos_behind" -gt 0 ]] 2>/dev/null; then
      printf '\033[38;2;140;140;140m  Updating myOS (%s new commits)...\033[0m\n' "$myos_behind"
      git -C ~/claude/torios pull --ff-only origin main >/dev/null 2>&1
    fi
    echo "done" > "$_git_tmp"
  ) &
  local _git_pid=$!

  wait $_ostk_pid $_git_pid 2>/dev/null
  local ostk_current
  ostk_current=$(cat "$_ostk_tmp" 2>/dev/null)
  rm -f "$_ostk_tmp" "$_git_tmp"

  # --- Splash ---
  local myos_version
  myos_version=$(git -C ~/claude/torios describe --tags --abbrev=0 --match 'v*' 2>/dev/null)

  printf '\n'
  printf '\033[38;2;255;105;180m  ████████╗ \033[38;2;255;140;50m ██████╗  \033[38;2;180;100;255m██████╗  \033[38;2;100;149;237m██╗  \033[38;2;255;105;180m ██████╗  \033[38;2;255;140;50m███████╗\033[0m\n'
  printf '\033[38;2;255;105;180m  ╚══██╔══╝ \033[38;2;255;140;50m██╔═══██╗\033[38;2;180;100;255m██╔══██╗ \033[38;2;100;149;237m██║  \033[38;2;255;105;180m██╔═══██╗\033[38;2;255;140;50m██╔════╝\033[0m\n'
  printf '\033[38;2;255;105;180m     ██║    \033[38;2;255;140;50m██║   ██║\033[38;2;180;100;255m██████╔╝ \033[38;2;100;149;237m██║  \033[38;2;255;105;180m██║   ██║\033[38;2;255;140;50m███████╗\033[0m\n'
  printf '\033[38;2;255;105;180m     ██║    \033[38;2;255;140;50m██║   ██║\033[38;2;180;100;255m██╔══██╗ \033[38;2;100;149;237m██║  \033[38;2;255;105;180m██║   ██║\033[38;2;255;140;50m╚════██║\033[0m\n'
  printf '\033[38;2;255;105;180m     ██║    \033[38;2;255;140;50m╚██████╔╝\033[38;2;180;100;255m██║  ██║ \033[38;2;100;149;237m██║  \033[38;2;255;105;180m╚██████╔╝\033[38;2;255;140;50m███████║\033[0m\n'
  printf '\033[38;2;255;105;180m     ╚═╝    \033[38;2;255;140;50m ╚═════╝ \033[38;2;180;100;255m╚═╝  ╚═╝ \033[38;2;100;149;237m╚═╝  \033[38;2;255;105;180m ╚═════╝ \033[38;2;255;140;50m╚══════╝\033[0m\n'
  printf '\n'
  printf '\033[38;2;180;100;255m          ░▒▓ \033[38;2;255;105;180mYour personal OS \033[38;2;100;149;237m· \033[38;2;255;140;50mpowered by ostk \033[38;2;180;100;255m▓▒░\033[0m\n'
  printf '\033[38;2;140;140;140m          myOS %s · ostk %s\033[0m\n' "$myos_version" "$ostk_current"
  printf '\n'

  # --- Start ToriOS servers ---
  ~/claude/torios/scripts/dev-backend.sh </dev/null >/dev/null 2>&1 &
  be_pid=$!
  ~/claude/torios/scripts/dev-frontend.sh </dev/null >/dev/null 2>&1 &
  fe_pid=$!

  # Poll for backend readiness (5s ceiling).
  # dev-backend.sh serves HTTPS when ~/.myos/localhost.{key,crt} exist,
  # otherwise plain HTTP. Match the actual scheme so the probe does not
  # speak HTTP to an HTTPS listener (curl rc=52 = false "failed to start").
  # See feedback_readiness_probe.md.
  local _be_scheme="http"
  if [ -f "$HOME/.myos/localhost.key" ] && [ -f "$HOME/.myos/localhost.crt" ]; then
    _be_scheme="https"
  fi
  # Probe /api/health. The splash must trust the probe, NOT kill -0 $be_pid.
  # Rationale (→558): dev-backend.sh exits 1 on purpose when a uvicorn is
  # already listening (double-bind guard). In that case $be_pid is dead but
  # the actual server is up and healthy. If we gate on $be_pid we report
  # "✗ Backend failed to start" even though the backend is running fine.
  # NOTE: `local VAR` (bare re-declaration) inside a zsh loop prints
  # `VAR=VALUE` to stdout on the SECOND and later iterations, because zsh's
  # `local` is `typeset` and a second bare typeset of an already-local var
  # echoes its current value. Earlier versions of this block had
  # `local _response` and `local _http_code` inside the while body, which
  # leaked `_response=$'\n000'` lines on every retry. Declare once above
  # the loop and only assign inside.
  local _ready=0
  local _attempt=0
  # 60s ceiling: dev-backend.sh's kill-and-replace path can take ~50s on a
  # cold start (SIGTERM old uvicorn + 3s grace + SSL cert load + reload
  # worker fork + first /api/health OK). Earlier 15s budget caused false
  # "Backend failed to start" reports when the watchdog had just restarted
  # the backend seconds before tori() ran. Probe breaks on first 200, so
  # the happy path still finishes in ~1s.
  local _max_retries=60
  local _last_response=""
  local _response=""
  local _http_code=""
  while [ $_attempt -lt $_max_retries ]; do
    _attempt=$((_attempt + 1))
    _response=$(curl --silent --insecure --tlsv1.2 --tls-max 1.2 --connect-timeout 3 -m 5 -w "\n%{http_code}" "${_be_scheme}://127.0.0.1:8000/api/health" 2>&1)
    _http_code=$(echo "$_response" | tail -1)
    if [ "$_http_code" = "200" ]; then
      _ready=1
      break
    fi
    _last_response=$(echo "$_response" | sed '$d')
    if [ $_attempt -lt $_max_retries ]; then
      sleep 1
    fi
  done

  if [ "$_ready" -eq 1 ]; then
    printf '\033[38;2;100;200;100m  ✓ Backend running (port 8000)\033[0m\n'
  else
    printf '\033[38;2;255;80;80m  ✗ Backend failed to start\033[0m\n'
  fi
  # Probe vite on port 3010. Same rationale as backend (→558, →560):
  # dev-frontend.sh exits when 3010 is already bound, so $fe_pid dies
  # even though vite is serving. Trust the port probe, not kill -0.
  # Vite listens on IPv6 localhost, so use localhost (not 127.0.0.1).
  local _fe_scheme="http"
  if [ -f "$HOME/.myos/localhost.key" ] && [ -f "$HOME/.myos/localhost.crt" ]; then
    _fe_scheme="https"
  fi
  local _fe_ready=0
  for _j in $(seq 1 25); do
    if curl -sfk --connect-timeout 1 -m 1 "${_fe_scheme}://localhost:3010/" >/dev/null 2>&1; then
      _fe_ready=1; break
    fi
    sleep 0.2
  done
  if [ "$_fe_ready" -eq 1 ]; then
    printf '\033[38;2;100;200;100m  ✓ Frontend running (port 3010)\033[0m\n'
  else
    printf '\033[38;2;255;80;80m  ✗ Frontend failed to start\033[0m\n'
  fi
  printf '\n'

  # Boot ostk kernel. Advisory/trust warnings (humanfile OAE, primefile
  # mismatches) go to a log so the splash stays clean. Run `ostk boot`
  # directly or check ~/.myos/logs/ostk-boot.log to see full output.
  mkdir -p "$HOME/.myos/logs"
  local _boot_exit=0
  ~/.local/bin/ostk boot >>"$HOME/.myos/logs/ostk-boot.log" 2>&1 || _boot_exit=$?
  if [ "$_boot_exit" -ne 0 ]; then
    # Suppress known advisory noise (HUMANFILE OAE + primefile T0 trust anchor).
    # Only show the warning line if there are real errors beyond those.
    local _real_errors=0
    _real_errors=$(grep "^error:" "$HOME/.myos/logs/ostk-boot.log" \
      | grep -cv "ENTITYFILE OAE\|primefile.*trust root") || _real_errors=0
    if [ "$_real_errors" -gt 0 ]; then
      printf '\033[38;2;255;200;80m  ⚠ ostk boot warnings — see ~/.myos/logs/ostk-boot.log\033[0m\n'
    fi
  fi

  # Run :boot tack as soon as ostk is up
  ~/.local/bin/ostk tack ":boot" >/dev/null 2>&1

  # Show a human-friendly summary instead
  local _open _p0 _p1
  _open=$(grep -c '"status":"open"' ~/claude/torios/.ostk/needles.jsonl 2>/dev/null) || _open=0
  _p0=$(grep '"status":"open"' ~/claude/torios/.ostk/needles.jsonl 2>/dev/null | grep -c '"priority":"P0"') || _p0=0
  _p1=$(grep '"status":"open"' ~/claude/torios/.ostk/needles.jsonl 2>/dev/null | grep -c '"priority":"P1"') || _p1=0

  if [ "$_p0" -gt 0 ]; then
    printf '\033[38;2;255;80;80m  ⚠ %s urgent task%s need attention\033[0m\n' "$_p0" "$([ $_p0 -gt 1 ] && echo s)"
  fi
  if [ "$_p1" -gt 0 ]; then
    printf '\033[38;2;255;200;80m  %s high-priority task%s open\033[0m\n' "$_p1" "$([ $_p1 -gt 1 ] && echo s)"
  fi
  if [ "$_open" -gt 0 ]; then
    printf '\033[38;2;140;140;140m  %s open task%s total\033[0m\n' "$_open" "$([ $_open -gt 1 ] && echo s)"
  else
    printf '\033[38;2;100;200;100m  ✓ No open tasks\033[0m\n'
  fi
  printf '\n'

  # --- Optional agent launch ---
  # Opt-in via TORI_LAUNCH_AGENT. Default tori exits cleanly after the
  # splash so myOS stays generic (Claude/Codex/Cursor/plain shell all
  # work). When no agent is requested, leave the dev servers running
  # in the background (the user may still want to hit the UI).
  if [[ "${TORI_LAUNCH_AGENT:-}" == "claude" ]]; then
    command claude --dangerously-skip-permissions

    # When Claude exits, clean up the servers
    echo "Shutting down ToriOS servers..."
    kill $be_pid $fe_pid 2>/dev/null
    wait $be_pid $fe_pid 2>/dev/null
    echo "ToriOS shut down."
  fi
}
