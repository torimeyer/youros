---
title: Settings, Onboarding and Navigation Reorganization
created_at: 2026-06-01T23:25:43Z
promoted_at: 2026-06-01T23:26:49Z
status: spec
---

## Problem

The Settings page (`app/src/pages/Settings.tsx`, ~2230 lines) is one long scroll of cards under a single "Preferences" tab. Several problems compound:

- The secondary navigation is inconsistent: Tasks uses a top tab bar, Settings uses a left rail. The app should feel like one product.
- "System Features" is a techy name, and the card is bloated because two unrelated toggles (budget caps, power user mode) are crammed into the bottom of it.
- "Standing instructions" and "Memory" sit far apart and feel redundant to users, even though they are mechanically different.
- File-location setup lives only in onboarding; there is no way to change where torios saves files afterward.
- "Starter agents" and "Shortcuts" add clutter that belongs elsewhere or shouldn't ship with defaults.
- The Help block bundles unrelated things (Rules, Take the tour, Activity log) and Activity log is not a preference at all.
- Two already-decided settings have no home yet: a "turn plans into specs" toggle and an "act on incoming iMessages" toggle (the backend poller for the latter is built but intentionally not auto-started).

## Goals

- One consistent **top-tab** secondary nav, matching the Tasks pattern.
- Group the three "how torios behaves for you" settings together: plans→specs, inbound iMessage routing, and where files are saved.
- Remove the file-location question from onboarding; expose it in Settings instead.
- Merge standing instructions and memory into one plain-language "AI behavior" section; fold Rules in (as a linked sub-page).
- Rename and de-bulk "System Features"; give power user mode its own card; remove the budget-caps toggle (re-home it under a future team-mode spec).
- Declutter: empty-by-default shortcuts, starter agents removed from Settings (the Agents tab covers adding agents), Help block dissolved.
- Move Activity log into the bottom "small nav" alongside What's New, Usage, and Settings.

## Non-goals

- Building team mode or budget-cap enforcement (separate follow-up spec).
- Merging the storage of standing instructions and memory (they keep separate backends; only the presentation unifies).
- Refactoring the Tasks page nav (optional cleanup, not required here).

## Resolved facts (from codebase audit, 2026-06-01)

- **Rules count: 17** in `.claude/hooks/lib/default-rules.json`, across 5 categories (agent_flow, tool_guards, quality, infrastructure, prompt_header). **No exact duplicates among them.** The redundancy users feel is cross-layer (the rules engine restates intent also captured in memory/standing instructions, e.g. `ostk_first` ≈ "ostk over bash"). Only one soft in-engine merge candidate: `task_hygiene` + `task_state_hygiene` → one "Task hygiene" rule (17→16). **Because 16–17 categorized rules is too many to inline, Rules stays a linked sub-page** (`SettingsRules.tsx` at `/settings/rules`) surfaced from the AI-behavior section.
- **Agents tab covers adding agents.** `app/src/pages/Agents.tsx` (route `agents`, `App.tsx:167`) has a **"New Setup"** create control (`Agents.tsx:282-286`, form at `250-266`) plus the marketplace import. Removing the Settings starter-agents section leaves a working post-onboarding path.

## Design / changes

