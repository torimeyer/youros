#!/bin/bash
# Test the myOS installer in a fresh Ubuntu container.
#
# This script spins up a clean `ubuntu:22.04` container, copies the repo in,
# installs the system packages myOS needs (no Homebrew), and runs ./install.sh.
# It prints PASS or FAIL for each phase so we can tell exactly where Linux
# installs would break for Coulter.
#
# Requires Docker. Run on macOS with Docker Desktop, on Linux with a native
# docker install, or in CI.
#
# Usage:
#   ./scripts/test_install_linux.sh
#   IMAGE=fedora:40 ./scripts/test_install_linux.sh   # override distro

set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${IMAGE:-ubuntu:22.04}"
CONTAINER_NAME="myos-install-test-$$"

if ! command -v docker &> /dev/null; then
    echo -e "${RED}Docker is not installed. This script needs Docker to run.${NC}"
    echo "Install Docker Desktop on Mac, or see https://docs.docker.com/engine/install/ on Linux."
    echo ""
    echo "If you cannot run Docker locally, run this script on a Linux box or in CI."
    exit 2
fi

echo -e "${BLUE}=== myOS Linux install smoke test ===${NC}"
echo "Image: $IMAGE"
echo "Repo:  $REPO_DIR"
echo ""

cleanup() {
    docker rm -f "$CONTAINER_NAME" &> /dev/null || true
}
trap cleanup EXIT

# Start a long-lived container so we can run multiple commands in it.
docker run -d --name "$CONTAINER_NAME" \
    -v "$REPO_DIR":/workspace/myos:ro \
    "$IMAGE" \
    sleep 3600 > /dev/null

run_in_container() {
    docker exec "$CONTAINER_NAME" bash -c "$1"
}

phase() {
    local name="$1"
    local cmd="$2"
    printf "  %-40s" "$name..."
    if run_in_container "$cmd" > /tmp/myos_phase.log 2>&1; then
        echo -e "${GREEN}PASS${NC}"
        return 0
    else
        echo -e "${RED}FAIL${NC}"
        echo "  --- last 20 lines of output ---"
        tail -20 /tmp/myos_phase.log | sed 's/^/    /'
        echo "  -------------------------------"
        return 1
    fi
}

FAILED=0

# Decide which package manager line to use based on the image.
case "$IMAGE" in
    ubuntu*|debian*)
        INSTALL_DEPS="apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl ca-certificates python3 python3-venv python3-pip nodejs npm"
        ;;
    fedora*)
        INSTALL_DEPS="dnf install -y -q git curl ca-certificates python3 python3-pip nodejs npm tar"
        ;;
    archlinux*)
        INSTALL_DEPS="pacman -Sy --noconfirm git curl ca-certificates python python-pip nodejs npm tar"
        ;;
    *)
        echo -e "${YELLOW}Unknown image $IMAGE, trying apt.${NC}"
        INSTALL_DEPS="apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl ca-certificates python3 python3-venv python3-pip nodejs npm"
        ;;
esac

phase "system deps installed" "$INSTALL_DEPS" || FAILED=$((FAILED+1))

# The repo is mounted read only. Copy it so install.sh can write into it.
phase "copy repo into writable dir" "cp -r /workspace/myos /root/myos && chmod +x /root/myos/install.sh" || FAILED=$((FAILED+1))

phase "python3 available" "python3 --version" || FAILED=$((FAILED+1))
phase "node available" "node --version" || FAILED=$((FAILED+1))
phase "npm available" "npm --version" || FAILED=$((FAILED+1))

phase "python venv created" "cd /root/myos/api && python3 -m venv .venv && . .venv/bin/activate && pip install --quiet --upgrade pip" || FAILED=$((FAILED+1))

phase "api requirements installed" "cd /root/myos/api && . .venv/bin/activate && pip install --quiet -r requirements.txt" || FAILED=$((FAILED+1))

phase "frontend deps installed" "cd /root/myos/app && npm install --silent" || FAILED=$((FAILED+1))

phase "frontend builds" "cd /root/myos/app && npm run build" || FAILED=$((FAILED+1))

# Do not actually install the Claude CLI in the test, but verify that the
# install command is available and the package exists.
phase "claude cli install command exists" "command -v npm && npm view @anthropic-ai/claude-code version > /dev/null" || FAILED=$((FAILED+1))

phase "install.sh parses as bash" "bash -n /root/myos/install.sh" || FAILED=$((FAILED+1))

echo ""
if [ "$FAILED" -eq 0 ]; then
    echo -e "${GREEN}All phases passed.${NC} myOS installs cleanly on $IMAGE without Homebrew."
    exit 0
else
    echo -e "${RED}$FAILED phase(s) failed.${NC}"
    exit 1
fi
