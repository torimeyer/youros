#!/bin/bash
# myOS installer
# Usage: ./install.sh [--with-claude-hooks] [--help]

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# --- Flag parsing ---
#
# --with-claude-hooks wires a user-global Claude Code hook at
# ~/.claude/hooks/register-agent.sh that fires on EVERY Claude Code
# session on this machine (regardless of project) and POSTs to the
# local myOS backend so Task-tool subagents show up on the Agents
# page. Off by default because it has machine-wide scope and most
# users never open Claude Code in a non-myOS project anyway.
# Also honours MYOS_INSTALL_CLAUDE_HOOKS=1 for scripted installs.
WITH_CLAUDE_HOOKS="${MYOS_INSTALL_CLAUDE_HOOKS:-0}"

while [ $# -gt 0 ]; do
    case "$1" in
        --with-claude-hooks) WITH_CLAUDE_HOOKS=1; shift ;;
        --without-claude-hooks) WITH_CLAUDE_HOOKS=0; shift ;;
        --help|-h)
            cat <<'EOF'
Usage: ./install.sh [--with-claude-hooks] [--help]

Installs ostk (if absent), sets up the Python backend in api/.venv,
installs and builds the frontend in app/, seeds ~/.myos/settings.json,
and adds `myos` / `myos-update` aliases to your shell rc.

Flags:
  --with-claude-hooks     Also install a user-global Claude Code hook
                          at ~/.claude/hooks/register-agent.sh. Fires
                          on every Claude Code session on this machine
                          (not just myOS projects) and registers
                          Task-tool subagents with the local myOS
                          backend. Machine-wide. Off by default.
                          Equivalent env var: MYOS_INSTALL_CLAUDE_HOOKS=1

  --without-claude-hooks  Explicitly skip the hook install (the default;
                          use this if MYOS_INSTALL_CLAUDE_HOOKS=1 is
                          set in your environment and you want to
                          override it for one run).

  --help, -h              Print this message and exit.
EOF
            exit 0
            ;;
        *) echo "Unknown flag: $1" >&2; echo "Try --help." >&2; exit 2 ;;
    esac
done

INSTALL_DIR="${MYOS_DIR:-$HOME/myos}"

echo ""
echo -e "${BLUE}=== myOS Installer ===${NC}"
echo ""

# --- Check prerequisites ---
#
# myOS lists what it needs and exits if anything is missing. We do not try to
# install system packages on your behalf. Install the prereqs your way (apt,
# dnf, pacman, nvm, pyenv, asdf, whatever you use), then run this again.

MISSING=()

need() {
    if ! command -v "$1" &> /dev/null; then
        MISSING+=("$2")
    fi
}

echo "Checking requirements..."

need git    "git"
need curl   "curl"
need python3 "python3 (3.11 or newer)"
need node   "node (18 or newer)"
need npm    "npm (ships with node)"

# Version checks for things that are present but too old.
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
    if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]); then
        MISSING+=("python3 3.11 or newer (found $PYTHON_VERSION)")
    fi
    # On Debian/Ubuntu, python3-venv is a separate package. Detect and report,
    # do not install.
    if ! python3 -c "import venv" &> /dev/null; then
        MISSING+=("python3 venv module (the 'venv' standard library package)")
    fi
fi

if command -v node &> /dev/null; then
    NODE_MAJOR=$(node -v | sed 's/v//' | cut -d. -f1)
    if [ "$NODE_MAJOR" -lt 18 ]; then
        MISSING+=("node 18 or newer (found $(node -v))")
    fi
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    echo ""
    echo -e "${RED}Missing prerequisites:${NC}"
    for item in "${MISSING[@]}"; do
        echo "  - $item"
    done
    echo ""
    echo "Install these with whatever tool you prefer, then run ./install.sh again."
    exit 1
fi

echo -e "${GREEN}All requirements met.${NC}"
echo ""

# --- Install ostk if not present ---

