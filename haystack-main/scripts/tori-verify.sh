#!/bin/bash
# TORI-MODE Verification Script (→489)
# Simulates a fresh user experience with zero prior knowledge.

set -e

echo "=== TORI-MODE: Fresh Install Simulation ==="

# 1. Setup fresh temp directory
TMP_DIR=$(mktemp -d)
cd "$TMP_DIR"
echo "Workdir: $TMP_DIR"

# 2. Simulate binary availability (use current debug build)
HAYSTACK_BIN="/Users/scottmeyer/projects/haystack/target/debug/haystack"

# 3. Attempt boot without install (should fail with helpful nudge)
echo "Step 1: Bare boot..."
if "$HAYSTACK_BIN" boot --bail > boot_output.txt 2>&1; then
    echo "FAILED: boot should have failed in fresh dir"
    exit 1
else
    echo "SUCCESS: boot failed as expected."
    grep -i "install" boot_output.txt || (echo "FAILED: no install hint in error"; exit 1)
fi

# 4. Attempt install
echo "Step 2: Install..."
"$HAYSTACK_BIN" install --no-symlinks

# 5. Attempt boot after install (should pass)
echo "Step 3: Boot after install..."
"$HAYSTACK_BIN" boot --bail

# 6. Attempt needle filing via CLI
echo "Step 4: Filing hay..."
"$HAYSTACK_BIN" hay "tori test message"

# 7. Attempt needle compilation
echo "Step 5: Compilation..."
"$HAYSTACK_BIN" compile

# 8. Verify audit integrity
echo "Step 6: Audit check..."
"$HAYSTACK_BIN" audit check

echo "=== TORI-MODE: PASS ==="
rm -rf "$TMP_DIR"
