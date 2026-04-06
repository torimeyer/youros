#!/bin/bash
# YourOS installer
# Usage: curl -fsSL https://[repo-url]/install.sh | bash
#    or: ./install.sh

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

INSTALL_DIR="${YOUROS_DIR:-$HOME/youros}"

echo ""
echo -e "${BLUE}=== YourOS Installer ===${NC}"
echo ""

# --- Check prerequisites ---

check_cmd() {
    if ! command -v "$1" &> /dev/null; then
        echo -e "${RED}Missing: $1${NC}"
        echo "$2"
        exit 1
    fi
}

echo "Checking requirements..."

check_cmd git "Install git: https://git-scm.com"

check_cmd python3 "Install Python 3: https://python.org/downloads"

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 9 ]); then
    echo -e "${RED}Python 3.9+ required (found $PYTHON_VERSION)${NC}"
    exit 1
fi

check_cmd node "Install Node.js 18+: https://nodejs.org"

NODE_MAJOR=$(node -v | sed 's/v//' | cut -d. -f1)
if [ "$NODE_MAJOR" -lt 18 ]; then
    echo -e "${RED}Node.js 18+ required (found $(node -v))${NC}"
    exit 1
fi

check_cmd npm "npm should come with Node.js. Reinstall Node: https://nodejs.org"

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
            TMPDIR=$(mktemp -d)
            trap 'rm -rf "$TMPDIR"' EXIT

            if curl -fsSL "$OSTK_URL" -o "$TMPDIR/$TARBALL" 2>/dev/null; then
                tar -xzf "$TMPDIR/$TARBALL" -C "$TMPDIR"
                if [ -f "$TMPDIR/ostk" ]; then
                    install -m 755 "$TMPDIR/ostk" "$OSTK_BIN_DIR/ostk"
                    echo -e "${GREEN}ostk ${VERSION} installed to $OSTK_BIN_DIR/ostk${NC}"
                else
                    echo -e "${YELLOW}Could not find ostk binary in download. Skipping.${NC}"
                fi
            else
                echo -e "${YELLOW}Could not download ostk automatically.${NC}"
                echo "You can install it manually later. YourOS will still work without it"
                echo "for basic features (chat, tasks, settings)."
            fi
        else
            echo -e "${YELLOW}Could not determine latest ostk version. Skipping.${NC}"
        fi
    fi

    # Add to PATH if needed
    if [[ ":$PATH:" != *":$OSTK_BIN_DIR:"* ]]; then
        SHELL_RC="$HOME/.zshrc"
        [ -f "$HOME/.bashrc" ] && [ ! -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.bashrc"
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
    echo "Running from existing YourOS repo at $INSTALL_DIR"
elif [ -d "$INSTALL_DIR" ]; then
    echo "YourOS directory already exists at $INSTALL_DIR"
    echo "Updating..."
    cd "$INSTALL_DIR"
    git pull --ff-only 2>/dev/null || echo -e "${YELLOW}Could not auto-update. Continuing with existing files.${NC}"
else
    echo "Downloading YourOS to $INSTALL_DIR..."
    git clone git@github.com:torimeyer/youros.git "$INSTALL_DIR" 2>/dev/null || {
        echo -e "${RED}Could not clone the repo. Check your SSH keys and access, then try again.${NC}"
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
pip install -q -r requirements.txt
echo -e "${GREEN}Backend ready.${NC}"
echo ""

# --- Set up the frontend ---

echo "Setting up the frontend..."
cd "$INSTALL_DIR/app"
npm install --silent 2>/dev/null
echo "Building the app (this takes a moment)..."
npm run build
echo -e "${GREEN}Frontend ready.${NC}"
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
if ! grep -q "alias youros=" "$SHELL_RC" 2>/dev/null; then
    echo "alias youros='$INSTALL_DIR/start.sh'" >> "$SHELL_RC"
    echo "Added 'youros' command to $SHELL_RC"
fi

echo ""
echo -e "${GREEN}=== YourOS is installed! ===${NC}"
echo ""
echo "To start YourOS:"
echo "  1. Open a new Terminal window (so the 'youros' command is available)"
echo "  2. Type: youros"
echo "  3. Your browser will open automatically"
echo ""
echo "Or run it right now:"
echo "  $INSTALL_DIR/start.sh"
echo ""