if ! command -v ostk &> /dev/null; then
    echo "Installing ostk..."
    OSTK_BIN_DIR="$HOME/.local/bin"
    mkdir -p "$OSTK_BIN_DIR"

    ARCH=$(uname -m)
    case "$ARCH" in
        arm64) ARCH="aarch64" ;;
    esac

    OS_TAG=$(uname -s)
    case "$OS_TAG" in
        Linux)  OS_TAG="unknown-linux-musl" ;;
        Darwin) OS_TAG="apple-darwin" ;;
        *)      echo -e "${YELLOW}Unsupported OS for ostk. Skipping.${NC}"; OS_TAG="" ;;
    esac

    if [ -n "$OS_TAG" ]; then
        PLATFORM="${ARCH}-${OS_TAG}"
        OSTK_REPO="os-tack/ostk.ai"
        VERSION=$(curl -fsSL "https://api.github.com/repos/${OSTK_REPO}/releases/latest" \
            | grep '"tag_name"' | head -1 | cut -d'"' -f4)

        if [ -n "$VERSION" ]; then
            # GitHub tag is "v4.0.0" but tarball filename is "ostk-4.0.0-..." (no v).
            VERSION_NUMBER="${VERSION#v}"
            TARBALL="ostk-${VERSION_NUMBER}-${PLATFORM}.tar.gz"
            OSTK_URL="https://github.com/${OSTK_REPO}/releases/download/${VERSION}/${TARBALL}"
            OSTK_TMP=$(mktemp -d)

            if curl -fsSL "$OSTK_URL" -o "$OSTK_TMP/$TARBALL" 2>/dev/null; then
                tar -xzf "$OSTK_TMP/$TARBALL" -C "$OSTK_TMP"
                if [ -f "$OSTK_TMP/ostk" ]; then
                    install -m 755 "$OSTK_TMP/ostk" "$OSTK_BIN_DIR/ostk"
                    echo -e "${GREEN}ostk ${VERSION} installed to $OSTK_BIN_DIR/ostk${NC}"
                else
                    echo -e "${YELLOW}Could not find ostk binary in download. Skipping.${NC}"
                fi
            else
                echo -e "${YELLOW}Could not download ostk automatically.${NC}"
                echo "You can install it manually later. myOS will still work without it"
                echo "for basic features (chat, tasks, settings)."
            fi
            rm -rf "$OSTK_TMP"
        else
            echo -e "${YELLOW}Could not determine latest ostk version. Skipping.${NC}"
        fi
    fi

    # Add to PATH if needed
    if [[ ":$PATH:" != *":$OSTK_BIN_DIR:"* ]]; then
        SHELL_RC="$HOME/.zshrc"
        [ -f "$HOME/.bashrc" ] && [ ! -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.bashrc"
        echo "" >> "$SHELL_RC"
        echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$SHELL_RC"
        export PATH="$OSTK_BIN_DIR:$PATH"
        echo "Added $OSTK_BIN_DIR to PATH in $SHELL_RC"
    fi
else
    echo -e "${GREEN}ostk already installed.${NC}"
fi
echo ""

# --- Locate the repo ---

# If running from inside the repo, use it directly
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/start.sh" ] && [ -d "$SCRIPT_DIR/app" ] && [ -d "$SCRIPT_DIR/api" ]; then
    INSTALL_DIR="$SCRIPT_DIR"
    echo "Running from existing myOS repo at $INSTALL_DIR"
elif [ -d "$INSTALL_DIR" ]; then
    echo "myOS directory already exists at $INSTALL_DIR"
    echo "Updating..."
    cd "$INSTALL_DIR"
    git pull --ff-only 2>/dev/null || echo -e "${YELLOW}Could not auto-update. Continuing with existing files.${NC}"
else
    echo "Downloading myOS to $INSTALL_DIR..."
    git clone https://github.com/torimeyer/youros.git "$INSTALL_DIR" 2>/dev/null || \
    git clone git@github.com:torimeyer/youros.git "$INSTALL_DIR" 2>/dev/null || {
        echo -e "${RED}Could not clone the repo. Check your access and try again.${NC}"
        exit 1
    }
fi
cd "$INSTALL_DIR"
echo ""

# --- Seed the MCP server config from the tracked template ---
# .mcp.json holds per-user secrets (Stitch API key) and is gitignored.
# .mcp.json.example ships with the repo as a skeleton. Copy it into
# place on first install so the user has a working config to edit.
if [ -f "$INSTALL_DIR/.mcp.json.example" ] && [ ! -f "$INSTALL_DIR/.mcp.json" ]; then
    cp "$INSTALL_DIR/.mcp.json.example" "$INSTALL_DIR/.mcp.json"
    echo "Seeded .mcp.json from the template. Paste your own Stitch API key"
    echo "into .mcp.json to enable the Stitch MCP server. Other MCP servers"
    echo "work without any further setup."
    echo ""
fi

# --- Set up the Python backend ---

echo "Setting up the backend..."
cd "$INSTALL_DIR/api"
PYTHON_BIN=""
for cand in python3.13 python3.12 python3.11 python3.10; do
    if command -v "$cand" >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v "$cand")"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    echo "Error: need Python 3.11 or newer. Install it with your system package manager or via pyenv, then re-run ./install.sh."
    exit 1
fi
"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo -e "${GREEN}Backend ready.${NC}"
echo ""

# --- Set up the frontend ---

echo "Setting up the frontend..."
cd "$INSTALL_DIR/app"
npm install --silent
echo "Building the app (this takes a moment)..."
npm run build
echo -e "${GREEN}Frontend ready.${NC}"
echo ""

