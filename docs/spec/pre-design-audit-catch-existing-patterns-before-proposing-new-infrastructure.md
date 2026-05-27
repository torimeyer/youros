---
title: 'Pre-design audit: catch existing patterns before proposing new infrastructure'
created_at: 2026-05-24T03:17:14Z
status: building
needle: →1663
promoted_at: 2026-05-24T03:18:12Z
tasks:
  - "1715"
  - "1716"
  - "1717"
  - "1718"
  - "1719"
  - "1720"
  - "1721"
  - "1722"
  - "1723"
---

## Problem

Agents proposing new files, components, or features without checking whether an equivalent already exists on `main`. Two confirmed incidents:

1. An agent working needle →1662 in a worktree created `app/src/components/ClaimSourceChip.tsx` (gen 1 / initial). The file had already been committed on `main` in a4faaca (2026-05-22, →1630).
2. The same agent re-proposed FR-013 and FR-014 work that was already shipped in commits on `main`.

The result is wasted implementation effort and spec drift (the plan describes something that already exists).

## Root cause

Three compounding gaps in the current planning workflow:

**Gap 1: "Explore project context" is unstructured.**
The brainstorming skill's Step 1 says "check files, docs, recent commits" but provides no concrete search mandate. Agents interpret this loosely and move on to clarifying questions without searching for the specific names being proposed.

**Gap 2: Worktree isolation hides recent merges.**
When a worktree is branched before a relevant commit lands on `main`, the component doesn't appear in the worktree's file tree at all. Without an explicit `git log origin/main` check, the agent has no reason to suspect the thing it's about to build already exists.

**Gap 3: No cross-reference between proposed features and the needle/spec ledger.**
Agents know the needle they're working but don't check whether the work they're about to propose overlaps with other recently-closed needles or promoted specs.

## Proposed solution: pre-design audit

A structured audit that runs before any new infrastructure is proposed. It extracts the concepts from the request, searches four places, and produces a clearance report the planner must act on before proceeding.

### What the audit checks

**1. Codebase search (literal)**: for each proposed component/file/service name:
```
search(query="<ComponentName>", scope="code")
```
Flag any match in `app/src/`, `frontend/src/`, or `api/`.

**2. Codebase search (semantic)**: splits the concept name into camelCase tokens, expands with synonyms, and searches filenames for same-purpose components with different names.

**3. Recent git log on `main`**: scan the last 30 commits on `origin/main`:
```
git fetch origin main
git log --oneline -30 origin/main
```
Flag any commit subject mentioning the proposed concept.

**4. Needle and spec ledger**: search open and recently-closed needles plus promoted specs:
```
ostk work list --status open
ostk work list --status closed --limit 20
ls docs/spec/
grep -l "<concept>" docs/spec/*.md
```
Flag any needle or spec that references the proposed thing.

### Clearance report format

The audit produces a short table before any design work starts. The script outputs a table like this:

```
## Pre-design audit: `SourceBadge`

| Signal | Finding |
|--------|---------|
| Codebase (literal) | none |
| Codebase (semantic) | app/src/components/ClaimSourceChip.tsx [claim, chip, source] |
| Git log  | none |
| Needles/Specs | none |

POSSIBLE MATCH: a different-named component may serve the same purpose.
Review the semantic hits above and choose before proceeding:
- [ ] Reuse the existing component (state which file and how)
- [ ] Confirm these are genuinely different (state the distinction)
```

Three possible outcomes:

- **`MATCH FOUND`**: a literal match exists. Hard block. Reuse/justify checkbox must be resolved before any design work.
- **`POSSIBLE MATCH`**: only semantic hits exist. Soft block. Each semantic hit must be reviewed and resolved in writing before file-structure decisions or code.
- **`CLEAR: no existing equivalent found`**: no signals fired. Continue the normal workflow.

### Hard gate

**If `MATCH FOUND`**: the planner cannot advance to clarifying questions or file-structure decisions until the reuse/justify checkbox is resolved. Proposing a new file when a literal match exists without written justification is a plan failure, equivalent to skipping TDD.

**If `POSSIBLE MATCH`**: the planner cannot advance to file-structure decisions or code until each semantic hit is reviewed. The distinction (same purpose vs. genuinely different) must be written down before proceeding.

## Where it hooks in

