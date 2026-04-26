#!/bin/bash
# Pre-release conventions check
# Run: ./scripts/test_release_conventions.sh
#
# Catches the class of bugs that block releases:
#   1. Version string mismatches (tag vs tarball filename vs URL path).
#   2. Missing or mis-named release notes.
#   3. CHANGELOG / What's New entry missing for today's release.
#   4. Live ostk tarball URLs that 404.
#   5. Hardcoded personal paths that slipped into shipped code.
#   6. Public API fields removed in code but still referenced in the frontend.
#
# Runs BEFORE a release tag is created. Fast. No pytest, no agents.

set -e

PASS=0
FAIL=0
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

assert() {
    local name="$1"
    local result="$2"
    if [ "$result" -eq 0 ]; then
        echo -e "  ${GREEN}PASS${NC}  $name"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}FAIL${NC}  $name"
        FAIL=$((FAIL + 1))
    fi
}

warn() {
    local msg="$1"
    echo -e "  ${YELLOW}WARN${NC}  $msg"
}

DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo ""
echo "=== release conventions check ==="
echo ""

# --- 1. Version string consistency in install.sh ---
# install.sh pulls a tag like "v4.0.0" from the GitHub API and strips the
# leading "v" before building the tarball filename. Verify that pattern is
# still intact. If someone "simplifies" it back to using $VERSION directly,
# every user install breaks.

echo "--- Version string consistency ---"

if grep -qE 'VERSION_NUMBER="\$\{VERSION#v\}"' "$DIR/install.sh"; then
    assert "install.sh strips leading v from GitHub tag for filename" 0
else
    assert "install.sh strips leading v from GitHub tag for filename" 1
fi

# The tarball filename must use the stripped VERSION_NUMBER, not the raw tag.
if grep -qE 'TARBALL="ostk-\$\{VERSION_NUMBER\}' "$DIR/install.sh"; then
    assert "install.sh tarball filename uses VERSION_NUMBER (no v)" 0
else
    assert "install.sh tarball filename uses VERSION_NUMBER (no v)" 1
fi

# The URL path to GitHub must use the raw tag VERSION (with v), not stripped.
if grep -qE 'releases/download/\$\{VERSION\}/' "$DIR/install.sh"; then
    assert "install.sh URL path uses raw tag VERSION (with v)" 0
else
    assert "install.sh URL path uses raw tag VERSION (with v)" 1
fi

# Regression guard: if anyone ever builds the tarball name straight from
# ${VERSION}, the install would 404 on every user. Hard fail.
if grep -qE 'TARBALL="ostk-\$\{VERSION\}-' "$DIR/install.sh"; then
    assert "install.sh does NOT build tarball name from raw tag (v-prefix bug)" 1
else
    assert "install.sh does NOT build tarball name from raw tag (v-prefix bug)" 0
fi

echo ""

# --- 2. Release notes filename convention ---
# Every file under docs/releases/ must be vN.N.N.md (semver). Malformed
# names (v3.5, v3.5.0-beta.md without the strict form) get flagged.

echo "--- Release notes filenames ---"