# --- Set up default settings if none exist ---

MYOS_CONFIG_DIR="$HOME/.myos"
if [ ! -f "$MYOS_CONFIG_DIR/settings.json" ]; then
    mkdir -p "$MYOS_CONFIG_DIR"
    cp "$INSTALL_DIR/settings.default.json" "$MYOS_CONFIG_DIR/settings.json"
    echo -e "${GREEN}Default settings created at $MYOS_CONFIG_DIR/settings.json${NC}"
else
    echo "Settings already exist. Keeping your current preferences."
fi
echo ""

# --- Initialize ostk if available ---

if command -v ostk &> /dev/null; then
    echo "Setting up ostk..."
    cd "$INSTALL_DIR"
    ostk init 2>/dev/null || echo -e "${YELLOW}ostk init skipped (may already be initialized).${NC}"
    echo ""
fi

# --- Stage the register-agent hook file -------------------------------
# Always copy .claude/hooks/register-agent.sh and its lib to
# ~/.myos/hooks/ so myos-track / myos-claude / --with-claude-hooks all
# point at the same canonical location. Nothing fires yet — this just
# places the artifact. Idempotent; refreshes on every install.
STAGED_HOOKS_DIR="$HOME/.myos/hooks"
mkdir -p "$STAGED_HOOKS_DIR/lib"
if [ -f "$INSTALL_DIR/.claude/hooks/register-agent.sh" ]; then
    cp -f "$INSTALL_DIR/.claude/hooks/register-agent.sh" "$STAGED_HOOKS_DIR/register-agent.sh"
    chmod +x "$STAGED_HOOKS_DIR/register-agent.sh"
fi
if [ -f "$INSTALL_DIR/.claude/hooks/lib/drain-pending.sh" ]; then
    cp -f "$INSTALL_DIR/.claude/hooks/lib/drain-pending.sh" "$STAGED_HOOKS_DIR/lib/drain-pending.sh"
fi
echo "Staged register-agent hook in $STAGED_HOOKS_DIR"
echo ""

# --- Install Claude Code hooks into ~/.claude/ (opt-in) --------------
# Wires the Agent PreToolUse register hook globally so every Claude
# Code session on this machine registers its subagents with myOS,
# not just sessions inside this repo. Scoped off by default because
# the scope is machine-wide, not per-project. Enable with
# --with-claude-hooks or MYOS_INSTALL_CLAUDE_HOOKS=1. Idempotent.
#
# Per-project alternatives (no ~/.claude/ modification):
#   myos-track                    enable tracking in current repo
#   myos-claude                   one-shot wrapper around `claude`
if [ "$WITH_CLAUDE_HOOKS" = "1" ]; then
    if [ -x "$INSTALL_DIR/scripts/install-claude-hooks.sh" ]; then
        echo "Wiring global Claude Code hooks into ~/.claude/..."
        bash "$INSTALL_DIR/scripts/install-claude-hooks.sh" --from "$INSTALL_DIR" \
            || echo -e "${YELLOW}Claude Code hook install skipped (non-fatal).${NC}"
        echo ""
    fi
else
    echo "Skipping global Claude Code hook install."
    echo "Project-local hooks under .claude/hooks/ still activate when Claude"
    echo "Code is opened in this repo."
    echo ""
    echo "To opt any OTHER project into myOS subagent tracking:"
    echo "  myos-track              (persistent, writes .claude/settings.local.json)"
    echo "  myos-claude             (one-shot, cleans up on exit)"
    echo ""
    echo "To register every Claude Code session on this machine (any project),"
    echo "rerun with --with-claude-hooks."
    echo ""
fi

# --- Set up launchd agents (macOS only) ---
# Renders the plist templates in ops/ into ~/Library/LaunchAgents/ and
# bootstraps both agents so they start immediately and at every login.
# Guarded to macOS; Linux installs skip this block entirely.