Three touch points, each catching a different stage of the pipeline:

### Hook 1: brainstorming skill (primary)

Replace Step 1 of the brainstorming SKILL.md checklist:

**Before:**
> 1. Explore project context, check files, docs, recent commits

**After:**
> 1. Run pre-design audit: extract component/service/feature names from the request. Run the four-signal check (codebase literal, codebase semantic, git log, needles/specs). Produce the clearance report. Resolve any MATCH FOUND or POSSIBLE MATCH before asking clarifying questions.

This is the most important hook because it intercepts proposals at the earliest possible point, before the design conversation even starts.

### Hook 2: writing-plans skill (secondary)

Add to the "File Structure" section of writing-plans SKILL.md, before the agent lists files to create:

> Before listing files as `Create:`, search for each proposed file name in the codebase and in `git log --oneline -30 origin/main`. Files that already exist on `main` must be listed as `Existing (reuse):` not `Create:`. If a file is listed as `Create:` and it matches an existing file, the plan is invalid.

This catches duplicates that slip through brainstorming and get locked into the plan's file structure.

### Hook 3: subagent brief template (tertiary)

Add to `feedback_subagent_prompt_template.md` (the standing instruction for all subagent spawns):

> Before creating any new file, run:
> ```
> git fetch origin main
> git log --oneline -10 origin/main | grep -i "<filename-stem>"
> search(query="<FileName>", scope="code")
> ```
> If either returns a match, stop. Use the existing file rather than creating a new one. Creating a duplicate of something already on `main` without written justification is a blocking defect.

This is the last-resort catch. Even if brainstorming and the plan missed it, the implementer agent hits a forced check before `file.write`.

## Delivery

This is a three-file change:

1. **New skill: `superpowers:pre-design-audit`**: create `.claude/skills/pre-design-audit/SKILL.md` as a project-local skill. It formalises the four-signal check and the clearance report format so any agent can invoke it by name. This makes the audit independently testable and reusable outside of brainstorming.

2. **Amend brainstorming skill**: edit the Step 1 checklist text in the project-local override or upstream plugin, as above.

3. **Amend `feedback_subagent_prompt_template.md`**: add the pre-file-create check paragraph.

The writing-plans amendment (Hook 2) can be done as a note in the CLAUDE.md `## Behavior` section if a local skill override isn't available.

## What this does not fix

- A worktree that was branched before a feature landed and where `origin/main` is not accessible (no network, detached HEAD). In that case the git log check will return no results. Mitigation: the codebase search still runs against the worktree's local files; if the file was merged after the branch point, the codebase check will miss it too. This edge case is rare and acceptable for now.
- Semantic duplicates where the name differs but the purpose overlaps (e.g., `SourceBadge.tsx` proposed when `ClaimSourceChip.tsx` already exists). The semantic search in signal 2 reduces this but doesn't eliminate it.

## Acceptance criteria

- [ ] A `superpowers:pre-design-audit` skill file exists at `.claude/skills/pre-design-audit/SKILL.md` and can be invoked via the `Skill` tool.
- [ ] The skill runs the four-signal check (codebase literal, codebase semantic, git log, needles/specs) and produces a clearance report in the defined format.
- [ ] The brainstorming skill's Step 1 (in the project-local override or upstream) mandates invoking pre-design-audit before asking clarifying questions.
- [ ] `feedback_subagent_prompt_template.md` includes the pre-file-create check with the exact search commands.
- [ ] When a proposed component name matches an existing file on `main` (literal), the audit returns `MATCH FOUND` and blocks all design work until the reuse/justify checkbox is resolved.
- [ ] When only semantic hits exist, the audit returns `POSSIBLE MATCH` and blocks file-structure decisions until each hit is reviewed and resolved in writing.
- [ ] When no match exists, the audit returns `CLEAR` and the workflow continues without interruption.
- [ ] The writing-plans SKILL.md (or CLAUDE.md equivalent) distinguishes `Create:` vs `Existing (reuse):` in the file-structure section.

## Critical files

- `.claude/skills/pre-design-audit/SKILL.md`: the shipped skill (already exists as of →1663)
- The brainstorming skill file (project-local override or upstream plugin SKILL.md)
- `~/.claude/projects/.../memory/feedback_subagent_prompt_template.md`: standing subagent spawn instruction
