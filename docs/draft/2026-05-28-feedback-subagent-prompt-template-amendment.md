# Amendment: feedback_subagent_prompt_template.md Rule 6

**Needle**: →1792  
**Date**: 2026-05-28  
**File amended**: `/Users/torimeyer/.claude/projects/-Users-torimeyer-claude-torios/memory/feedback_subagent_prompt_template.md`

## What changed

Rule 6 (pre-file-create check) already existed in the memory file but used a python3 script as the third check step. Updated to use `search(query="<FileName>", scope="code")` per pre-design-audit spec Hook 3.

**Before** (code block in Rule 6):
```bash
git fetch origin main
git log --oneline -10 origin/main | grep -i "<filename-stem>"
python3 ~/.myos/pre-design-audit.py "<FileName>" --repo-root "$(git rev-parse --show-toplevel)"
```

**After** (code block in Rule 6):
```bash
git fetch origin main
git log --oneline -10 origin/main | grep -i "<filename-stem>"
search(query="<FileName>", scope="code")
```

The "If MATCH FOUND" language was also updated to "If either returns a match, STOP" to match the spec, and the include-in-brief snippet was updated accordingly.

## Why

The python3 script approach depended on an external file (`~/.myos/pre-design-audit.py`) that may not exist in all environments. The `search(query=..., scope="code")` form uses the ostk search tool that is always available, and is consistent with how other ostk-aware checks are written.

## Note

The spec file referenced in →1792 (`docs/spec/pre-design-audit-catch-existing-patterns-before-proposing-new-infrastructure.md`) did not exist in the repo at time of amendment. The task brief itself provided the verbatim text to add.
