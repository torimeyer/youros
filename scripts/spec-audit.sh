#!/usr/bin/env bash
# spec-audit.sh (→1469)
# Walks ~/.myos/specs/*.md and docs/spec/*.md, scores each against the
# 10-section template, and prints a JSON report to stdout.
# Exit 0: report printed (all specs score >= 5).
# Exit 1: at least one spec scores < 5 (or an error occurred).

set -euo pipefail

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
PYTHON="${REPO_ROOT}/api/.venv/bin/python3.11"

if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

PYTHONPATH="${REPO_ROOT}/api" "$PYTHON" - <<PYEOF
import json
import sys
from services.spec_audit import audit_all_specs

report = audit_all_specs()
print(json.dumps(report, indent=2))
low_scores = [s for s in report["specs"] if s["score"] < 5]
sys.exit(1 if low_scores else 0)
PYEOF
