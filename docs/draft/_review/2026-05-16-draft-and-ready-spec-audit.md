# Draft + Ready Spec Audit — 2026-05-16

Reviewed by: subagent `review-draft-ready-specs-7f0d83`
Tools used: ostk MCP (read, bash, search) — no native fallback.

---

## Inventory

- **1 spec in draft** (`docs/draft/`)
- **5 specs in ready** (`~/.myos/specs/`)
- **6 total**

| # | Title | Path | Status | Size |
|---|-------|------|--------|------|
| 1 | Hook System Review | `docs/draft/hooks-review-2026-05-15.md` | draft | 17,972 bytes |
| 2 | Gemini CLI Integration | `~/.myos/specs/gemini-cli-integration.md` | ready | 3,725 bytes |
| 3 | Left Nav Consolidation Plan | `~/.myos/specs/left-nav-consolidation-plan.md` | ready | 4,746 bytes |
| 4 | Pattern Watcher | `~/.myos/specs/pattern-watcher.md` | ready | 12,020 bytes |
| 5 | Spec Auto-Status & Terminal-Agent Detection | `~/.myos/specs/spec-auto-status.md` | ready | 9,088 bytes |
| 6 | VP Marketing First Impression | `~/.myos/specs/vp-marketing-first-impression.md` | ready | 8,398 bytes |

Open Tasks for context: 19 open (P1: 8, P2: 6, P3: 3).
Recent commits reviewed: 50 (back to `23cfcb0` feat: Atlassian integration).

---

## Per-spec verdict

---

### Hook System Review ([docs/draft/hooks-review-2026-05-15.md](../hooks-review-2026-05-15.md))

