# Onboarding flow polish review (→1491)

**Date:** 2026-05-19  
**Reviewer:** agent 1491-onboarding-flow-polish-rev-b176a7  
**Files read:** `app/src/components/OnboardingWizard.tsx` (1756 lines, gen 53), `app/src/components/TeamOnboardingSteps.tsx` (gen 2)  
**Frontend:** confirmed reachable at https://127.0.0.1:3010/

---

## Flow map

**Personal (no team fork):**
Welcome → You → Name → FilesLocation → Profile → Customize → Theme → Tracking → Connect → Ready

**Personal (team fork visible):**
Fork → Welcome → You → Name → FilesLocation → Profile → Customize → Theme → Tracking → Connect → Ready

**Team:**
Fork → OrgName → AdminEmail → InviteTeam → Guardrails → Theme → Connect → TeamReady

Connect is silently skipped when a provider is already detected (`/providers/detect` returns a hit). This affects all three paths.

---

## Copy

### C1 — Duplicate "Welcome!" on Fork + Welcome steps
**File:** `OnboardingWizard.tsx:632`  
ForkStep renders `<h1 className="text-3xl font-bold mb-2">Welcome!</h1>`. The very next step (Welcome) also opens with `Welcome!` (line 807). Users who go through the Fork step see the greeting twice in under 3 seconds.  
**Fix applied inline:** Changed ForkStep heading to "Let's get started" (see commit).

### C2 — Profile heading breaks when OS name is blank
**File:** `OnboardingWizard.tsx:396`  
`<h2>Tell {osName} about you</h2>` — if the user skipped the Name step, `osName` is `''` and the heading renders as "Tell  about you" (two spaces, missing word). The Ready step handles this correctly with `{osName || 'myOS'}` but Profile does not.  
**Fix applied inline:** Changed to `Tell {osName || 'your OS'} about you`.

### C3 — TrackingStep first option is a statement, not a choice label
**File:** `OnboardingWizard.tsx:1052`  
Option label: `"myOS is my daily dashboard, track everything."` — this reads as a statement of fact, not a choice. The other two options ("I always want tracking when I'm in my work repo.", "I just want to try myOS without touching anything else.") use first-person choice phrasing.  
**Fix applied inline:** Changed to `"Track everything — myOS is my main dashboard."` (consistent with the intent, first-person removed for brevity match with the others).

### C4 — Connect step heading doesn't match its contents
**File:** `OnboardingWizard.tsx:1161`  
Heading: "Connect your providers." But the Connect step includes Atlassian (Jira/Confluence) and GitHub — those are integrations, not AI providers. A user who clicked the Anthropic tab and then scrolls down is surprised to see Jira setup here.  
**Proposed fix:** Change heading to "Connect your tools" and update the subtext to reflect both AI providers and integrations.

### C5 — Gemini Cloud Console instructions are incomplete and assume context
**File:** `OnboardingWizard.tsx:1302`  
Step 2 of the 3-step list reads: "Open Credentials and click Create credentials, API key." — this skips that "Open Credentials" means navigating to "APIs & Services → Credentials" first. Users arriving at the Cloud Console for the first time won't know where Credentials lives.  
**Proposed fix:** Expand step 2 to "Go to APIs & Services → Credentials, then click Create credentials → API key."

### C6 — Customize error copy points to a control that isn't nearby
**File:** `OnboardingWizard.tsx:726`  
Error text: `"Couldn't load suggestions. Skip this step or try again."` — "Skip this step" sounds like a button, but it's just advice. The actual Skip button is in the nav at the bottom of the page, visually separated from the error. Users looking for a "Skip this step" button next to the error won't find one.  
**Proposed fix:** Change to `"Couldn't load suggestions — use the Skip button below or try again."` (makes the location explicit).

---

## UX

### U1 — Connect step is silently skipped when a provider is detected, hiding Atlassian/GitHub
**File:** `OnboardingWizard.tsx:143–148`  
When `/providers/detect` returns a hit (e.g., Claude Code is set up), the Connect step index is skipped entirely. Atlassian and GitHub setup cards live only in Connect. So a Claude Code user never sees them during onboarding. This is probably the largest discoverability gap in the current flow.  
**Child needle filed:** see →1492.

### U2 — No step persistence across reload
**File:** `OnboardingWizard.tsx` overall  
If the user reloads mid-onboarding (e.g., page crashes, network hiccup, Cmd+R out of habit), `stepIndex` resets to 0 and they start over. The OAuth redirect path does save/restore step (via `PATCH /settings onboarding_step`), but normal navigation doesn't.  
**Child needle filed:** see →1493.

### U3 — No exit hatch from the wizard
**File:** `OnboardingWizard.tsx:329–336`  
The wizard is `fixed inset-0 z-50` with no close button and no Escape key handler. A user who accidentally triggers a fresh-install state (e.g., reset their localStorage but has a working backend) is stuck — they must complete the full flow to get back to the app.  
**Child needle filed:** see →1494.

### U4 — Tracking repo path captured as folder name, not full path
**File:** `OnboardingWizard.tsx:1103`  
When a user picks the "repo" tracking option and selects a folder, the path stored is `rel.split('/')[0]` — just the top-level folder name (e.g., "myproject"), not an absolute path. The `/onboarding/enable-myos-hooks` endpoint likely needs an absolute path to install the hook in the right place. This looks like a functional bug.  
**Child needle filed:** see →1495.

