---
name: pre-design-audit
description: Run before proposing any new file, component, or service. Checks three signals (codebase, git log, needles/specs) and produces a clearance report. MATCH FOUND blocks design until reuse or justification is written.
---

# Pre-design audit

Run this before creating any new file, component, or service. Catches things that already exist before the design conversation begins.

## When to invoke

- Any time brainstorming or planning proposes a **new** file, component, service, or feature by name
- Before listing files under `Create:` in a writing-plans file structure section
- Before a subagent writes a new file to disk

## The three-signal check

Run the audit script for each proposed concept name:

```bash
python3 ~/.myos/pre-design-audit.py "<ConceptName>" --repo-root "$(git rev-parse --show-toplevel)"
```

The script checks:
1. **Codebase** — filename glob + grep in `app/src/`, `frontend/src/`, `api/` (excluding test files)
2. **Git log** — last 30 commits on `origin/main` for the concept name
3. **Needles/Specs** — `docs/spec/` and `~/.myos/specs/` for any mention

## Clearance report format

The script outputs a table like this:

```
## Pre-design audit: `ClaimSourceChip`

| Signal | Finding |
|--------|---------|
| Codebase | app/src/components/ClaimSourceChip.tsx — EXISTS |
| Git log  | a4faaca feat(→1630): render claim-source attribution chip on spec rows |
| Needles/Specs | docs/spec/spec-auto-status.md |

MATCH FOUND. You must choose before proceeding:
- [ ] Reuse the existing component (state which file and how)
- [ ] Justify why a new implementation is needed (state the gap)
```

If no signals fire: `CLEAR — no existing equivalent found. Proceed with design.`

## Hard gate (MANDATORY)

**If the report says MATCH FOUND:**
- Do NOT proceed to clarifying questions, file-structure decisions, or code
- The checkbox (reuse or justify) must be resolved in writing FIRST
- Proposing a new file when a match exists without written justification = plan failure

**If the report says CLEAR:**
- Continue the normal workflow (brainstorming, writing-plans, implementation)

## Quick check (when audit script isn't available)

If the script can't be run (no shell access, offline), perform the check manually:

```
search(query="<ComponentName>", scope="code")
git fetch origin main && git log --oneline -30 origin/main | grep -i "<concept>"
grep -rl "<concept>" docs/spec/
```

Produce the same clearance table from the results.

## Integration points

This skill is invoked by:
1. **Brainstorming** — at Step 1 ("Explore project context"), before asking clarifying questions
2. **Writing-plans** — before listing any file as `Create:` in the file structure section
3. **Subagent briefs** — before writing any new file to disk (per `feedback_subagent_prompt_template.md` Rule 6)
