# Method Not Allowed (405) Diagnose — →1601

## Surface

**Page:** Specs page — NeedsClarityChip component  
**Trigger:** Clicking "Suggest fix" (calls `/clarity/suggest`) or "Accept" (calls `/clarity`) on any failed check  
**Also affected:** Task mode clarity calls (`/clarify/suggest`, `/clarify/apply`)

## Root Cause

`NeedsClarityChip.tsx` passes paths that already include `/api/` to the `api` client, which itself prepends `/api` (from `BASE = '/api'` in `api.ts`). This doubles the prefix.

**Frontend calls (incorrect):**
- `api.post("/api/specs/${specPath}/clarity/suggest", ...)` → actual URL: `POST /api/api/specs/.../clarity/suggest`
- `api.patch("/api/specs/${specPath}/clarity", ...)` → actual URL: `PATCH /api/api/specs/.../clarity`
- `api.post("/api/tasks/${taskId}/clarify/suggest", ...)` → actual URL: `POST /api/api/tasks/.../clarify/suggest`
- `api.post("/api/tasks/${taskId}/clarify/apply", ...)` → actual URL: `POST /api/api/tasks/.../clarify/apply`

**Why 405 (not 404):** The catch-all `DELETE /specs/{doc_path:path}` route in `specs.py` matches the doubled path (after stripping the `/api` prefix, FastAPI sees `/api/specs/.../clarity/suggest` and the `{doc_path:path}` greedily captures `api/specs/.../clarity/suggest`). The path matches for DELETE but not POST/PATCH, so FastAPI returns 405.

## Verified live

```
POST /api/api/specs/foo/clarity/suggest → 405 Method Not Allowed
PATCH /api/api/specs/foo/clarity       → 405 Method Not Allowed
POST /api/api/tasks/foo/clarify/suggest → 405 Method Not Allowed
```

## Fix

Remove the `/api/` prefix from the four paths in `NeedsClarityChip.tsx` (lines 76, 78, 103, 105).

Correct paths match the rest of the codebase convention (e.g. Specs.tsx uses `api.post("/specs/promote", ...)` without `/api/` prefix).

## Files changed

- `app/src/components/NeedsClarityChip.tsx` — remove `/api/` prefix from four `api.post`/`api.patch` calls
