# youros() - launch yourOS
# Integrated with ostk-cache for token savings (~21%)
youros() {
  local REPO_ROOT="$HOME/claude/torios"
  cd "$REPO_ROOT" || return 1

  local _agent="${1:-${TORI_LAUNCH_AGENT:-}}"

  # --- ostk-cache automation ---
  # Check if proxy is listening on port 9090
  if ! lsof -i :9090 >/dev/null 2>&1; then
    echo "Starting ostk-cache proxy on port 9090..."
    # Ensure mapping exists (using the confirmed-working tmp location)
    if [ ! -f /tmp/ostk-test/file_cache.jsonl ]; then
      echo "Warning: /tmp/ostk-test/file_cache.jsonl missing. Mechanism 4 will be inactive."
    fi
    nohup "$REPO_ROOT/.ostk/bin/ostk-cache" --port 9090 --mode mutate --ostk-dir /tmp/ostk-test > "$REPO_ROOT/.ostk/cache.log" 2>&1 &
    # Give it a moment to bind
    sleep 1
  fi
  export ANTHROPIC_BASE_URL=http://127.0.0.1:9090

  # --- Updates (parallel) ---
  if [[ "$_agent" == "claude" ]]; then
    nohup sh -c 'command claude update' > /tmp/youros-update.log 2>&1 < /dev/null & disown
  elif [[ "$_agent" == "gemini" ]]; then
    nohup sh -c 'npm update -g @google/gemini-cli' > /tmp/youros-update.log 2>&1 < /dev/null & disown
  fi

  # --- yourOS Launch sequence ---
  printf '\033[38;2;255;105;180m  ████████╗ \033[38;2;255;140;50m ██████╗  \033[38;2;180;100;255m██████╗  \033[38;2;100;149;237m██╗  \033[38;2;255;105;180m ██████╗  \033[38;2;255;140;50m███████╗\033[0m\n'
  printf '\033[38;2;180;100;255m          ░▒▓ \033[38;2;255;105;180mYour personal OS \033[38;2;100;149;237m· \033[38;2;255;140;50mpowered by ostk \033[38;2;180;100;255m▓▒░\033[0m\n'
  printf '\033[38;2;140;140;140m          (Token Savings Active: ANTHROPIC_BASE_URL=9090)\033[0m\n\n'

  # Start servers
  "$REPO_ROOT/scripts/dev-backend.sh" </dev/null >/dev/null 2>&1 &
  "$REPO_ROOT/scripts/dev-frontend.sh" </dev/null >/dev/null 2>&1 &

  # Opt-in: Launch Agent if requested
  if [[ "$_agent" == "claude" ]]; then
    command claude --dangerously-skip-permissions
  elif [[ "$_agent" == "gemini" ]]; then
    command gemini
  else
    echo "yourOS is starting. Point your browser to http://localhost:8000"
  fi
}
