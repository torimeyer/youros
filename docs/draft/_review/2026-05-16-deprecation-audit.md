# Deprecation Audit — 2026-05-16

Reviewed by: deprecate-auditor-codebase-scan-6d2ebe
Safety constraint: HIGH PRECISION. Each candidate has at least one strong, verifiable signal. Ambiguous cases were skipped.

## Summary

- 1 HIGH-confidence candidate
- 7 MEDIUM-confidence candidates
- 0 LOW-confidence (no ambiguous cases surfaced per spec)
- Total Python service modules scanned: ~120
- Total TypeScript component files scanned: ~60
- Total TypeScript page files scanned: ~40
- Closed needles cross-checked: ~50
- Deprecated/superseded markers inspected: 10

---

## HIGH-confidence candidates

### `app/src/components/GoalsView.tsx` — unreachable component calling a removed endpoint

- **Signals:** (1) Orphaned import: zero references to `GoalsView` anywhere in the codebase outside the file itself. No static import, no dynamic import, no lazy() call. (2) Deprecated endpoint: the component calls `GET /goals`, and `api/tests/test_goals.py` explicitly documents that endpoint no longer exists ("The `/api/goals` endpoint has been replaced by `/api/labels`") and asserts it returns 404/405. The replacement `GET /api/labels` returns 200.
- **Evidence:**

```tsx
// app/src/components/GoalsView.tsx:58
const res = await api.get<GoalsResponse>("/goals");
```

```python
# api/tests/test_goals.py:13
"""The /api/goals endpoint has been replaced by /api/labels."""
resp = await client.get("/api/goals")
assert resp.status_code in (404, 405, 422)
```

- **Closed needle:** Not tied to a single needle, but the test file documents the migration is complete.
- **Recommendation:** Delete `GoalsView.tsx`. No callers exist. The endpoint it calls is gone. The file contributes to the bundle but is dead weight.

---

## MEDIUM-confidence candidates

### `api/services/fcp_export.py` — 397-line service with zero production callers

- **Signals:** Orphaned module. Zero references in any router, main.py, or other service file. Only appears in `api/tests/test_fcp_export.py`. FCP file generation in the live product is handled via MCP plugins (fcp-sheets, fcp-drawio, fcp-pdf, fcp-slides), not this backend wrapper.
- **Evidence:**

```python
# api/services/fcp_export.py — module docstring
"""FCP one-shot export helper.
Wraps the full lifecycle of an FCP (File Context Protocol) server session
for generating files in a single pass ...
"""

class FCPExporter:  # 397 lines, never instantiated in production code
```

Search result: `grep -r "fcp_export" api/ --include="*.py" | grep -v "^api/services/fcp_export" | grep -v "^api/tests/"` → 0 results.

- **Recommendation:** Verify no external callers (scripts, CLI tools) depend on it, then delete. Tests would go with it. If the feature is planned for wiring up later, file a needle instead of leaving dead production code.

---

### `app/src/components/GithubConnect.tsx` — component replaced by inline Settings UI

- **Signals:** Orphaned import. Zero references outside the file itself (no static import, no dynamic import, not referenced in App.tsx routing). The GitHub connection UI now lives inline inside Settings.tsx (`data-testid="github-connect-section"` at line 1618), built directly without this component.
- **Evidence:**

```bash
grep -rn "GithubConnect" app/src/ --include="*.tsx" --include="*.ts"
# Returns only: app/src/components/GithubConnect.tsx (the definition itself)
```

```tsx
// app/src/pages/Settings.tsx:1617-1618 — the replacement
{expandedConnection === 'github' && (
  <div className={cardClass} data-testid="github-connect-section">
```

- **Recommendation:** Delete `GithubConnect.tsx`. The functionality it contained is now in Settings.tsx.

---

### `app/src/components/GmailTriage.tsx` — component with no import path

- **Signals:** Orphaned import. Zero references outside the file itself. The component defines a full Gmail triage UI with category badges and task creation, but nothing renders it.
- **Evidence:**

```bash
grep -rn "GmailTriage" app/src/ --include="*.tsx" --include="*.ts"
# Returns only: app/src/components/GmailTriage.tsx (the definition itself)
```

- **Recommendation:** Delete. If Gmail triage is a planned feature, the code should live in a branch or be tracked by a needle, not in an unreachable production file.

---

### `app/src/components/MeetingTasks.tsx` — component with no import path

- **Signals:** Orphaned import. Zero references outside the file itself. The component fetches meeting-to-task pairs from the backend, but is never mounted anywhere.
- **Evidence:**

```bash
grep -rn "MeetingTasks" app/src/ --include="*.tsx" --include="*.ts"
# Returns only: app/src/components/MeetingTasks.tsx (the definition itself)
```

- **Recommendation:** Delete, or file a needle if the feature is coming back.

---

### `app/src/components/WhatChanged.tsx` — component with no import path

- **Signals:** Orphaned import. Zero references outside the file itself. The component renders a "what happened while you were away" summary (completed agents, new emails, calendar events, drive changes). Nothing mounts it.
- **Evidence:**

```bash
grep -rn "WhatChanged" app/src/ --include="*.tsx" --include="*.ts"
# Returns only: app/src/components/WhatChanged.tsx (the definition itself)
```

