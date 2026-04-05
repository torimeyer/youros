#!/usr/bin/env bash
# scripts/install.sh — download and install ostk binary
# Usage: curl -fsSL https://ostk.ai/install.sh | bash
#        curl -fsSL https://ostk.ai/install.sh | bash -s -- --version v2.0.0
set -euo pipefail

REPO="os-tack/ostk.ai"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"
VERSION="${1:-latest}"

# ── detect platform ──────────────────────────────────────────────

detect_platform() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"

  case "$os" in
    Linux)  os="unknown-linux-musl" ;;
    Darwin) os="apple-darwin" ;;
    *)      echo "error: unsupported OS: $os" >&2; exit 1 ;;
  esac

  case "$arch" in
    x86_64)          arch="x86_64" ;;
    aarch64|arm64)   arch="aarch64" ;;
    *)               echo "error: unsupported architecture: $arch" >&2; exit 1 ;;
  esac

  echo "${arch}-${os}"
}

PLATFORM="$(detect_platform)"
echo "detected platform: $PLATFORM"

# ── resolve version ──────────────────────────────────────────────

if [ "$VERSION" = "latest" ]; then
  VERSION="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
    | grep '"tag_name"' | head -1 | cut -d'"' -f4)"
  if [ -z "$VERSION" ]; then
    echo "error: could not determine latest release version" >&2
    exit 1
  fi
fi
echo "installing version: $VERSION"

# ── download ─────────────────────────────────────────────────────

TARBALL="ostk-${VERSION}-${PLATFORM}.tar.gz"
SIGFILE="${TARBALL}.asc"
BASE_URL="https://github.com/${REPO}/releases/download/${VERSION}"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "downloading $TARBALL ..."
curl -fSL -o "${TMPDIR}/${TARBALL}" "${BASE_URL}/${TARBALL}"
curl -fSL -o "${TMPDIR}/${SIGFILE}" "${BASE_URL}/${SIGFILE}" 2>/dev/null || true

# ── verify GPG signature ────────────────────────────────────────

verify_signature() {
  if ! command -v gpg >/dev/null 2>&1; then
    echo "warning: gpg not found — skipping signature verification"
    return 0
  fi

  if [ ! -f "${TMPDIR}/${SIGFILE}" ]; then
    echo "warning: no .asc signature file found — skipping verification"
    return 0
  fi

  # Import the ostk verification keyring (T0, T1, CI)
  local keyring
  keyring="$(cd "$(dirname "$0")" && pwd)/../.ostk/keys/ostk-keyring.asc"

  # If running from curl pipe, fetch the keyring from the repo
  if [ ! -f "$keyring" ]; then
    keyring="${TMPDIR}/ostk-keyring.asc"
    echo "fetching verification keyring ..."
    curl -fSL -o "$keyring" \
      "https://raw.githubusercontent.com/${REPO}/main/.ostk/keys/ostk-keyring.asc"
  fi

  echo "importing verification keys ..."
  gpg --batch --import "$keyring" 2>/dev/null || true

  echo "verifying signature ..."
  if gpg --batch --verify "${TMPDIR}/${SIGFILE}" "${TMPDIR}/${TARBALL}" 2>/dev/null; then
    echo "signature verified OK"
  else
    echo "error: GPG signature verification FAILED" >&2
    echo "The binary may have been tampered with. Aborting." >&2
    exit 1
  fi
}

verify_signature

# ── install ──────────────────────────────────────────────────────

echo "extracting ..."
tar -xzf "${TMPDIR}/${TARBALL}" -C "${TMPDIR}"

# Install the ostk binary; create haystack as a compat symlink
if [ -f "${TMPDIR}/ostk" ]; then
  if [ -w "$INSTALL_DIR" ]; then
    install -m 755 "${TMPDIR}/ostk" "${INSTALL_DIR}/ostk"
    ln -sf "${INSTALL_DIR}/ostk" "${INSTALL_DIR}/haystack"
  else
    echo "installing to ${INSTALL_DIR} (requires sudo) ..."
    sudo install -m 755 "${TMPDIR}/ostk" "${INSTALL_DIR}/ostk"
    sudo ln -sf "${INSTALL_DIR}/ostk" "${INSTALL_DIR}/haystack"
  fi
fi

echo ""
echo "ostk ${VERSION} installed to ${INSTALL_DIR}/ostk"
echo "haystack compat symlink at ${INSTALL_DIR}/haystack"
echo ""
echo "Get started:"
echo "  mkdir my-project && cd my-project && git init"
echo "  ostk boot"