### U5 — Persona install fires on card click, not on Next — no undo
**File:** `OnboardingWizard.tsx:162`  
`api.post('/agents/pm-templates/install-persona', ...)` fires immediately when a persona card is clicked, before the user confirms by clicking Next. If the user changes their mind and picks a different persona, the old persona's templates may already be installed. There's no rollback.  
**Child needle filed:** see →1496.

### U6 — Atlassian/GitHub cards are invisible while status loads
**File:** `OnboardingWizard.tsx:1416, 1581`  
Both `AtlassianSetupCard` and `GithubSetupCard` return `null` while `connected === null` (loading). On slow connections, the Connect page renders with just the Anthropic/Google sections for a moment, then Atlassian and GitHub pop in. No skeleton or placeholder during load.  
**Proposed fix:** Show a small skeleton row while `connected === null` instead of returning `null`.

---

## Visual

### V1 — Light theme preview in ThemeStep can be overridden by global dark CSS
**File:** `OnboardingWizard.tsx:966–988`  
The dark theme preview hardcodes hex colors (correct per the comment at line 999). The light theme preview uses Tailwind classes (`bg-gray-100`, `bg-white`, etc.). If a returning user has `darkMode=true` and `pickedDark` starts as `true` (line 298: `useState(darkMode)`), the Theme step renders in dark mode. Global `[data-theme="dark"]` CSS overrides Tailwind's light-named classes in the light preview, making it look dark. The user picks "Light" to see a dark preview.  
**Proposed fix:** Also hardcode hex values in the light preview, or ensure `effectiveDark` is forced to false until the Theme step is confirmed.

### V2 — Profile step communication style buttons have conflicting border classes
**File:** `OnboardingWizard.tsx:474`  
Unselected button class: `border-slate-300 dark:border-slate-700 ${inputCls}`. But `inputCls` already includes `border-gray-300` (light) or `border-slate-700` (dark). The prefix `border-slate-300` conflicts with `inputCls`'s `border-gray-300` — slate and gray are different Tailwind scales. Tailwind last-class wins, meaning the actual border is from `inputCls`, and `border-slate-300` is dead weight. Not a visible bug on most monitors but makes the code misleading.  
**Proposed fix:** Remove the standalone `border-slate-300 dark:border-slate-700` and rely solely on `inputCls`.

### V3 — Customize step uses plain "Loading..." with no spinner
**File:** `OnboardingWizard.tsx:753`  
All other async loading states in the app use a spinner component. Customize shows raw text "Loading..." inconsistently.  
**Proposed fix:** Replace with the app's standard spinner/skeleton pattern.

---

## Edge cases

### E1 — Two tabs open during onboarding race on settings writes
Both tabs run `finish()` independently. The second tab to finish overwrites the first tab's settings (OS name, provider, tracking). The user ends up with whichever tab won the last PATCH call. Low probability but silent data loss.

### E2 — Team flow has no Tracking setup
The Team path (Fork → OrgName → AdminEmail → InviteTeam → Guardrails → Theme → Connect → TeamReady) has no tracking step. A team admin finishing onboarding never configures myOS hooks. This may be intentional but it's not surfaced to the user (no "you can set up tracking in settings later" note on TeamReady).

### E3 — OrgName and AdminEmail accept empty strings
**File:** `TeamOnboardingSteps.tsx` — both input steps call `onNext` on Enter and there's no validation guard. An empty OrgName produces the TeamReadyStep headline " OS is ready" (blank prefix). Empty AdminEmail means the admin record has no email.  
**Proposed fix:** Disable the Next button (or add a simple `if (!orgName.trim()) return` guard) on both steps.

### E4 — Privacy link + Enter on Welcome step double-fires
**File:** `OnboardingWizard.tsx:275–288`  
The global Enter handler excludes `INPUT`, `TEXTAREA`, `BUTTON`, and `contentEditable` but not `A` (anchor). If the privacy link has keyboard focus and the user presses Enter: the link opens in a new tab AND the wizard advances to the next step simultaneously. Minor but unexpected.

---

## Summary of inline fixes (this commit)

| # | File | Line | Change |
|---|------|------|--------|
| C1 | `OnboardingWizard.tsx` | 632 | ForkStep heading: "Welcome!" → "Let's get started" |
| C2 | `OnboardingWizard.tsx` | 396 | Profile heading: `{osName}` → `{osName \|\| 'your OS'}` |
| C3 | `OnboardingWizard.tsx` | 1052 | Tracking label 1: awkward statement → "Track everything — myOS is my main dashboard." |

## Summary of child needles filed

| Needle | Title | Priority |
|--------|-------|----------|
| →1492 | Connect step skipped when provider detected — Atlassian/GitHub not shown | P2 |
| →1493 | Onboarding: persist step index on reload | P3 |
| →1494 | Onboarding wizard: add exit/close hatch | P3 |
| →1495 | Tracking repo path captures folder name only, not absolute path | P1 |
| →1496 | Persona install fires on card click before user confirms | P3 |
