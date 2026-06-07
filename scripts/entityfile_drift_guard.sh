#!/usr/bin/env bash
# ENTITYFILE drift guard (→908).
#
# Refuses a commit that stages `.ostk/ENTITYFILE` without also updating the
# matching signature envelope (`.ostk/ENTITYFILE.oae.json` or
# `.ostk/ENTITYFILE.sigstore`). Verifies the envelope actually covers the
# staged content so a stale signature cannot sneak through.
#
# Usage:
#   scripts/entityfile_drift_guard.sh              # check against git index
#   scripts/entityfile_drift_guard.sh --path PATH  # check a file directly
#
# Exit codes:
#   0  no drift (or ENTITYFILE not touched)
#   1  drift detected (commit must be blocked)
#   2  internal error

set -u

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENTITY="${REPO_DIR}/.ostk/ENTITYFILE"
OAE="${REPO_DIR}/.ostk/ENTITYFILE.oae.json"
SIGSTORE="${REPO_DIR}/.ostk/ENTITYFILE.sigstore"

MODE="git"
TARGET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --path)
      MODE="path"; TARGET="$2"; shift 2 ;;
    *)
      echo "usage: $0 [--path FILE]" >&2; exit 2 ;;
  esac
done

fail() {
  cat >&2 <<EOF
[entityfile-drift-guard] REFUSING: .ostk/ENTITYFILE is out of sync with its signature.

  reason: $1

  What happened: the identity file changed but the matching cryptographic
  attestation was not regenerated. ostk boot will refuse to start.

  Fix: re-sign before committing.
    ostk repair entityfile --i-accept-tofu   # local key path (no browser)
    # or, if you have a cosign key, regenerate .ostk/ENTITYFILE.sigstore

  Then re-stage ENTITYFILE + its envelope together and retry the commit.
  Emergency bypass: YOUROS_SKIP_HOOK=1 git commit ...  (discouraged)
EOF
  exit 1
}

# In git mode, only act when ENTITYFILE is actually staged.
if [ "${MODE}" = "git" ]; then
  if ! git -C "${REPO_DIR}" diff --cached --name-only | grep -qx ".ostk/ENTITYFILE"; then
    exit 0
  fi
  # Write the staged blob to a temp file so we verify what WILL land,
  # not what's currently on disk.
  TMP="$(mktemp -t entityfile.staged.XXXXXX)"
  trap 'rm -f "${TMP}"' EXIT
  git -C "${REPO_DIR}" show ":.ostk/ENTITYFILE" > "${TMP}" 2>/dev/null \
    || fail "could not read staged ENTITYFILE blob"
  TARGET="${TMP}"
else
  [ -f "${TARGET}" ] || fail "target file does not exist: ${TARGET}"
fi

# Check we have at least one envelope.
if [ ! -f "${OAE}" ] && [ ! -f "${SIGSTORE}" ]; then
  fail "no signature envelope found (.ostk/ENTITYFILE.oae.json or .sigstore)"
fi

# Primary invariant: the OAE envelope's hashed payload must match the
# current ENTITYFILE bytes. This mirrors what `ostk boot` enforces and
# catches the exact failure in →908 (ENTITYFILE edited after signing).
TARGET_SHA="$(shasum -a 256 "${TARGET}" | awk '{print $1}')"
[ -n "${TARGET_SHA}" ] || fail "could not hash target"

if [ -f "${OAE}" ]; then
  # OAE envelope is a DSSE-ish JSON with base64-encoded in-toto payload.
  # Decode and pull subject[0].digest.sha256 and compare to target hash.
  ENV_SHA="$(python3 - "${OAE}" <<'PY' 2>/dev/null
import sys, json, base64
try:
    d = json.load(open(sys.argv[1]))
    p = json.loads(base64.b64decode(d["payload"]))
    print(p["subject"][0]["digest"]["sha256"])
except Exception:
    sys.exit(1)
PY
)"
  if [ -z "${ENV_SHA}" ]; then
    fail "could not parse OAE envelope payload"
  fi
  if [ "${ENV_SHA}" != "${TARGET_SHA}" ]; then
    fail "OAE envelope digest (${ENV_SHA}) does not match ENTITYFILE (${TARGET_SHA})"
  fi
  exit 0
fi

# Sigstore fallback: require cosign verify-blob to pass. We do not require
# this path in CI (no key present); we only enforce if sigstore is the only
# envelope on disk.
if [ -f "${SIGSTORE}" ]; then
  if ! command -v cosign >/dev/null 2>&1; then
    fail "cosign not installed but .sigstore is the only envelope; cannot verify"
  fi
  if ! cosign verify-blob --bundle "${SIGSTORE}" "${TARGET}" >/dev/null 2>&1; then
    fail "cosign verify-blob failed against .ostk/ENTITYFILE.sigstore"
  fi
fi

exit 0
