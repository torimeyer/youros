#!/usr/bin/env bash
# Rule: home_scan_guard (→2479)
# Blocks find/grep/ls commands that scan the home directory root, Documents,
# Desktop, Downloads, or iCloud (~/Library/Mobile Documents) from agent sessions.
# Those paths trigger macOS privacy permission popups for every file touched.
# Called from: pre-tool-guard.sh (Bash|mcp__ostk__bash section)
# Assumes: load-rule.sh, log-fire.sh, deny.sh already sourced by caller.

_home_scan_guard_check() {
    local tool="$1" cmd="$2"

    local VERDICT
    VERDICT=$(HOOK_CMD="$cmd" python3 <<'PY' 2>/dev/null
import os, sys, shlex

cmd = os.environ.get("HOOK_CMD", "")
home = os.path.expanduser("~")

# Blocked scan roots: home itself and its sensitive immediate subdirs.
# Agents only need the project folder, /tmp, and ~/.youros.
BLOCKED = [
    home,
    os.path.join(home, "Documents"),
    os.path.join(home, "Desktop"),
    os.path.join(home, "Downloads"),
    os.path.join(home, "Library", "Mobile Documents"),
]

# Always-allowed prefixes even if they live under home.
ALLOWED_PREFIXES = [
    "/tmp",
    "/private/tmp",
    os.path.join(home, ".youros"),
    os.path.join(home, ".claude"),
    os.path.join(home, "claude"),
]
proj = os.environ.get("CLAUDE_PROJECT_DIR", "")
if proj:
    ALLOWED_PREFIXES.append(os.path.normpath(proj))


def normalize(p):
    p = p.strip().strip("'\"")
    p = os.path.expandvars(p)
    p = os.path.expanduser(p)
    return os.path.normpath(p)


def is_blocked(p):
    norm = normalize(p)
    # Check allowlist first (most specific wins).
    for ap in ALLOWED_PREFIXES:
        anorm = os.path.normpath(ap)
        if norm == anorm or norm.startswith(anorm + os.sep):
            return False, ""
    # Check blocked list.
    for bp in BLOCKED:
        bnorm = os.path.normpath(bp)
        if norm == bnorm or norm.startswith(bnorm + os.sep):
            return True, bp
    return False, ""


def args_after_flags_with_values(rest):
    """Walk tokens; options that consume a value argument are skipped."""
    VALUE_FLAGS = {
        "-maxdepth", "-mindepth", "-name", "-iname", "-path", "-ipath",
        "-newer", "-user", "-group", "-perm", "-size", "-mtime", "-atime",
        "-ctime", "-exec", "-execdir", "-ok", "-type", "-wholename",
    }
    paths = []
    skip = False
    for tok in rest:
        if skip:
            skip = False
            continue
        if tok in VALUE_FLAGS:
            skip = True
            continue
        if tok.startswith("-"):
            continue
        if tok in (";", "+", "{}"):
            continue
        paths.append(tok)
    return paths


try:
    tokens = shlex.split(cmd)
except ValueError:
    tokens = cmd.split()

if not tokens:
    sys.exit(0)

# Skip leading env-var assignments (KEY=value pattern with no slash or tilde).
i = 0
while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith(("/", "~")):
    i += 1

if i >= len(tokens):
    sys.exit(0)

primary = tokens[i]
rest = tokens[i + 1:]

paths_to_check = []

if primary == "find":
    paths_to_check = args_after_flags_with_values(rest)

elif primary in ("grep", "ggrep"):
    has_recursive = any(
        tok in ("-r", "-R", "--recursive")
        or (tok.startswith("-") and not tok.startswith("--") and ("r" in tok or "R" in tok))
        for tok in rest
    )
    if has_recursive:
        positional = [t for t in rest if not t.startswith("-")]
        # First positional is the pattern; the rest are paths.
        if len(positional) >= 2:
            paths_to_check = positional[1:]

elif primary == "ls":
    has_recursive = any(
        tok == "-R"
        or (tok.startswith("-") and not tok.startswith("--") and "R" in tok)
        for tok in rest
    )
    if has_recursive:
        paths_to_check = [t for t in rest if not t.startswith("-")]

for p in paths_to_check:
    if not p:
        continue
    blocked, bp = is_blocked(p)
    if blocked:
        sys.stdout.write(p + "\x1f" + bp)
        sys.exit(0)
PY
    )

    if [ -n "$VERDICT" ]; then
        local SCAN_PATH BLOCKED_ROOT
        IFS=$'\x1f' read -r SCAN_PATH BLOCKED_ROOT <<<"$VERDICT"
        log_rule_fire "home_scan_guard" "$tool" "block" "home scan blocked: $SCAN_PATH"
        deny "Scanning '${SCAN_PATH}' triggers macOS privacy permission popups. Agents only need the project folder (~/.claude/worktrees/ or ~/claude/torios), /tmp, and ~/.youros. Re-run your search inside one of those paths."
    fi
}