- **Status:** draft
- **Verdict: OUT OF DATE — archive or move to docs/reference/**

This isn't a spec. It's a completed plain-language review of every active hook in `.claude/hooks/` and `~/.claude/hooks/`. Every hook was assessed; the verdict for all 15 was "keep." There are no acceptance criteria, no implementation steps, and nothing left to build or decide. The analysis is finished.

It doesn't belong in `docs/draft/` because draft implies there's a pending decision or pending build. The right home is `docs/reference/hooks.md` (a living doc that gets updated when hooks change) or archived. If you ever want to revisit hook health, this becomes a template for the next review — not an ongoing spec.

**Receipt:** Content is entirely descriptive ("Here is what each hook does, here is what would happen if we deleted it"). No unchecked AC boxes exist because none were written.

---

### Gemini CLI Integration ([~/.myos/specs/gemini-cli-integration.md](file:///Users/torimeyer/.myos/specs/gemini-cli-integration.md))

- **Status:** ready
- **Verdict: OUT OF DATE — archive**

The work described in this spec has shipped. All three components it called for now exist in the codebase:

- `api/services/gemini_cli_provider.py` — exists, fully implemented (binary detection, subprocess streaming, auth check, fallback logic).
- `api/services/chat_providers.py` — updated to route through the CLI provider.
- `api/services/provider_detection.py` — `gemini_cli` is included in the detection dict (confirmed by commit `0e9d59b fix(provider-detection): patch gemini_cli and add it to expected dict`).
- Settings toggle — shipped in commit `e4316e2 wip: Gemini CLI toggle in Settings + auth status timing + chat panel surface`.

**Receipt:** `api/services/gemini_cli_provider.py` exists (inspected); git log shows 4 Gemini CLI commits merged to main. Nothing in the spec remains unbuilt.

---

### Left Nav Consolidation Plan ([~/.myos/specs/left-nav-consolidation-plan.md](file:///Users/torimeyer/.myos/specs/left-nav-consolidation-plan.md))

- **Status:** ready
- **Verdict: NEEDLE — file with `ostk work add`**

The design work is done. The spec evaluated four options, and you left USER FEEDBACK selecting **Option B** on 2026-05-15: "Move Tour, Activity, and Rules out of the bottom rail into Settings, keep the rail tight." No further design is needed.

What remains is a focused implementation: remove three items from `app/src/components/Sidebar.tsx`, add them inside the Settings page. That's 2 files, no new architecture, no Problem-Goals-Non-goals to resolve. It qualifies as a Task, not a spec.

**Receipt:** Inspected `app/src/components/Sidebar.tsx` — Tour button, Activity NavLink, and Rules NavLink are still in the sidebar, confirming this is unbuilt. Commit `d55ed9f refactor(ui): move activity + transcripts out of main nav into settings` moved Transcripts (not Tour/Activity/Rules), so there's prior art for this pattern.

**Suggested Task text:**
```
ostk work add "Move Tour, Activity, and Rules from sidebar bottom rail into Settings — Option B from left-nav-consolidation-plan spec. Remove Tour button, Activity NavLink, and Rules NavLink from Sidebar.tsx; add them as links/buttons inside the Settings page. 2 files: Sidebar.tsx + Settings area." --priority P2
```

---

### Pattern Watcher ([~/.myos/specs/pattern-watcher.md](file:///Users/torimeyer/.myos/specs/pattern-watcher.md))

- **Status:** ready
- **Verdict: KEEP AS SPEC**

This is the right shape for a spec. It proposes a new silent learning layer across six distinct components: an observation writer hook, a per-session reader hook, ostk-recall configuration, a "What I learned about you" panel, a tier-promotion system, and the full ostk-recall wire-up. It touches the hook system, backend, frontend, and an external binary. All 7 AC items are unchecked.

Nothing has shipped. The observations directory (`~/myos/observations/`) doesn't exist. `mem.fault_recall` shows as inactive. ostk-recall has a config file (`~/.config/ostk-recall/config.toml`) but isn't wired into the session hooks.

**What's missing before implementation can start:** The open questions at the bottom of the spec haven't been resolved (hook insertion points, panel location, cluster-summary prompt, backfill strategy). These should be answered before `saa`-ing it.

**What's missing from the spec itself:** Verification steps are written but not formatted as `- [ ]` AC checkboxes — they're prose. Worth converting before build starts so agents can track completion.

---

### Spec Auto-Status & Terminal-Agent Detection ([~/.myos/specs/spec-auto-status.md](file:///Users/torimeyer/.myos/specs/spec-auto-status.md))

- **Status:** ready
- **Verdict: KEEP AS SPEC — but Task →1420 is a duplicate and should be closed**

The spec is well-designed. It covers four implementation phases: a claims registry in the API, lazy spec decomposition, three active claim paths (wrapper CLI, agent self-register, slash command), and a passive git-commit watcher. It has clear AC and tradeoff tables. Nothing has been built yet (confirmed: `compute_spec_status` and `_spec_claims` don't exist in the codebase).

The problem: open Task **→1420** ("Auto-promote specs to 'build' state when implementation begins") describes the same feature at a coarser level. It was filed 2026-05-15, the same day this spec was promoted. The Task has more limited scope (just the status flip) while the spec has the full design. They are the same work.

**Recommended action:** Close →1420 with a note pointing to this spec. The spec is the authoritative design; the Task is a duplicate at lower resolution.

---

### VP Marketing First Impression ([~/.myos/specs/vp-marketing-first-impression.md](file:///Users/torimeyer/.myos/specs/vp-marketing-first-impression.md))

- **Status:** ready
- **Verdict: KEEP AS SPEC — but flag temporal context**

Three substantial blocks of unbuilt work:

- **Block 1 (source library + KNOWLEDGE grounding):** `api/routers/knowledge.py` is still the 97-line note-taking endpoint the spec calls out. No `KNOWLEDGE` directive exists in `api/services/agentfile_parser.py`. No `~/.myos/sources/` directory. Fully unbuilt.
- **Block 2 (spec-to-artifact chooser):** The Spec wizard has no "What does this produce?" step. No `document-drafter`, `slides-drafter`, `diagram-drafter`, or `skill-builder` agent templates exist. Fully unbuilt.
- **Block 3 (MCP/Skill visibility on agent pages):** No marketplace agent declares an `MCP` line today. Unbuilt.

**Temporal flag:** The spec is dated 2026-05-14 and was written as prep for a meeting with a VP of marketing. It's been 2 days. Worth confirming whether that meeting has happened and whether the priority has changed. If the meeting happened and the VP is on board, this is high-urgency. If the meeting hasn't happened yet, the spec is still fresh. If the meeting is off, priority drops significantly. The spec itself is well-structured and valid regardless — but the build order and timeline depend on the meeting outcome.

The estimate is 9 days of work. This warrants remaining a spec (7+ files, 3 distinct behavior areas, design decisions throughout).

---

## Cross-cutting findings

### Specs overlapping with open Tasks

| Spec | Overlapping Task | Relationship |
|------|--------------------|--------------|
| Spec Auto-Status | →1420 | Exact duplicate. Task is a coarser version of the spec. Close →1420. |
| Left Nav Consolidation | →1369, →1363 | Referenced inside the spec as "already planned." →1363 (iMessage/Contacts merge) is called out as a follow-on, not a blocker. →1369 was the Task that prompted the spec draft (commit `e87dab2 →1369 draft left nav consolidation plan for review`). Both can stay open until the Task for Option B is filed. |

### Recurring themes

- **Specs surface (3 of 6 specs touch it):** Spec Auto-Status, the hooks review, and the VP marketing spec all interact with the spec pipeline in some way. No umbrella spec exists for "making the spec flow work end-to-end for non-code output." Something to watch.
- **Missing observations directory:** Pattern Watcher is gated on ostk-recall being wired up. That's the actual blocker, not the spec quality.

### Specs without acceptance criteria

- `hooks-review-2026-05-15.md` — no AC at all (it's a review doc, not a spec).
- `gemini-cli-integration.md` — has a "Verification & Testing" section but no `- [ ]` checkboxes. Moot since it's shipped.
- `pattern-watcher.md` — AC is properly checkboxed but verification steps at the bottom are prose, not checkboxes.

### Specs referencing already-shipped features

- `gemini-cli-integration.md` — fully shipped, see verdict above.

---

## Recommended action summary

| Action | Spec | Notes |
|--------|------|-------|
| **Archive** | Hook System Review | Completed review, not a spec. Move to `docs/reference/hooks.md`. |
| **Archive** | Gemini CLI Integration | All three spec objectives shipped. Mark as archived. |
| **Convert to Task** | Left Nav Consolidation Plan | Decision made (Option B). File 1 Task, archive the spec. |
| **Keep as spec** | Pattern Watcher | Large unbuilt feature. Resolve open questions before `saa`-ing. |
| **Keep as spec** | Spec Auto-Status | Well-designed, unbuilt. Close Task →1420 as duplicate. |
| **Keep as spec** | VP Marketing First Impression | 9 days of work, 3 blocks, fully unbuilt. Confirm meeting status first. |

**Summary counts:** 3 keep / 1 Task / 0 combine / 2 archive.
