#!/usr/bin/env bash
# Test the tori() ostk auto-update logic against the real v4.0.0 tarball.
# Validates that the extract + find-binary + sign path produces a runnable
# ostk that reports the expected version.
#
# Rationale: v4.0.0 renamed the tarball entry from 'ostk' to 'ostk-macos'.
# The old `cp /tmp/ostk ...` step silently failed. This test guards the
# tarball-layout-agnostic replacement in ~/.zshrc.

set -euo pipefail

VERSION="4.0.0"
TARBALL="/tmp/ostk-probe.tar.gz"
URL="https://github.com/os-tack/ostk.ai/releases/download/v${VERSION}/ostk-${VERSION}-darwin-universal.tar.gz"

# Download if not already cached.
if [[ ! -s "$TARBALL" ]]; then
  echo "  downloading $URL"
  curl -fsL --connect-timeout 3 -m 20 "$URL" -o "$TARBALL"
fi

SCRATCH=$(mktemp -d)
TARGET_DIR=$(mktemp -d)
TARGET="$TARGET_DIR/ostk"

cleanup() {
  rm -rf "$SCRATCH" "$TARGET_DIR"
}
trap cleanup EXIT

# Mirror the fixed zshrc logic.
tar -xzf "$TARBALL" -C "$SCRATCH"
BIN=$(find "$SCRATCH" -maxdepth 2 -type f -perm -u+x | head -1)

if [[ -z "$BIN" ]]; then
  echo "FAIL: no executable found in tarball"
  exit 1
fi

echo "  found binary: $(basename "$BIN")"
cp "$BIN" "$TARGET"
chmod +x "$TARGET"
xattr -c "$TARGET" 2>/dev/null || true
codesign --force -s - "$TARGET" >/dev/null 2>&1

# Verify.
OUT=$("$TARGET" --version 2>&1)
echo "  version output: $OUT"

if [[ "$OUT" != *"$VERSION"* ]]; then
  echo "FAIL: expected version $VERSION, got: $OUT"
  exit 1
fi

echo "PASS: tori() ostk update logic works on v${VERSION} tarball"
