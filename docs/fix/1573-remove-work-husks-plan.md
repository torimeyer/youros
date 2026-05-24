# →1573: Remove "work husks" terminology

## Finding

The phrase "work husks" (two words) does **not** appear anywhere in code, scripts, docs, or memory files. The standalone word "husk" does appear in the Specs page as user-visible copy for empty/placeholder spec documents.

Partial cleanup already happened (line 1036 already says "empty drafts detected").

## Remaining user-visible "husk" strings to replace

| File | Line | Before | After |
|------|------|--------|-------|
| `app/src/pages/Specs.tsx` | 749 | `"husk" : "husks"` (toast) | `"empty draft" : "empty drafts"` |
| `app/src/pages/Specs.tsx` | 1044 | `Delete all husks older than 7 days` | `Delete empty drafts older than 7 days` |
| `app/src/pages/Specs.tsx` | 1216 | `Husk` (tag label) | `Empty draft` |
| `app/src/pages/Specs.test.tsx` | various | toast/describe strings | updated to match |

## What stays unchanged

- Internal variable names (`visibleHusks`, `oldHusks`, `husk` field) — not user-visible
- Backend classes (`HuskResult`, `compute_husk_status`) — internal API
- `data-testid` attributes — updated alongside the test strings

## Confirm clean

`grep -ri "work husk" .` must return zero after this change.
