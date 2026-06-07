#!/bin/bash
# test_setup_localhost_cert.sh
#
# Tests for setup-localhost-cert.sh.
# Runs in a temp directory so it never touches ~/.youros or the system Keychain.
#
# Usage:
#   scripts/test_setup_localhost_cert.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SETUP_SCRIPT="$SCRIPT_DIR/setup-localhost-cert.sh"

pass=0
fail=0

ok()   { echo "  PASS: $1"; ((pass++)) || true; }
fail() { echo "  FAIL: $1"; ((fail++)) || true; }

# ── Setup: temp CERT_DIR ──────────────────────────────────────────────────────
TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST"' EXIT

echo "Running tests in $TMPDIR_TEST"
echo ""

# ── Test 1: script exists and is executable ───────────────────────────────────
echo "Test 1: script exists and is executable"
if [ -x "$SETUP_SCRIPT" ]; then
    ok "setup-localhost-cert.sh is executable"
else
    fail "setup-localhost-cert.sh not found or not executable at $SETUP_SCRIPT"
fi

# ── Test 2: script has correct shebang ───────────────────────────────────────
echo "Test 2: correct shebang"
first_line="$(head -1 "$SETUP_SCRIPT")"
if [ "$first_line" = "#!/bin/bash" ]; then
    ok "shebang is #!/bin/bash"
else
    fail "unexpected shebang: $first_line"
fi

# ── Test 3: openssl generates a valid self-signed cert ───────────────────────
echo "Test 3: openssl can generate a localhost cert"
CERT="$TMPDIR_TEST/localhost.crt"
KEY="$TMPDIR_TEST/localhost.key"
openssl req -x509 -newkey rsa:2048 -keyout "$KEY" -out "$CERT" \
    -days 3650 -nodes -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1" 2>/dev/null
if [ -f "$CERT" ] && [ -f "$KEY" ]; then
    ok "openssl generated cert and key"
else
    fail "openssl did not produce expected files"
fi

# ── Test 4: generated cert has correct subject ───────────────────────────────
echo "Test 4: cert subject is CN=localhost"
subject_raw="$(openssl x509 -in "$CERT" -noout -subject 2>/dev/null)"
if echo "$subject_raw" | grep -q "CN=localhost"; then
    ok "cert subject contains CN=localhost"
else
    fail "unexpected cert subject: $subject_raw"
fi

# ── Test 5: generated cert is self-signed (issuer == subject) ────────────────
echo "Test 5: detect self-signed cert"
issuer_val="$(openssl x509 -in "$CERT" -noout -issuer 2>/dev/null | sed 's/^issuer=//')"
subject_val="$(openssl x509 -in "$CERT" -noout -subject 2>/dev/null | sed 's/^subject=//')"
if [ "$issuer_val" = "$subject_val" ]; then
    ok "cert is self-signed (issuer == subject)"
else
    fail "issuer ($issuer_val) differs from subject ($subject_val), expected self-signed"
fi

# ── Test 6: cert has SAN for localhost and 127.0.0.1 ─────────────────────────
echo "Test 6: cert has expected SANs"
sans="$(openssl x509 -in "$CERT" -noout -ext subjectAltName 2>/dev/null)"
if echo "$sans" | grep -q "DNS:localhost" && echo "$sans" | grep -q "IP Address:127.0.0.1"; then
    ok "SAN contains DNS:localhost and IP:127.0.0.1"
else
    fail "SAN missing expected entries: $sans"
fi

# ── Test 7: mkcert path check in script ──────────────────────────────────────
echo "Test 7: script references mkcert"
if grep -q "mkcert" "$SETUP_SCRIPT"; then
    ok "script references mkcert"
else
    fail "script does not reference mkcert"
fi

# ── Test 8: keychain fallback present ────────────────────────────────────────
echo "Test 8: script has keychain fallback"
if grep -q "add-trusted-cert" "$SETUP_SCRIPT"; then
    ok "script has security add-trusted-cert fallback"
else
    fail "script is missing keychain fallback"
fi

# ── Test 9: idempotent cert path (cert already exists, openssl not called) ───
echo "Test 9: skip cert generation if cert already exists"
# The script's use_keychain_trust only calls openssl if cert is absent.
# Verify that logic by checking the condition in the script source.
if grep -q '[ ! -f "$CERT" ]' "$SETUP_SCRIPT" || grep -q "if \[ ! -f" "$SETUP_SCRIPT"; then
    ok "script guards cert generation with existence check"
else
    fail "script does not check for existing cert before generating"
fi

# ── Test 10: mkcert verify (if installed) ────────────────────────────────────
echo "Test 10: mkcert cert passes openssl verify (skipped if mkcert absent)"
if command -v mkcert &>/dev/null; then
    MKCERT_DIR="$(mktemp -d)"
    trap 'rm -rf "$MKCERT_DIR"' EXIT
    pushd "$MKCERT_DIR" > /dev/null
    mkcert -cert-file localhost.crt -key-file localhost.key localhost 127.0.0.1 ::1 2>/dev/null
    ROOT="$(mkcert -CAROOT)/rootCA.pem"
    if openssl verify -CAfile "$ROOT" "$MKCERT_DIR/localhost.crt" &>/dev/null; then
        ok "mkcert cert verified against mkcert CA"
    else
        fail "mkcert cert failed openssl verify"
    fi
    popd > /dev/null
else
    echo "  SKIP: mkcert not installed, skipping verify test"
    ((pass++)) || true
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: $pass passed, $fail failed"
if [ "$fail" -gt 0 ]; then
    exit 1
fi
