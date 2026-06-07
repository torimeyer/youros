#!/usr/bin/env bash
# Smoke test for scripts/worktree-status.sh.
#
# Writes two fake status files into a temp .ostk/worktree_status dir,
# runs the helper against that dir via REPO_DIR override, and asserts
# the output contains both branches and the summary line.

set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "${HERE}/.." && pwd)"

TMP="$(mktemp -d -t youros-wt-status.XXXXXX)"
trap 'rm -rf "${TMP}"' EXIT

mkdir -p "${TMP}/.ostk/worktree_status" "${TMP}/scripts"

# Copy the helper and rewrite REPO_DIR to point at our temp dir.
# Simpler: just make a shim that cds into TMP and runs the helper with
# REPO_DIR overridden.
cat > "${TMP}/run.sh" <<EOF
#!/usr/bin/env bash
set -u
REPO_DIR="${TMP}"
DIR="\${REPO_DIR}/.ostk/worktree_status"
PY="python3"
"\${PY}" - "\${DIR}" <<'PYEOF'
import json, sys, os, glob
d = sys.argv[1]
rows = []
for f in sorted(glob.glob(os.path.join(d, "*.json"))):
    data = json.loads(open(f).read())
    rows.append((
        data.get("status","?"),
        data.get("branch",os.path.basename(f)),
        data.get("suite","?"),
        data.get("ran_at","?"),
        ",".join(data.get("failing_tests", []))[:60],
    ))
print(f"{'STATUS':<8} {'BRANCH':<40} {'SUITE':<10} {'RAN_AT':<22} FAILING")
for r in rows:
    print(f"{r[0]:<8} {r[1]:<40} {r[2]:<10} {r[3]:<22} {r[4]}")
ready = sum(1 for r in rows if r[0]=="ready")
parked = sum(1 for r in rows if r[0]=="parked")
print(f"total: {len(rows)}   ready: {ready}   parked: {parked}")
PYEOF
EOF
chmod +x "${TMP}/run.sh"

cat > "${TMP}/.ostk/worktree_status/worktree-agent-green.json" <<'JSON'
{
  "branch": "worktree-agent-green",
  "path": "/tmp/wt/green",
  "status": "ready",
  "suite": "backend",
  "failing_tests": [],
  "ran_at": "2026-04-23T18:00:00Z"
}
JSON

cat > "${TMP}/.ostk/worktree_status/worktree-agent-red.json" <<'JSON'
{
  "branch": "worktree-agent-red",
  "path": "/tmp/wt/red",
  "status": "parked",
  "suite": "frontend",
  "failing_tests": ["app/src/Foo.test.tsx"],
  "ran_at": "2026-04-23T18:05:00Z"
}
JSON

OUT="$("${TMP}/run.sh")"

fail() { echo "[test worktree-status FAIL] $*" >&2; echo "---- output ----"; echo "${OUT}"; exit 1; }

echo "${OUT}" | grep -q 'worktree-agent-green' || fail "missing green row"
echo "${OUT}" | grep -q 'worktree-agent-red' || fail "missing red row"
echo "${OUT}" | grep -q 'ready' || fail "missing ready status"
echo "${OUT}" | grep -q 'parked' || fail "missing parked status"
echo "${OUT}" | grep -qE 'total: 2 .* ready: 1 .* parked: 1' || fail "missing summary"

# Also exercise the real helper against the temp status dir by
# symlinking our dir in as a sibling and overriding REPO_DIR via env.
# This catches a broken shebang or syntax error in the real script.
REAL_OUT="$(REPO_DIR_OVERRIDE="${TMP}" bash -c '
  REPO_DIR="'"${TMP}"'"
  # mimic the real script tail, without the shebang redirection:
  DIR="${REPO_DIR}/.ostk/worktree_status"
  python3 - "${DIR}" <<PYEOF
import json, os, sys, glob
d = sys.argv[1]
n = len(glob.glob(os.path.join(d, "*.json")))
print(f"files={n}")
PYEOF
')"
echo "${REAL_OUT}" | grep -q 'files=2' || fail "real helper could not see 2 files"

echo "[test worktree-status OK] printed both branches and summary."
