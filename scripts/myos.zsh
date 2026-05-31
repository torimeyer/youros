# myos() - launch your OS
# Canonical, tracked source for the generic launch shell function.
# ~/.zshrc sources this file; do NOT redefine myos() in ~/.zshrc.
#
# This launcher is name-free and machine-agnostic so anyone can use it:
#   - The OS name on the splash comes from ~/.myos/settings.json
#     (os_name, falling back to instance_name, then a neutral default).
#   - Optional custom ASCII art: if ~/.myos/banner.sh exists it is sourced
#     just before the splash lines, so you can show your own logo without
#     putting it in the shared repo.
#   - The repo path comes from $MYOS_HOME (default ~/torios).
#
# Agent launch is OPT-IN. Set MYOS_LAUNCH_AGENT=claude to auto-run
# `claude update` during startup and launch the Claude CLI after the splash.
# Unset or any other value exits cleanly after the readiness probes with no
# agent launched.
#
# Parallel updates (ostk + ostk-recall + git), then splash, then servers,
# then (optional) agent.
myos() {
  local _home="${MYOS_HOME:-$HOME/torios}"
  cd "$_home" || return 1
  setopt LOCAL_OPTIONS NO_MONITOR

  local _launch_agent="${MYOS_LAUNCH_AGENT:-}"

  # OS name for the splash, read from settings (data, not hardcoded).
  local _os_name
  _os_name=$(python3 -c "import json;d=json.load(open('$HOME/.myos/settings.json'));print(d.get('os_name') or d.get('instance_name') or '')" 2>/dev/null)
  [[ -z "$_os_name" ]] && _os_name="yourOS"

  # --- Updates (parallel, non-blocking) ---
  # Claude update takes ~60s but only applies next launch. Opt-in, matching
  # the launch gate below. Fire-and-forget: script(1) gives the child its own
  # pty so /dev/tty writes don't cause "suspended (tty output)".
  if [[ "$_launch_agent" == "claude" ]]; then
    /usr/bin/script -q /dev/null sh -c 'command claude update' </dev/null >/dev/null 2>&1 &!
  fi

  # ostk + ostk-recall + git checks run in parallel (~0.5s each).
  local _ostk_tmp=$(mktemp) _git_tmp=$(mktemp) _recall_tmp=$(mktemp)

  (
    ostk_current=$(~/.local/bin/ostk --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+[.0-9]*' | head -1)
    ostk_latest=$(curl -s --connect-timeout 3 -m 5 https://api.github.com/repos/os-tack/ostk.ai/releases/latest 2>/dev/null | grep '"tag_name"' | sed -E 's/.*"v?([^"]*)".*/\1/')
    if [[ -n "$ostk_latest" && "$ostk_current" != "$ostk_latest" ]]; then
      printf '\033[38;2;140;140;140m  Updating ostk %s -> %s...\033[0m\n' "$ostk_current" "$ostk_latest"
      # v3.0.0+ uses darwin-universal with no v-prefix; older used v-prefixed
      # aarch64-apple-darwin. ~28MB asset, 120s budget; connect-timeout 5 bails
      # fast when GitHub is unreachable.
      local url="https://github.com/os-tack/ostk.ai/releases/download/v${ostk_latest}/ostk-${ostk_latest}-aarch64-apple-darwin.tar.gz"
      local _dl_err=""
      if ! curl -fSL --connect-timeout 5 -m 120 "$url" -o /tmp/ostk-update.tar.gz 2>/tmp/ostk-update.err; then
        _dl_err=$(cat /tmp/ostk-update.err 2>/dev/null)
        url="https://github.com/os-tack/ostk.ai/releases/download/v${ostk_latest}/ostk-${ostk_latest}-aarch64-apple-darwin-fuse.tar.gz"
        if ! curl -fSL --connect-timeout 5 -m 120 "$url" -o /tmp/ostk-update.tar.gz 2>/tmp/ostk-update.err; then
          _dl_err="$_dl_err; fuse: $(cat /tmp/ostk-update.err 2>/dev/null)"
          url="https://github.com/os-tack/ostk.ai/releases/download/v${ostk_latest}/ostk-v${ostk_latest}-aarch64-apple-darwin.tar.gz"
          if ! curl -fSL --connect-timeout 5 -m 120 "$url" -o /tmp/ostk-update.tar.gz 2>/tmp/ostk-update.err; then
            _dl_err="$_dl_err; legacy: $(cat /tmp/ostk-update.err 2>/dev/null)"
            printf '\033[38;2;255;80;80m  ✗ ostk download failed: %s\033[0m\n' "$_dl_err"
          fi
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
          # Clear quarantine xattrs and ad-hoc sign so Gatekeeper allows it.
          xattr -c "$HOME/.local/bin/ostk" 2>/dev/null
          codesign --force -s - "$HOME/.local/bin/ostk" >/dev/null 2>&1
        fi
      fi
      rm -rf "$_scratch" /tmp/ostk-update.tar.gz
      ostk_current=$(~/.local/bin/ostk --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+[.0-9]*' | head -1)
      # The splash version line is a contract: if we entered the upgrade branch
      # but the binary still reports the old version, the install path silently
      # failed. Surface it instead of letting the splash lie.
      if [[ "$ostk_current" != "$ostk_latest" ]]; then
        printf '\033[38;2;255;80;80m  ✗ ostk upgrade did not take: still %s, wanted %s\033[0m\n' "$ostk_current" "$ostk_latest"
      else
        # Restart a stale daemon so the boot splash and --version agree.
        local _anchor="$_home/.ostk/anchor.pid"
        if [[ -f "$_anchor" ]]; then
          local _daemon_ver _daemon_pid
          _daemon_ver=$(python3 -c "import json,sys; d=json.load(open('$_anchor')); print(d.get('ostk_version',''))" 2>/dev/null)
          _daemon_pid=$(python3 -c "import json,sys; d=json.load(open('$_anchor')); print(d.get('pid',''))" 2>/dev/null)
          if [[ -n "$_daemon_pid" && "$_daemon_ver" != "$ostk_current" ]]; then
            kill -TERM "$_daemon_pid" 2>/dev/null || true
          fi
        fi
      fi
    fi
    echo "$ostk_current" > "$_ostk_tmp"
  ) &
  local _ostk_pid=$!

  (
    # ostk-recall is a SEPARATE binary (~/.local/bin/ostk-recall) with its own
    # release stream (os-tack/ostk-recall). Not bundled with ostk, so update it
    # here in parallel. Asset names carry a v-prefix and a -macos-arm64 suffix,
    # so read the download URL from the release JSON rather than guessing.
    recall_current=$(~/.local/bin/ostk-recall --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+[.0-9]*([-.][A-Za-z0-9.]+)?' | head -1)
    local _recall_json
    _recall_json=$(curl -s --connect-timeout 5 -m 8 https://api.github.com/repos/os-tack/ostk-recall/releases/latest 2>/dev/null)
    recall_latest=$(printf '%s' "$_recall_json" | grep '"tag_name"' | head -1 | sed -E 's/.*"v?([^"]*)".*/\1/')
    if [[ -n "$recall_latest" && "$recall_current" != "$recall_latest" ]]; then
      local _recall_before="$recall_current"
      printf '\033[38;2;140;140;140m  Updating ostk-recall %s -> %s...\033[0m\n' "$recall_current" "$recall_latest"
      local _recall_url
      _recall_url=$(printf '%s' "$_recall_json" | grep '"browser_download_url"' | grep -iE 'macos-arm64|aarch64-apple-darwin|darwin-universal' | head -1 | sed -E 's/.*"(https[^"]*)".*/\1/')
      if [[ -z "$_recall_url" ]]; then
        _recall_url="https://github.com/os-tack/ostk-recall/releases/download/v${recall_latest}/ostk-recall-v${recall_latest}-macos-arm64.tar.gz"
      fi
      if curl -fSL --connect-timeout 5 -m 120 "$_recall_url" -o /tmp/ostk-recall-update.tar.gz 2>/tmp/ostk-recall-update.err; then
        local _rscratch=$(mktemp -d)
        if tar -xzf /tmp/ostk-recall-update.tar.gz -C "$_rscratch" 2>/dev/null; then
          local _rbin=$(find "$_rscratch" -maxdepth 2 -type f -perm -u+x -name 'ostk-recall*' | head -1)
          [[ -z "$_rbin" ]] && _rbin=$(find "$_rscratch" -maxdepth 2 -type f -perm -u+x | head -1)
          if [[ -n "$_rbin" ]]; then
            cp "$_rbin" "$HOME/.local/bin/ostk-recall" && chmod +x "$HOME/.local/bin/ostk-recall"
            xattr -c "$HOME/.local/bin/ostk-recall" 2>/dev/null
            codesign --force -s - "$HOME/.local/bin/ostk-recall" >/dev/null 2>&1
          fi
        fi
        rm -rf "$_rscratch"
      else
        printf '\033[38;2;255;80;80m  ✗ ostk-recall download failed: %s\033[0m\n' "$(cat /tmp/ostk-recall-update.err 2>/dev/null)"
      fi
      rm -f /tmp/ostk-recall-update.tar.gz /tmp/ostk-recall-update.err
      recall_current=$(~/.local/bin/ostk-recall --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+[.0-9]*([-.][A-Za-z0-9.]+)?' | head -1)
      # Verify by change, not exact-match: the binary's --version may render the
      # prerelease tag differently than the GitHub tag.
      if [[ -z "$recall_current" || "$recall_current" == "$_recall_before" ]]; then
        printf '\033[38;2;255;80;80m  ✗ ostk-recall upgrade did not take: still %s, wanted %s\033[0m\n' "$recall_current" "$recall_latest"
      fi
    fi
    echo "$recall_current" > "$_recall_tmp"
  ) &
  local _recall_pid=$!

  (
    # GIT_TERMINAL_PROMPT=0 prevents a credential prompt hanging in a background
    # subshell. http.* timeouts bound the HTTPS fetch so a stall exits.
    GIT_TERMINAL_PROMPT=0 git -C "$_home" \
      -c http.connectTimeout=10 -c http.lowSpeedLimit=1 -c http.lowSpeedTime=15 \
      fetch origin main 2>/dev/null || true
    myos_behind=$(git -C "$_home" rev-list HEAD..origin/main --count 2>/dev/null || echo 0)
    # Skip pull when local has unpushed commits: ff-only would fail anyway, and
    # git pull runs a second fetch that can hang (root cause of ->1776).
    _ahead=$(git -C "$_home" rev-list origin/main..HEAD --count 2>/dev/null || echo 0)
    if [[ "${myos_behind:-0}" -gt 0 && "${_ahead:-0}" -eq 0 ]]; then
      printf '\033[38;2;140;140;140m  Updating %s (%s new commits)...\033[0m\n' "$_os_name" "$myos_behind"
      GIT_TERMINAL_PROMPT=0 git -C "$_home" \
        -c http.connectTimeout=10 -c http.lowSpeedLimit=1 -c http.lowSpeedTime=15 \
        pull --ff-only origin main >/dev/null 2>&1 || true
    fi
    echo "done" > "$_git_tmp"
  ) &
  local _git_pid=$!

  wait $_ostk_pid $_recall_pid $_git_pid 2>/dev/null
  local ostk_current recall_current
  ostk_current=$(cat "$_ostk_tmp" 2>/dev/null)
  recall_current=$(cat "$_recall_tmp" 2>/dev/null)
  rm -f "$_ostk_tmp" "$_git_tmp" "$_recall_tmp"

  # --- Splash ---
  local myos_version
  myos_version=$(git -C "$_home" describe --tags --abbrev=0 --match 'v*' 2>/dev/null)

  printf '\n'
  # Optional custom ASCII art (e.g. a personal logo) lives OUTSIDE the repo.
  # If present, it is sourced here and can read $_os_name.
  [[ -f "$HOME/.myos/banner.sh" ]] && source "$HOME/.myos/banner.sh"
  printf '\033[38;2;180;100;255m          ░▒▓ \033[38;2;255;105;180mYour personal OS \033[38;2;100;149;237m· \033[38;2;255;140;50mpowered by ostk \033[38;2;180;100;255m▓▒░\033[0m\n'
  printf '\033[38;2;140;140;140m          %s %s · ostk %s · recall %s\033[0m\n' "$_os_name" "$myos_version" "$ostk_current" "$recall_current"
  printf '\n'

  # --- Start servers ---
  "$_home/scripts/dev-backend.sh" </dev/null >/dev/null 2>&1 &
  be_pid=$!
  "$_home/scripts/dev-frontend.sh" </dev/null >/dev/null 2>&1 &
  fe_pid=$!

  # Poll for backend readiness. dev-backend.sh serves HTTPS when
  # ~/.myos/localhost.{key,crt} exist, otherwise plain HTTP. Match the scheme
  # so the probe does not speak HTTP to an HTTPS listener. See
  # feedback_readiness_probe.md.
  local _be_scheme="http"
  if [ -f "$HOME/.myos/localhost.key" ] && [ -f "$HOME/.myos/localhost.crt" ]; then
    _be_scheme="https"
  fi
  # Trust the /api/health probe, NOT kill -0 $be_pid (->558): dev-backend.sh
  # exits 1 on purpose when a uvicorn already listens, so $be_pid can be dead
  # while the server is up. Declare loop vars once above the loop: a bare
  # `local VAR` re-declaration inside a zsh loop echoes VAR=VALUE on later
  # iterations and leaks output.
  local _ready=0
  local _attempt=0
  # 60s ceiling: dev-backend.sh's kill-and-replace can take ~50s on a cold
  # start. Probe breaks on first 200, so the happy path finishes in ~1s.
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
  # Probe vite on port 3010. Same rationale as backend (->558, ->560): trust
  # the port probe, not kill -0. Vite listens on IPv6 localhost, so use
  # localhost (not 127.0.0.1).
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

  # Boot ostk kernel. Advisory/trust warnings go to a log so the splash stays
  # clean. Run `ostk boot` directly or check ~/.myos/logs/ostk-boot.log.
  mkdir -p "$HOME/.myos/logs"
  local _boot_exit=0
  ~/.local/bin/ostk boot >>"$HOME/.myos/logs/ostk-boot.log" 2>&1 || _boot_exit=$?
  if [ "$_boot_exit" -ne 0 ]; then
    local _real_errors=0
    _real_errors=$(grep "^error:" "$HOME/.myos/logs/ostk-boot.log" \
      | grep -cv "ENTITYFILE OAE\|primefile.*trust root") || _real_errors=0
    if [ "$_real_errors" -gt 0 ]; then
      printf '\033[38;2;255;200;80m  ⚠ ostk boot warnings — see ~/.myos/logs/ostk-boot.log\033[0m\n'
    fi
  fi

  # Run :boot tack as soon as ostk is up
  ~/.local/bin/ostk tack ":boot" >/dev/null 2>&1

  # Human-friendly task summary
  local _open _p0 _p1
  _open=$(grep -c '"status":"open"' "$_home/.ostk/needles.jsonl" 2>/dev/null) || _open=0
  _p0=$(grep '"status":"open"' "$_home/.ostk/needles.jsonl" 2>/dev/null | grep -c '"priority":"P0"') || _p0=0
  _p1=$(grep '"status":"open"' "$_home/.ostk/needles.jsonl" 2>/dev/null | grep -c '"priority":"P1"') || _p1=0

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
  # Opt-in via MYOS_LAUNCH_AGENT. Default exits cleanly after the splash so
  # the OS stays generic
  # (Claude/Codex/Cursor/plain shell all work), leaving the dev servers up.
  if [[ "$_launch_agent" == "claude" ]]; then
    command claude --dangerously-skip-permissions

    echo "Shutting down servers..."
    kill $be_pid $fe_pid 2>/dev/null
    wait $be_pid $fe_pid 2>/dev/null
    echo "Shut down."
  fi
}
