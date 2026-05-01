"""Orphan reference sweep for e2e_smoke.sh.

Scans the smoke script for repo-relative paths and prints any that
don't exist on disk (one per line). Exit 0 always.

Usage: python3 scripts/lib/e2e_orphan_sweep.py <repo_dir> <script_path>
"""
import os, re, subprocess, sys

repo = sys.argv[1]
script = sys.argv[2]
pat = re.compile(
    r'(?:\$REPO_DIR/|\$\{REPO_DIR\}/|(?<=[\s"\x27`(]))'
    r'(scripts/[A-Za-z0-9_.\-/]+\.(?:sh|py)'
    r'|app/[A-Za-z0-9_.\-/]+\.(?:ts|tsx|js|jsx|json|cjs|mjs)'
    r'|api/routers/[A-Za-z0-9_.\-/]+\.py'
    r'|api/\.venv'
    r'|api/routers'
    r'|start\.sh'
    r'|README\.md'
    r')'
)
seen = set()
try:
    with open(script, 'r', encoding='utf-8') as fh:
        text = fh.read()
except OSError:
    sys.exit(0)
for m in pat.finditer(text):
    rel = m.group(1)
    line_start = text.rfind('\n', 0, m.start()) + 1
    line_end = text.find('\n', m.end())
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    stripped = line.lstrip()
    if stripped.startswith('#'):
        continue
    seen.add(rel)
missing = []
for rel in sorted(seen):
    full = os.path.join(repo, rel)
    if not os.path.exists(full):
        r = subprocess.run(
            ['git', 'check-ignore', '--quiet', '--no-index', rel + '/'],
            cwd=repo, capture_output=True
        )
        if r.returncode == 0:
            continue
        missing.append(rel)
if missing:
    for m in missing:
        print(m)
