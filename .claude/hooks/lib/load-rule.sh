#!/usr/bin/env bash
# Rule loader — source this file from any enforcement hook.
#
# Provides three functions:
#   rule_enabled <key>                — returns 0 if rules.<key>.enabled is true, else 1
#   rule_param   <key_path> <default> — prints the resolved value, falls back to <default>
#   myos_rule_get <path> <default>    — internal deep-merge query (used by both above)
#
# Fail-open: on any error (missing files, bad JSON, python crash),
# rule_enabled returns 1 (disabled) and rule_param echoes <default>.
# No caching — reads fresh per call. Correctness > speed.

# Capture the loader's own directory at source time so myos_rule_get can
# always find default-rules.json, regardless of the caller's cwd or
# CLAUDE_PROJECT_DIR.
_LOAD_RULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

myos_rule_get() {
  local path_arg="$1" default="$2"
  local defaults_file="${_LOAD_RULE_DIR}/default-rules.json"
  local user_file="${HOME}/.myos/rules.json"

  python3 - "$path_arg" "$defaults_file" "$user_file" "$default" <<'PYEOF' 2>/dev/null
import json, os, sys

path_arg      = sys.argv[1]
defaults_file = sys.argv[2]
user_file     = sys.argv[3]
default_val   = sys.argv[4] if len(sys.argv) > 4 else ""

def load(p):
    try:
        with open(p) as f:
            return json.load(f)
    except (FileNotFoundError, IsADirectoryError):
        return {}
    except Exception:
        return {}

def deep_merge(a, b):
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out

try:
    cfg = deep_merge(load(defaults_file), load(user_file))
    node = cfg
    for part in path_arg.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            # path not found — print nothing, caller uses default
            sys.exit(0)
    if isinstance(node, (dict, list)):
        print(json.dumps(node))
    else:
        # bool → lowercase string so shell callers can grep -qx true
        print(str(node).lower() if isinstance(node, bool) else node)
except Exception:
    sys.exit(0)
PYEOF
}

rule_enabled() {
  local key="$1"
  local val
  val=$(myos_rule_get "rules.${key}.enabled" "false")
  [ "${val:-false}" = "true" ]
}

rule_param() {
  local key_path="$1" default="$2"
  local val
  val=$(myos_rule_get "rules.${key_path}" "")
  if [ -z "$val" ]; then
    echo "$default"
  else
    echo "$val"
  fi
}
