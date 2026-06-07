#!/usr/bin/env bash
# Unit tests for load-rule.sh
# Usage: bash .claude/hooks/lib/load-rule.test.sh
# Exits 0 and prints PASS on success.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOADER="$SCRIPT_DIR/load-rule.sh"
DEFAULTS="$SCRIPT_DIR/default-rules.json"

FAIL_COUNT=0

assert_eq() {
  local label="$1" got="$2" want="$3"
  if [ "$got" = "$want" ]; then
    echo "  OK  $label"
  else
    echo "  FAIL $label: got='$got' want='$want'" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

assert_exit() {
  local label="$1" code="$2" want="$3"
  if [ "$code" = "$want" ]; then
    echo "  OK  $label"
  else
    echo "  FAIL $label: exit=$code want=$want" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

# Set up an isolated HOME for each test so ~/.youros/rules.json doesn't bleed in.
TMPBASE="/tmp/test-rules-$$"
mkdir -p "$TMPBASE"
trap 'rm -rf "$TMPBASE"' EXIT

run_test() {
  local name="$1"
  shift
  echo "--- $name"
  "$@"
}

# -------------------------------------------------------------------------
# Test 1: loader returns default when ~/.youros/rules.json is missing
# -------------------------------------------------------------------------
run_test "default_when_no_user_file" bash -c "
  FAKE_HOME='$TMPBASE/t1'
  mkdir -p \"\$FAKE_HOME\"
  export HOME=\"\$FAKE_HOME\"
  export CLAUDE_PROJECT_DIR='$(dirname "$SCRIPT_DIR")'
  source '$LOADER'
  got=\$(rule_param saa_must_spawn.enabled fallback_default)
  # No user file → value comes from default-rules.json (false)
  case \"\$got\" in
    false) echo '  OK  returns false from defaults' ;;
    *)     echo \"  FAIL expected false, got '\$got'\" >&2; exit 1 ;;
  esac
"

# -------------------------------------------------------------------------
# Test 2: loader returns user value when user file overrides default
# -------------------------------------------------------------------------
run_test "user_override_wins" bash -c "
  FAKE_HOME='$TMPBASE/t2'
  mkdir -p \"\$FAKE_HOME/.youros\"
  cat > \"\$FAKE_HOME/.youros/rules.json\" <<'ENDJSON'
{\"schema_version\":1,\"rules\":{\"saa_must_spawn\":{\"enabled\":true}}}
ENDJSON
  export HOME=\"\$FAKE_HOME\"
  export CLAUDE_PROJECT_DIR='$(dirname "$SCRIPT_DIR")'
  source '$LOADER'
  got=\$(rule_param saa_must_spawn.enabled false)
  case \"\$got\" in
    true) echo '  OK  user override returns true' ;;
    *)    echo \"  FAIL expected true, got '\$got'\" >&2; exit 1 ;;
  esac
"