if [ "$(uname)" = "Darwin" ]; then
    echo "Setting up launchd agents for auto-start..."

    # Ensure cert setup has run (idempotent). This creates ~/.myos/localhost.{key,crt}
    # if mkcert or the security add-trusted-cert path succeeds. We need the result
    # before computing the health URL for the watchdog plist.
    if [ -x "$INSTALL_DIR/scripts/setup-localhost-cert.sh" ]; then
        bash "$INSTALL_DIR/scripts/setup-localhost-cert.sh" 2>/dev/null \
            || echo -e "${YELLOW}Localhost cert setup skipped (non-fatal). Backend will use http.${NC}"
    fi

    # Compute the health probe URL that the watchdog plist will use.
    if [ -f "$HOME/.myos/localhost.key" ] && [ -f "$HOME/.myos/localhost.crt" ]; then
        HEALTH_URL="https://127.0.0.1:8000/api/health"
    else
        HEALTH_URL="http://127.0.0.1:8000/api/health"
    fi

    # Create the log directory launchd will write to.
    mkdir -p "$HOME/.myos/logs"

    LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
    mkdir -p "$LAUNCH_AGENTS_DIR"

    # Render each plist template: substitute __INSTALL_DIR__, __HOME__, __HEALTH_URL__.
    for tmpl in com.myos.backend com.myos.watchdog; do
        SRC="$INSTALL_DIR/ops/${tmpl}.plist.template"
        DEST="$LAUNCH_AGENTS_DIR/${tmpl}.plist"
        if [ ! -f "$SRC" ]; then
            echo -e "${YELLOW}Warning: $SRC not found; skipping ${tmpl} agent.${NC}"
            continue
        fi
        sed \
            -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
            -e "s|__HOME__|$HOME|g" \
            -e "s|__HEALTH_URL__|$HEALTH_URL|g" \
            "$SRC" > "$DEST"
        echo "  Rendered $DEST"
    done

    # Bootstrap (or reload) both agents. launchctl bootstrap is idempotent
    # on 10.15+; if the agent is already loaded we bootout first.
    USER_UID=$(id -u)
    for label in com.myos.backend com.myos.watchdog; do
        plist="$LAUNCH_AGENTS_DIR/${label}.plist"
        [ -f "$plist" ] || continue
        # Unload if already running so we pick up any plist changes.
        launchctl bootout "gui/${USER_UID}/${label}" 2>/dev/null || true
        if launchctl bootstrap "gui/${USER_UID}" "$plist" 2>/dev/null; then
            echo -e "  ${GREEN}${label} bootstrapped.${NC}"
        else
            echo -e "  ${YELLOW}${label}: bootstrap returned non-zero (may already be registered).${NC}"
        fi
    done

    echo -e "${GREEN}launchd agents installed. Backend starts at login and recovers automatically.${NC}"
    echo ""
fi

# --- Create startup shortcut ---

chmod +x "$INSTALL_DIR/start.sh"

SHELL_RC="$HOME/.zshrc"
[ -f "$HOME/.bashrc" ] && [ ! -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.bashrc"

# Add alias if not already present
if ! grep -q "alias myos=" "$SHELL_RC" 2>/dev/null; then
    echo "" >> "$SHELL_RC"
    echo "alias myos='$INSTALL_DIR/start.sh'" >> "$SHELL_RC"
    echo "Added 'myos' command to $SHELL_RC"
fi

# Add safe-update alias if not already present and update.sh exists.
if [ -f "$INSTALL_DIR/update.sh" ] && ! grep -q "alias myos-update=" "$SHELL_RC" 2>/dev/null; then
    chmod +x "$INSTALL_DIR/update.sh"
    echo "" >> "$SHELL_RC"
    echo "alias myos-update='$INSTALL_DIR/update.sh'" >> "$SHELL_RC"
    echo "Added 'myos-update' command to $SHELL_RC"
fi

# myos-track: enable/disable myOS subagent tracking in the current repo
# (no global modification). Writes .claude/settings.local.json.
if [ -f "$INSTALL_DIR/myos-track.sh" ] && ! grep -q "alias myos-track=" "$SHELL_RC" 2>/dev/null; then
    chmod +x "$INSTALL_DIR/myos-track.sh"
    echo "" >> "$SHELL_RC"
    echo "alias myos-track='$INSTALL_DIR/myos-track.sh'" >> "$SHELL_RC"
    echo "Added 'myos-track' command to $SHELL_RC"
fi

# myos-claude: one-shot tracked Claude Code session (transient, cleans
# up .claude/settings.local.json on exit).
if [ -f "$INSTALL_DIR/myos-claude.sh" ] && ! grep -q "alias myos-claude=" "$SHELL_RC" 2>/dev/null; then
    chmod +x "$INSTALL_DIR/myos-claude.sh"
    echo "" >> "$SHELL_RC"
    echo "alias myos-claude='$INSTALL_DIR/myos-claude.sh'" >> "$SHELL_RC"
    echo "Added 'myos-claude' command to $SHELL_RC"
fi

echo ""
echo -e "${GREEN}=== myOS is installed! ===${NC}"
echo ""
echo "To start myOS:"
echo "  1. Open a new Terminal window (so the 'myos' command is available)"
echo "  2. Type: myos"
echo "  3. Your browser will open automatically"
echo ""
echo "Or run it right now:"
echo "  $INSTALL_DIR/start.sh"
echo ""
echo "Optional: if you want to use a Claude subscription instead of an API key,"
echo "install the Claude command line tool yourself:"
echo "  npm install -g @anthropic-ai/claude-code"
echo "myOS works fine without it."
echo ""
