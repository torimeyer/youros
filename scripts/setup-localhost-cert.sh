#!/bin/bash
# setup-localhost-cert.sh
#
# Removes the "Not Secure" badge in Chrome for https://localhost:3010 by
# replacing the self-signed cert with one signed by a locally trusted CA.
#
# Two paths:
#   A) mkcert (preferred) - installs a tiny local CA into the macOS Keychain
#      and issues a cert signed by it. Chrome trusts it automatically.
#   B) Security add-trusted-cert (fallback) - directly trusts the existing
#      self-signed cert in the login Keychain. No new binary required.
#
# The script is idempotent: run it again at any time with no side effects.
# The vite config already reads ~/.youros/localhost.crt + localhost.key,
# so no vite changes are needed.
#
# Usage:
#   scripts/setup-localhost-cert.sh           # auto-detects path A or B
#   scripts/setup-localhost-cert.sh --keychain # force path B (no mkcert)
#
# After running, restart the frontend:
#   scripts/dev-frontend.sh
# Then open a NEW Chrome tab (existing tabs keep the old cert in memory).

set -euo pipefail

CERT_DIR="${YOUROS_HOME:-$HOME/.youros}"
CERT="$CERT_DIR/localhost.crt"
KEY="$CERT_DIR/localhost.key"
FORCE_KEYCHAIN="${1:-}"

mkdir -p "$CERT_DIR"

# ── Path A: mkcert ────────────────────────────────────────────────────────────
use_mkcert() {
    echo "==> mkcert found. Installing local CA and generating cert..."

    # Install CA into System Keychain (prompts for admin password once).
    mkcert -install

    # Generate cert for localhost, 127.0.0.1, and ::1.
    # mkcert writes to the current directory, so cd to CERT_DIR first.
    pushd "$CERT_DIR" > /dev/null
    mkcert -cert-file localhost.crt -key-file localhost.key localhost 127.0.0.1 ::1
    popd > /dev/null

    echo ""
    echo "Done. ~/.youros/localhost.crt is now signed by your local mkcert CA."
    echo "Restart the frontend (scripts/dev-frontend.sh) and open a NEW Chrome tab."
}

# ── Path B: security add-trusted-cert (no mkcert) ────────────────────────────
use_keychain_trust() {
    if [ ! -f "$CERT" ]; then
        echo "No cert found at $CERT. Generating a fresh self-signed cert..."
        openssl req -x509 -newkey rsa:2048 -keyout "$KEY" -out "$CERT" \
            -days 3650 -nodes -subj "/CN=localhost" \
            -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1" 2>/dev/null
        echo "Cert created."
    fi

    echo "==> Adding $CERT to the login Keychain with Always Trust..."
    echo "(macOS will prompt for your admin password)"
    security add-trusted-cert \
        -d \
        -r trustRoot \
        -k "$HOME/Library/Keychains/login.keychain-db" \
        "$CERT"

    echo ""
    echo "Done. The cert is now trusted in your login Keychain."
    echo "Restart Chrome fully (Cmd+Q, then reopen) for the change to take effect."
    echo "Then open a NEW tab to https://localhost:3010."
}

# ── Decision ──────────────────────────────────────────────────────────────────
if [ "$FORCE_KEYCHAIN" = "--keychain" ]; then
    use_keychain_trust
elif command -v mkcert &>/dev/null; then
    use_mkcert
else
    echo "mkcert is not installed."
    echo ""
    echo "Option 1 (recommended): install mkcert, then re-run this script."
    echo "  brew install mkcert"
    echo "  scripts/setup-localhost-cert.sh"
    echo ""
    echo "Option 2: trust the existing self-signed cert directly (no new tools)."
    read -r -p "Use option 2 now? [y/N] " answer
    if [[ "$answer" =~ ^[Yy]$ ]]; then
        use_keychain_trust
    else
        echo "Nothing changed. Install mkcert and re-run when ready."
        exit 0
    fi
fi
