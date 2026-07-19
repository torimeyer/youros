# Dead Code Review — 2026-07-19

Agent: review-dead-code-71524a
Context: post-texting-removal (commits 0ad0eb76, 609d1a94, 9a498edd, 5ad2ea33)

---

## Ranked findings (biggest/safest first)

### 1. `app/src/pages/Inbox.tsx` — unrouted page, unreachable
**Proof:** zero import in `App.tsx`; no `<Route path="inbox">` anywhere; `grep -rn "import.*Inbox" app/src` returns 0 hits outside the file itself and its test. `UniversalSearch.tsx:67` links to `/inbox` but that path 404s since it has no route.
**What's alive:** the backend `/inbox` router and data are still consumed by `CostTracking.tsx` — only the standalone page is dead.
**Deletion risk:** trivially safe — delete `app/src/pages/Inbox.tsx` + `app/src/pages/Inbox.test.tsx`.

---

### 2. `app/src/components/AdminGuard.tsx` — component with zero importers
**Proof:** `grep -rn "AdminGuard" app/src --include="*.tsx" --include="*.ts" | grep -v test` returns 1 hit: the definition itself. `AdminLayout` handles admin route nesting but has no role-check; `AdminGuard` was the intended guard but was never wired in.
**Deletion risk:** needs test edit — `app/src/components/AdminGuard.test.tsx` exercises it and must also be deleted.

---

### 3. `app/src/pages/Adoption.tsx` — unrouted page, superseded by CostTracking
**Proof:** zero import in `App.tsx`; `grep -rn "import.*Adoption" app/src` returns 0 hits outside the file and its test. The backend data it used (`/adoption/whats-working`) is now consumed by `CostTracking.tsx:1040` instead.
**Deletion risk:** trivially safe — delete `app/src/pages/Adoption.tsx` + `app/src/pages/Adoption.test.tsx`.

---

### 4. Dead link in `app/src/pages/AboutYourOS.tsx:117`
**Proof:** `<a href="/settings#shortcuts">` but the Shortcuts section was deleted in commit 609d1a94 (`fix(→2970)`). `grep -n "shortcuts" app/src/pages/Settings.tsx` returns 0 hits. The link points to a valid page but a non-existent anchor.
**Deletion risk:** trivially safe — remove or update the anchor (e.g. point to `/settings` or remove the link).

---

### 5. Team-mode pages — 5 files, unrouted dead cluster (needs decision)
**Proof:** `Team.tsx`, `TeamDashboard.tsx`, `TeamHome.tsx`, `TeamSettings.tsx`, `TeamStart.tsx` are all absent from `App.tsx` imports and routes. `TEAM_MODE_VISIBLE = false` hides the sidebar links, but even if flipped to `true` the pages would 404 because no `<Route>` mounts them. They form a dead cluster; none imports the others for standalone use.
**Note:** per project memory (`project_team_mode_parked.md`), team mode is intentionally parked by tori (2026-07-18). These files are kept for future work — deleting them is a decision, not a mechanical cleanup. Flagging only.
**Deletion risk:** needs decision from owner before touching.

---

## Checked and confirmed clean

- **iMessage page + ChatPanel send flow + imessageIntentDetector**: all actively used (Sidebar link, App.tsx route, ChatPanel direct-send intent detection). The _inbound SMS bridge_ was removed; the _Mac iMessage viewer and sender_ is alive.
- **`app/src/lib/pushNotifications.ts`**: imported by `Settings.tsx` (toggle + subscribe/unsubscribe) and `TopBar.tsx` (permission check). Live.
- **`app/src/services/sms.py`**: still imported and called by `api/services/reminders.py` as a fallback channel (19 references). Intentional stub with TODO(sms-provider). Live.
- **`app/src/stores/notifications.ts` vs `notificationsStore.ts`**: two different stores; both actively used (toast system vs WebSocket notification feed). No duplication.
- **All backend routers**: every file in `api/routers/` is imported and registered in `main.py` (confirmed via line 43–68 and `app.include_router` calls).
- **`TextYourOS.tsx`, `TextYourOSSettings.tsx`**: already deleted in commit 9a498edd. No references remain.
- **`AgentSpawn.notify` field**: not present in current `schemas.py`. Already cleaned in 5ad2ea33.
- **CSS (`index.css`)**: no classes referencing removed components.
- **`scripts/`**: no scripts reference deleted text/bridge files.

---

## Summary

4 confirmed dead items (3 files + 1 link) — all trivially safe to delete now.
1 parked cluster (5 team-mode pages) — keep until owner decides to resume or drop team mode.
