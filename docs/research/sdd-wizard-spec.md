# SDD Wizard Research: The Ideal Spec for Automated Development

## Current State

myOS specs today have:
- **Title** (user-entered)
- **Body** (markdown, "What we want" section)
- **Acceptance criteria** (AI-generated checklist, 3 items)
- **Status lifecycle**: draft -> ready -> in-progress -> complete
- **Tasks**: decomposed from AC, assigned to builder agents
- **Templates**: 9 built-in (website, launch, research, code review, etc.)

The current flow: user types a title -> AI generates AC + body -> auto-promoted to ready -> user clicks Build -> decompose into tasks -> spawn builder agents per task.

## What's Missing for True SDD

The current spec is essentially a short brief with 3 acceptance criteria. It's good enough for simple features but falls apart for anything complex because:

1. **No scope boundaries**: agents over-build or under-build because nothing says what's explicitly out of scope
2. **No technical context**: agents guess at patterns, file locations, and conventions
3. **No test plan**: success criteria are high-level, not testable assertions
4. **No dependencies**: agents don't know what already exists or what they're building on
5. **No UI/UX guidance**: no wireframes, no user flows, no component patterns to follow
6. **No API contract**: agents invent endpoint shapes instead of following a design

## The Ideal SDD Spec Structure

What an AI agent receiving this spec needs to build a feature perfectly:

### 1. Problem Statement (required)
- Who has this problem?
- What happens today without this feature?
- One sentence: "After this ships, [user] can [action] instead of [workaround]."

### 2. Success Criteria (required, AI-assisted)
- 3-5 testable assertions, not vibes
- Each one is a sentence that starts with a verb: "Shows...", "Creates...", "Prevents..."
- Each maps to exactly one test

### 3. Scope (required)
- **In scope**: concrete list of what gets built
- **Out of scope**: concrete list of what does NOT get built (prevents over-building)
- **Depends on**: existing features/code this builds on top of

### 4. Technical Context (AI-populated)
- **Patterns to follow**: "Look at how X feature does it" with file paths
- **Files likely touched**: auto-detected from related features
- **Existing endpoints**: relevant API surface the agent should know about
- **Component patterns**: UI components to reuse

### 5. API Contract (optional, for backend features)
- Endpoint paths, methods
- Request/response shapes (even rough ones)
- Auth requirements
- Error cases

### 6. UI/UX Requirements (optional, for frontend features)
- What the user sees at each step
- What components to use (existing or new)
- Interaction patterns (inline edit, modal, wizard, etc.)
- Empty state, loading state, error state

### 7. Test Plan (AI-generated from success criteria)
- Happy path tests (from success criteria)
- Edge cases (from scope boundaries)
- Error cases (from API contract)

### 8. Non-Goals (required)
- What this feature explicitly does NOT do
- What it's NOT trying to solve
- Prevents scope creep mid-build

## Wizard Steps

### Step 1: Problem (user writes)
**Prompt**: "What problem are you solving? Who has it?"
**Input**: textarea
**AI assist**: after user writes 1-2 sentences, AI suggests a one-line "After this ships..." statement

### Step 2: Scope (user + AI)
**Prompt**: "What should this include? What should it NOT include?"
**Input**: two lists (in scope / out of scope)
**AI assist**: from the problem statement, suggest 3-5 in-scope items and 2-3 out-of-scope items

### Step 3: Success Criteria (AI-generated, user-editable)
**Prompt**: "Here's how we'll know it works:"
**Input**: checklist, pre-populated by AI from problem + scope
**AI assist**: generate 3-5 testable criteria. User can add/remove/edit.

### Step 4: Technical Context (auto-populated)
**Prompt**: "Here's what already exists that's related:"
**Input**: read-only, auto-populated by scanning the codebase
**AI assist**: full auto. Search for related files, endpoints, components. Show patterns to follow.

### Step 5: Review + Build
**Prompt**: "Ready to build? Here's the full spec."
**Input**: read-only preview of the complete spec
**Action**: "Build it" spawns builder agents

## Key Design Decisions

1. **Steps 1-3 are user-facing, Step 4 is auto-populated**. The PM fills in what only they know (problem, scope, criteria). The system fills in what only the codebase knows (technical context).

2. **AI assists everywhere but never blocks**. Every AI suggestion is editable. If AI is slow or unavailable, the user can type everything manually.

3. **Non-goals are required, not optional**. The most common agent failure is over-building. Non-goals are the cheapest way to prevent it.

4. **The spec is a markdown file on disk**. Same as today. The wizard just produces a richer markdown structure. Backwards compatible with existing specs.

5. **Templates seed the wizard**. Instead of templates writing the whole spec, they pre-fill wizard steps. A "Build a Website" template pre-fills scope and criteria but still asks the user for the problem statement.
