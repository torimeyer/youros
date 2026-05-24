#!/usr/bin/env bash
# post-tool-filter.sh — wraps `ostk hook post-tool` and strips volatile content
# that breaks Anthropic prompt-cache prefix stability.
#
# Removes:
#   1. The [ctx] block (session identity, gen_table list, needles, parallelism age)
#   2. tok_calls:NNNN from [meminfo] (monotonically-increasing counter)
#
# Both fields change every turn, making every tool result unique and preventing
# the rewrite deduplication engine from matching any blocks.

set -euo pipefail

# Run the real hook, capture output
output=$(ostk hook post-tool "$@" 2>/dev/null) || true

if [[ -z "$output" ]]; then
  exit 0
fi

# Strip volatile content via Python (POSIX-portable, handles multiline blocks)
echo "$output" | python3 - <<'PYEOF'
import sys
import re

text = sys.stdin.read()

# 1. Strip the entire [ctx] block.
#    The block starts with a line beginning "[ctx]" and ends at the next blank line
#    (or end of string). It contains session identity, modified gen_table list,
#    needles counts, and parallelism age — all volatile.
text = re.sub(
    r'\[ctx\].*?(?=\n\n|\Z)',
    '',
    text,
    flags=re.DOTALL
)

# 2. Strip tok_calls:NNNN from [meminfo] lines.
#    tok_calls is a monotonically-increasing counter — makes every tool result unique.
#    Replace with tok_calls:0 so the field still exists but is stable.
text = re.sub(r'\btok_calls:\d+\b', 'tok_calls:0', text)

# Clean up any double-blank-lines left by block removal
text = re.sub(r'\n{3,}', '\n\n', text)

sys.stdout.write(text)
PYEOF