if [ -d "$DIR/docs/releases" ]; then
    BAD_NAMES=0
    for f in "$DIR/docs/releases"/*.md; do
        [ -e "$f" ] || continue
        base=$(basename "$f" .md)
        if ! echo "$base" | grep -qE '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
            warn "release note filename is not vN.N.N.md: $base"
            BAD_NAMES=$((BAD_NAMES + 1))
        fi
    done
    if [ "$BAD_NAMES" -eq 0 ]; then
        assert "every docs/releases/*.md matches vN.N.N.md" 0
    else
        assert "every docs/releases/*.md matches vN.N.N.md ($BAD_NAMES bad)" 1
    fi
else
    warn "docs/releases/ directory does not exist, skipping filename check"
fi

echo ""

# --- 3. Latest git tag has matching release notes ---

echo "--- Latest tag has matching release notes ---"

if LATEST_TAG=$(git -C "$DIR" describe --tags --abbrev=0 2>/dev/null); then
    :
else
    LATEST_TAG=""
fi
if [ -z "$LATEST_TAG" ]; then
    warn "no git tags found, skipping release notes match"
else
    # Only enforce for strict semver tags (skip pre-dry-run / old short tags).
    if echo "$LATEST_TAG" | grep -qE '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
        if [ -f "$DIR/docs/releases/${LATEST_TAG}.md" ]; then
            assert "docs/releases/${LATEST_TAG}.md exists for latest tag" 0
        else
            assert "docs/releases/${LATEST_TAG}.md exists for latest tag" 1
        fi
    else
        warn "latest tag ${LATEST_TAG} is not strict semver, skipping"
    fi
fi

echo ""

# --- 4. What's New entry in app for latest release date ---
# releaseNotes.ts drives the in-app What's New modal. If a release ships
# today, the top entry should be dated today (or very recent, <= 7 days).

echo "--- What's New entry ---"

RELEASE_NOTES_FILE="$DIR/app/src/data/releaseNotes.ts"
if [ -f "$RELEASE_NOTES_FILE" ]; then
    # Pull the first date: '...' line, which is the top (newest) entry.
    TOP_DATE=$(grep -m1 -oE "date: '[0-9]{4}-[0-9]{2}-[0-9]{2}'" "$RELEASE_NOTES_FILE" | head -1 | cut -d"'" -f2)
    if [ -n "$TOP_DATE" ]; then
        # How many days between today and the top entry.
        # Works on macOS and Linux via python, which we already require.
        DAYS_OLD=$(python3 -c "
from datetime import date
d = date.fromisoformat('$TOP_DATE')
print((date.today() - d).days)
" 2>/dev/null || echo "999")
        if [ "$DAYS_OLD" -ge 0 ] && [ "$DAYS_OLD" -le 14 ]; then
            assert "releaseNotes.ts top entry is within 14 days ($TOP_DATE)" 0
        else
            warn "releaseNotes.ts top entry dated $TOP_DATE ($DAYS_OLD days old)"
            assert "releaseNotes.ts top entry is within 14 days ($TOP_DATE, $DAYS_OLD days)" 1
        fi
    else
        assert "releaseNotes.ts has at least one dated entry" 1
    fi
else
    warn "app/src/data/releaseNotes.ts not found, skipping What's New check"
fi

echo ""

# --- 5. Live ostk tarball URLs exist (HTTP 200) ---
# This is the check that would have caught tonight's bug. For the version
# install.sh will request, verify every platform tarball is actually live.

echo "--- Live ostk tarball URLs ---"

OSTK_REPO="os-tack/ostk.ai"
VERSION=$(curl --connect-timeout 3 -m 5 -fsSL "https://api.github.com/repos/${OSTK_REPO}/releases/latest" 2>/dev/null \
    | grep '"tag_name"' | head -1 | cut -d'"' -f4)

if [ -z "$VERSION" ]; then
    warn "could not fetch latest ostk version (offline?), skipping URL checks"
else
    VERSION_NUMBER="${VERSION#v}"
    echo "  checking version: ${VERSION} (filename uses ${VERSION_NUMBER})"

    for PLATFORM in "darwin-universal" "x86_64-unknown-linux-musl"; do
        TARBALL="ostk-${VERSION_NUMBER}-${PLATFORM}.tar.gz"
        URL="https://github.com/${OSTK_REPO}/releases/download/${VERSION}/${TARBALL}"
        HTTP_CODE=$(curl --connect-timeout 3 -m 10 -fsSL -o /dev/null -w "%{http_code}" "$URL" 2>/dev/null || echo "000")
        if [ "$HTTP_CODE" = "200" ]; then
            assert "ostk ${PLATFORM} tarball HTTP 200" 0
        else
            assert "ostk ${PLATFORM} tarball HTTP ${HTTP_CODE} (expected 200)" 1
        fi
    done
fi

echo ""

# --- 6. No hardcoded personal paths in shipped code ---
# Tests are fine. Production code and the frontend are not. The intentional
# github.com/torimeyer/myos URL in install.sh is a clone target, not a path.
#
# We scan ONLY files that git is tracking, so we skip .venv/, .ostk/,
# .coverage, node_modules, and other runtime state.

echo "--- No hardcoded personal paths ---"

# Git-tracked api/ source files (python/shell/yaml/toml/json), minus tests.
API_LEAKS=$(git -C "$DIR" ls-files 'api/' 2>/dev/null \
    | grep -vE '(^|/)tests?/|(^|/)test_|_test\.py$|\.test\.' \
    | while read -r f; do
        if [ -f "$DIR/$f" ]; then
            grep -HnE '/Users/torimeyer' "$DIR/$f" 2>/dev/null || true
        fi
    done)

if [ -z "$API_LEAKS" ]; then
    assert "api/ has no hardcoded /Users/torimeyer paths (outside tests)" 0
else
    echo "$API_LEAKS" | head -5 | sed 's/^/    /'
    assert "api/ has no hardcoded /Users/torimeyer paths (outside tests)" 1
fi

# Git-tracked app/src/ source files, minus test files.
APP_LEAKS=$(git -C "$DIR" ls-files 'app/src/' 2>/dev/null \
    | grep -vE '\.test\.|__tests__|\.spec\.' \
    | while read -r f; do
        if [ -f "$DIR/$f" ]; then
            grep -HnE '/Users/torimeyer' "$DIR/$f" 2>/dev/null || true
        fi
    done)

if [ -z "$APP_LEAKS" ]; then
    assert "app/src/ has no hardcoded /Users/torimeyer paths (outside tests)" 0
else
    echo "$APP_LEAKS" | head -5 | sed 's/^/    /'
    assert "app/src/ has no hardcoded /Users/torimeyer paths (outside tests)" 1
fi

echo ""

# --- 7. Deprecated API fields ---
# When the backend removes a public response field, the frontend must stop
# reading it. This check looks at a small allowlist of recently removed
# fields. Extend this list whenever you deprecate a field.
#
# Format: "fieldName" -> fails if frontend grep finds it.

echo "--- Deprecated API field references ---"

# Fields that should no longer appear in app/src/ as response keys. Empty
# list today. Example entry: "legacyStatus".
DEPRECATED_FIELDS=()

if [ ${#DEPRECATED_FIELDS[@]} -eq 0 ]; then
    echo "  (no deprecated fields tracked)"
else
    for FIELD in "${DEPRECATED_FIELDS[@]}"; do
        HITS=$(grep -rE "\\b${FIELD}\\b" "$DIR/app/src" 2>/dev/null \
            | grep -v '\.test\.' \
            | grep -v '__tests__' \
            || true)
        if [ -z "$HITS" ]; then
            assert "app/src/ no longer references deprecated field: $FIELD" 0
        else
            echo "$HITS" | head -3 | sed 's/^/    /'
            assert "app/src/ no longer references deprecated field: $FIELD" 1
        fi
    done
fi

echo ""

# --- Summary ---

TOTAL=$((PASS + FAIL))
if [ "$FAIL" -eq 0 ]; then
    echo -e "${GREEN}All $TOTAL release-convention checks passed.${NC}"
else
    echo -e "${RED}$FAIL of $TOTAL release-convention checks failed.${NC}"
    exit 1
fi
echo ""
