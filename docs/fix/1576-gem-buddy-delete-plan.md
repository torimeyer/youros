# →1576 Gem buddy delete plan

## Root cause

`api.ts:request()` unconditionally calls `res.json()` on every successful (2xx) response.
DELETE `/gems/{id}` returns **204 No Content** — an empty body.
Parsing an empty body as JSON throws a SyntaxError.
The `handleDelete` catch handler rolls back the optimistic removal and shows an error toast.
The gem IS deleted on the backend but the UI lies about it.

## Fixes

### 1. `app/src/lib/api.ts` — skip `.json()` for 204
Add a guard before `res.json()`:
```ts
if (res.status === 204) {
  notifySidebarOnWrite(method, path)
  return undefined as T
}
```

### 2. `app/src/pages/MyGems.tsx` — actionable error messages
Import `ApiError`, `ApiTimeoutError` from `../lib/api`.
Replace bare `catch {}` with typed error handling:
- **404**: gem was already deleted — keep it removed, inform user to refresh
- **timeout**: server unreachable — tell user to check yourOS is running
- **other**: generic but specific enough to help

## Verification
- curl DELETE /api/gems/{id} → 204, then GET /api/gems → gem absent
- Run targeted test: `pytest api/tests/test_gems_router.py -x -q`
- Vitest: `MyGems.test.tsx` delete tests pass
