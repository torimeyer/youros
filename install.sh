#!/bin/bash
# myOS installer
# Usage: ./install.sh (from inside the repo)

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

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
need python3 "python3 (3.9 or newer)"
need node   "node (18 or newer)"
need npm    "npm (ships with node)"

# Version checks for things that are present but too old.
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
    if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 9 ]); then
        MISSING+=("python3 3.9 or newer (found $PYTHON_VERSION)")
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
            TARBALL="ostk-${VERSION}-${PLATFORM}.tar.gz"
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
    git clone https://github.com/torimeyer/myos.git "$INSTALL_DIR" 2>/dev/null || \
    git clone git@github.com:torimeyer/myos.git "$INSTALL_DIR" 2>/dev/null || {
        echo -e "${RED}Could not clone the repo. Check your access and try again.${NC}"
        exit 1
    }
fi
cd "$INSTALL_DIR"
echo ""

# --- Set up the Python backend ---

echo "Setting up the backend..."
cd "$INSTALL_DIR/api"
python3 -m venv .venv
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