# -------------------------------------------------------------------------
# Test 3: deep merge — flipping one flag does not lose other rule defaults
# -------------------------------------------------------------------------
run_test "deep_merge_preserves_other_keys" bash -c "
  FAKE_HOME='$TMPBASE/t3'
  mkdir -p \"\$FAKE_HOME/.youros\"
  cat > \"\$FAKE_HOME/.youros/rules.json\" <<'ENDJSON'
{\"schema_version\":1,\"rules\":{\"saa_must_spawn\":{\"enabled\":true}}}
ENDJSON
  export HOME=\"\$FAKE_HOME\"
  export CLAUDE_PROJECT_DIR='$(dirname "$SCRIPT_DIR")'
  source '$LOADER'
  # saa_must_spawn.enabled should be true (overridden)
  got_saa=\$(rule_param saa_must_spawn.enabled false)
  # adhd_monitor_pairing.enabled should still be false (from defaults)
  got_adhd=\$(rule_param adhd_monitor_pairing.enabled true)
  # needle_hygiene.enabled should be true (default in default-rules.json)
  got_needle=\$(rule_param needle_hygiene.enabled false)

  ok=1
  [ \"\$got_saa\" = 'true' ]    || { echo \"  FAIL saa expected true, got '\$got_saa'\" >&2; ok=0; }
  [ \"\$got_adhd\" = 'false' ]  || { echo \"  FAIL adhd expected false, got '\$got_adhd'\" >&2; ok=0; }
  [ \"\$got_needle\" = 'true' ] || { echo \"  FAIL needle expected true, got '\$got_needle'\" >&2; ok=0; }
  if [ \"\$ok\" = '1' ]; then
    echo '  OK  deep merge preserves unrelated keys'
  else
    exit 1
  fi
"

# -------------------------------------------------------------------------
# Test 4: rule_enabled returns 1 for unknown key (fail-open)
# -------------------------------------------------------------------------
run_test "unknown_key_returns_disabled" bash -c "
  FAKE_HOME='$TMPBASE/t4'
  mkdir -p \"\$FAKE_HOME\"
  export HOME=\"\$FAKE_HOME\"
  export CLAUDE_PROJECT_DIR='$(dirname "$SCRIPT_DIR")'
  source '$LOADER'
  if rule_enabled totally_nonexistent_rule_xyz; then
    echo '  FAIL expected exit 1 (disabled), got exit 0' >&2; exit 1
  else
    echo '  OK  unknown key returns disabled (fail-open)'
  fi
"

# -------------------------------------------------------------------------
# Test 5: rule_param returns explicit default when path is missing
# -------------------------------------------------------------------------
run_test "missing_path_returns_default" bash -c "
  FAKE_HOME='$TMPBASE/t5'
  mkdir -p \"\$FAKE_HOME\"
  export HOME=\"\$FAKE_HOME\"
  export CLAUDE_PROJECT_DIR='$(dirname "$SCRIPT_DIR")'
  source '$LOADER'
  got=\$(rule_param nonexistent_rule.some_param MY_DEFAULT)
  case \"\$got\" in
    MY_DEFAULT) echo '  OK  missing path returns caller-supplied default' ;;
    *)          echo \"  FAIL expected MY_DEFAULT, got '\$got'\" >&2; exit 1 ;;
  esac
"

# -------------------------------------------------------------------------
# Test 6: malformed user JSON falls back to defaults, does not error
# -------------------------------------------------------------------------
run_test "malformed_user_json_fallback" bash -c "
  FAKE_HOME='$TMPBASE/t6'
  mkdir -p \"\$FAKE_HOME/.youros\"
  printf 'THIS IS NOT JSON }{{{' > \"\$FAKE_HOME/.youros/rules.json\"
  export HOME=\"\$FAKE_HOME\"
  export CLAUDE_PROJECT_DIR='$(dirname "$SCRIPT_DIR")'
  source '$LOADER'
  # needle_hygiene defaults true in default-rules.json
  if rule_enabled needle_hygiene; then
    echo '  OK  malformed user JSON falls back to defaults'
  else
    echo '  FAIL malformed JSON should fall back to defaults (needle_hygiene=true)' >&2; exit 1
  fi
"

# -------------------------------------------------------------------------
# Test 7: rule_enabled returns 0 for needle_hygiene (defaults true)
# -------------------------------------------------------------------------
run_test "needle_hygiene_defaults_enabled" bash -c "
  FAKE_HOME='$TMPBASE/t7'
  mkdir -p \"\$FAKE_HOME\"
  export HOME=\"\$FAKE_HOME\"
  export CLAUDE_PROJECT_DIR='$(dirname "$SCRIPT_DIR")'
  source '$LOADER'
  if rule_enabled needle_hygiene; then
    echo '  OK  needle_hygiene defaults enabled (true)'
  else
    echo '  FAIL needle_hygiene should default to enabled' >&2; exit 1
  fi
"

# -------------------------------------------------------------------------
# Test 8: rule_enabled returns 1 for saa_must_spawn (defaults false)
# -------------------------------------------------------------------------
run_test "saa_must_spawn_defaults_disabled" bash -c "
  FAKE_HOME='$TMPBASE/t8'
  mkdir -p \"\$FAKE_HOME\"
  export HOME=\"\$FAKE_HOME\"
  export CLAUDE_PROJECT_DIR='$(dirname "$SCRIPT_DIR")'
  source '$LOADER'
  if rule_enabled saa_must_spawn; then
    echo '  FAIL saa_must_spawn should default to disabled' >&2; exit 1
  else
    echo '  OK  saa_must_spawn defaults disabled (false)'
  fi
"

# -------------------------------------------------------------------------
# Test 9: rule_param stash_min_label_chars returns 6 (hardcoded default)
# -------------------------------------------------------------------------
run_test "bash_guards_stash_min_label_chars_default" bash -c "
  FAKE_HOME='$TMPBASE/t9'
  mkdir -p \"\$FAKE_HOME\"
  export HOME=\"\$FAKE_HOME\"
  export CLAUDE_PROJECT_DIR='$(dirname "$SCRIPT_DIR")'
  source '$LOADER'
  got=\$(rule_param bash_guards.stash_min_label_chars 99)
  case \"\$got\" in
    6) echo '  OK  stash_min_label_chars returns 6 from defaults' ;;
    *) echo \"  FAIL expected 6, got '\$got'\" >&2; exit 1 ;;
  esac
"

# -------------------------------------------------------------------------
echo ""
if [ "$FAIL_COUNT" -eq 0 ]; then
  echo "PASS"
  exit 0
else
  echo "FAIL: $FAIL_COUNT test(s) failed"
  exit 1
fi