- **Recommendation:** Delete if the feature was folded into a different surface (e.g., the briefing or dashboard). File a needle if it should be revived.

---

### `api/routers/onboarding.py:302` — `POST /onboarding/dream` explicitly deprecated endpoint

- **Signals:** Explicit deprecated marker in code plus zero active callers. The comment reads: "Deprecated: use /onboarding/intent instead. Kept so existing flows don't break." The replacement endpoint `POST /onboarding/intent` (line 365) is live and called by `OnboardingWizard.tsx:715`. No frontend code calls `/onboarding/dream`.
- **Evidence:**

```python
# api/routers/onboarding.py:302-304
@router.post("/onboarding/dream", response_model=DreamResponse)
async def dream(body: DreamRequest):
    # Deprecated: use /onboarding/intent instead. Kept so existing flows don't break.
```

```bash
grep -rn "onboarding/dream" api/ app/src/ --include="*.py" --include="*.ts" --include="*.tsx" | grep -v test
# Returns only: api/routers/onboarding.py:302 (the definition itself)
```

Replacement is active:
```tsx
// app/src/components/OnboardingWizard.tsx:715
api.post<{ starter_pack: StarterPackItem[] }>('/onboarding/intent', { intent: intentId }),
```

- **Recommendation:** Delete the `dream` endpoint and its `DreamRequest`/`DreamResponse` models if no external clients (mobile apps, scripts) are known to use it. The "existing flows" comment was written when migration was ongoing; no flows remain.

---

### `api/routers/agents.py:5729` — `POST /agents/fleets/spawn` explicitly deprecated endpoint

- **Signals:** Explicit deprecated marker in docstring plus active warning log on every call. No active frontend callers. The UI comment in `Agents.tsx:4937` confirms: "Fleets panel removed: fleet launching has been folded into the Plans page template grid. The backend `/agents/fleets/spawn` endpoint stays alive for backwards compatibility." The Plans page now uses `POST /api/specs/from-template` exclusively (`Specs.tsx:586`). The `spawn_fleet()` function is approximately 100 lines.
- **Evidence:**

```python
# api/routers/agents.py:5736-5742
"""...
Deprecated: fleet launching has been folded into the Plans page
template grid. This endpoint is kept alive for backwards compatibility
with existing callers but new UI should use
POST /api/specs/from-template instead.
"""
logger.warning(
    "spawn_fleet called; fleets are folded into plan templates; "
    "caller fleet_id=%s",
    body.fleet_id,
)
```

```tsx
// app/src/pages/Agents.tsx:4937
// Fleets panel removed: fleet launching has been folded into the
// Plans page template grid. The backend /agents/fleets/spawn
// endpoint stays alive for backwards compatibility.
```

- **Recommendation:** Remove `spawn_fleet()` and the `FleetSpawn` model once confident no external scripts call this endpoint. The `fleet_templates` service itself is still used by the list and prewarm endpoints and should be kept.

---

## Skipped patterns

- **Naming-pattern matches** (`_old`, `_legacy`, `_v1`): Skipped per spec. These are too noisy in this codebase.
- **Aged TODO/FIXME/HACK**: Skipped per spec.
- **Closed-needle references investigated:** 50+ needle references scanned. All code surrounding closed needles (→932, →916, →999, →1041, →1086, →1096, →1147, →1192, →1225, →1227, →1272, →1290, →1305, →1344, →1346, →1379) was read. In every case the surrounding code was the implemented fix, not a leftover workaround. No HIGH-confidence needle-workaround candidates emerged.
- **Ambiguous pages** (`Adoption`, `TeamDashboard`, `TeamSettings`, `TeamHome`): These appear in `app/src/pages/` but not in App.tsx routing or any import. However, they may belong to an NR-enterprise overlay or admin-only routing path not visible in the main checkout. Skipped to avoid false positives.

## Methodology notes

**What I searched:**
1. Closed-needle references: searched all `→NNNN` patterns in Python and TypeScript source, listed ~50 closed needle IDs, read 10-20 lines of context around each reference in the most workaround-shaped comments.
2. Explicit deprecated markers: searched for `deprecated`, `superseded by`, `replaced by`, `obsoleted by` in all source files; investigated each hit for (a) named replacement and (b) continued presence of deprecated code.
3. Orphaned imports: for Python services, checked that each module in `api/services/` had at least one reference outside its own file and outside test files; for TypeScript, checked that each component in `app/src/components/` appeared in at least one import statement outside its own file.
4. Replacement comments: searched for `# previously`, `# old approach`, `# before this`, `# instead of`, `# rather than` in source; all hits described design rationale for current code, not stale workarounds.

**What I excluded:**
- Worktree copies of files (only scanned main checkout files)
- Test files as callers (they keep test coverage for deprecated code, but their existence alone is not evidence the production path is live)
- Dynamic string imports — flagged as a caveat; any module with a string reference to its name elsewhere was excluded from orphan candidates

**What I would search next if signals were broader:**
- Cross-file call graph tracing for the `spawn_fleet` path to confirm no CLI scripts call it
- Dead route detection: routes registered in the router but not linked from any navigation component
- Test-only services: modules that exist only because a test mocks them, not because production code needs them