### A. Group three settings into a new "How torios works" card (Preferences)
- New field `plans_become_specs` (default **on**, user-overridable) — owns the parked auto-spec toggle (task #2 from the connect-step session).
- New field `inbound_imessage_routing_enabled` (default **off**) — when on, backend starts `channel_intent_parser.InboundPoller` at boot. Manual route (`POST /api/channel-routing/route`), dispatchers, and the poller already exist; only auto-start is deferred. Poller baselines on first pass, so enabling does not replay backlog.
- `files_dir` location picker — moved from onboarding; reuse `FilePathPicker`.

### B. Remove the file-location step from onboarding
- `OnboardingWizard.tsx`: delete the `files_dir` step; apply `DEFAULT_FILES_DIR` silently.

### C. Standardize secondary nav on top tabs
- New `app/src/components/TopNavTabs.tsx`, modeled on `Tasks.tsx:1747-1749` (horizontal `<button>` row, `border-b-2` active underline). Replace the Settings left rail (`Settings.tsx:781-809`) with it.

### D. Merge standing instructions + memory into one "AI behavior" section
- Combine the instructions card (`Settings.tsx:831-953`) and memory editor (~`1917`) into one section, two labeled areas: "Your instructions" and "What it's learned." Keep both backends unchanged (`PATCH /settings { standing_instructions }`, `/settings/standing-instructions/suggest`; `GET /memory` + suggest/split/overflow).

### E. Surface Rules from the AI-behavior section (sub-page)
- Dedupe the soft pair (`task_hygiene` + `task_state_hygiene`) in `default-rules.json` → 16 rules. Link `SettingsRules.tsx` from the AI-behavior section; remove the Help-block link.

### F. Rename + de-bulk "System Features" → "Features"
- Keep only the real feature toggles (`features.map`). **Keep `Projects` as canonical — drop the `Projects→Files` remap** at `Settings.tsx:50-54`; keep `Transcripts→History`, `Cost Tracking→Usage`.

### G. Power user mode own card + remove budget caps
- Move power user mode (gates Delegate, Shared Workspace, ostk browser) to its own card. Delete the budget-caps toggle (`Settings.tsx:1057-1064`) and tied `showBudgetCaps` usage. Budget caps → future team-mode spec.

### H. Remove starter agents from Settings
- Delete `section-starter-agents` (`Settings.tsx:2063`) + its `CustomizeStep` reuse. Onboarding keeps `CustomizeStep`; Agents tab covers later adds.

### I. Shortcuts → empty "Add shortcut"
- Replace the default list (`section-shortcuts`, `Settings.tsx:2091-2155`) with an empty state + "Add shortcut"; persist only user-added entries. Verify no global key handler depends on the default `allShortcuts` list (e.g. ⌘K); keep load-bearing handlers alive while hidden.

### J. Dissolve the Help block
- Rules → AI-behavior (E). "Take the tour" → standalone affordance (`setShowTour(true)`). Activity log → small nav (K).

### K. Small nav (bottom of `Sidebar.tsx`)
- Bottom cluster (`Sidebar.tsx:818-844`, Usage + Settings) gains **Activity** (`/activity`) and **What's New**, reading: What's New · Activity · Usage · Settings. Edit ONLY this region (~line 818); leave the `'Projects'` label (~line 64) untouched.

## Resulting Preferences layout (top tabs: Connections | Preferences)
1. Appearance · 2. **How torios works** (plans→specs, iMessage routing, files) · 3. **AI behavior** (instructions + memory, Rules link) · 4. **Features** · 5. **Power user** · 6. Notifications · 7. Shortcuts (empty) · 8. Danger zone. Removed: Starter agents, Help block, budget-caps toggle.

## Backend
- `api/services/settings_store.py`: defaults `plans_become_specs` (on), `inbound_imessage_routing_enabled` (off). `files_dir` already exists.
- `api/main.py`: start `InboundPoller` only when the toggle is on; copy the Atlassian-poller pattern at `api/main.py:1041`.
- `default-rules.json`: merge `task_state_hygiene` into `task_hygiene`.

## Acceptance criteria

**Navigation (C)**
- [ ] A reusable `TopNavTabs` component exists and renders a horizontal tab bar with an active-underline style matching Tasks.
- [ ] Settings' secondary nav (Connections / Preferences) renders as top tabs, not a left rail.

**Grouped settings (A) + onboarding (B)**
- [ ] A "How torios works" card in Preferences contains exactly three controls: plans→specs toggle, inbound iMessage routing toggle, and a files-location picker.
- [ ] `plans_become_specs` persists (default on) and round-trips through `/api/settings`.
- [ ] `inbound_imessage_routing_enabled` persists (default off); `InboundPoller` starts at boot only when it is on, and does not start when off.
- [ ] The files-location picker reads/writes `files_dir` and reuses `FilePathPicker`.
- [ ] Onboarding no longer shows a file-location step; a default `files_dir` is applied silently for new users.

**AI behavior (D, E)**
- [ ] One "AI behavior" section shows "Your instructions" and "What it's learned" together; both save paths work (standing_instructions PATCH + suggest; memory GET + suggest/split/overflow).
- [ ] `task_state_hygiene` is merged into `task_hygiene` in `default-rules.json` (16 rules remain) and `/api/rules` returns the merged set.
- [ ] The AI-behavior section links to the Rules sub-page; the old Help-block Rules link is gone.

**Features + power user (F, G)**
- [ ] The section is titled "Features" (no "System Features"); the `Projects→Files` remap is removed and the label reads "Projects".
- [ ] Power user mode is its own card and still gates Delegate, Shared Workspace, and the ostk browser.
- [ ] The budget-caps toggle is removed from Settings and no `showBudgetCaps` control remains.

**Declutter (H, I, J)**
- [ ] No starter-agents section in Settings; the Agents tab "New Setup" control still adds agents.
- [ ] Shortcuts shows an empty state with an "Add shortcut" control and no default rows; load-bearing global keys still function.
- [ ] The Help block is gone; "Take the tour" is a standalone control.

**Small nav (K)**
- [ ] The sidebar bottom cluster shows What's New · Activity · Usage · Settings; the `/activity` link is removed from Settings.
- [ ] The `Sidebar.tsx` `'Projects'` label region is unchanged.

**Tests + verification**
- [ ] `Settings.test.tsx`, `OnboardingWizard.test.tsx`, `Sidebar.test.tsx`, and `api/tests/test_settings.py` cover the above and pass.
- [ ] `tsc -b` clean; `scripts/run-vitest.sh` green; `scripts/e2e_smoke.sh` passes before release.
- [ ] Manual: reset onboarding (`onboarded:false`), hard-refresh, confirm no file-location step; toggle iMessage on and see `InboundPoller` start in backend logs.

## Sequencing / coordination
- Work in an isolated worktree branched from current `main`. The Settings/Onboarding owner (connect-step) already landed at `efa968a0` (PR #8) — rebase to pick it up. The `Sidebar.tsx` `'Projects'` edit from the projects-tab session is non-overlapping with item K. Define `plans_become_specs` only here. Do not bounce the backend while agents run.

## Follow-up (separate spec)
- **Budget caps under team mode**: re-home budget-cap columns/enforcement as a team-mode feature.
