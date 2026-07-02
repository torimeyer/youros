# e2e settings name pollution diagnosis

## Root cause

`scripts/e2e_browser.sh` Journey 5 (Settings round-trip) writes the string
`"e2e-browser-os"` into the live backend via the real browser UI — it opens
the Settings page, types the test value into the OS Identifier input, and
presses Enter, which fires `PATCH /api/settings` against the always-running
backend at `:8000`.

The script has an inline restore at line 382 (`curl -X PATCH ... os_name ...`),
but that only runs if execution reaches line 382 in normal flow. The
`_browser_cleanup` trap (line 106, bound to EXIT/INT/TERM/HUP) only closed
the browser session — it did **not** restore the OS name.

So if the script was interrupted (Ctrl-C, SIGTERM, browser crash, agent cancel)
between pressing Enter (line 370) and the restore (line 382), the setting stayed
as `"e2e-browser-os"` permanently.

**Self-reinforcing loop**: once polluted, a subsequent run captures
`ORIGINAL_OS_NAME="e2e-browser-os"`, "restores" it to `"e2e-browser-os"`,
and the problem persists indefinitely.

### Why not YOUROS_HOME isolation instead?

`settings_store.py` already supports `YOUROS_HOME` at import time (line 13):
```python
_YOUROS_HOME = Path(os.environ["YOUROS_HOME"]) if "YOUROS_HOME" in os.environ else youros_home()
```
But the live backend at `:8000` is started by the user without `YOUROS_HOME`
set. Browser e2e tests must hit the real running app — spinning up a separate
backend for them would be a disproportionate change. Restore-on-teardown in
the trap is the correct and proportionate fix here.

### Sidebar rendering path (point 1 of brief)

`app/src/stores/app.ts:341` — `const initialOsName = lsGet(LS_KEYS.osName) || 'yourOS'`
`app/src/stores/app.ts:753-756` — on server sync, `server.os_name` overwrites
the store and localStorage key `"myos-os-name"`. The sidebar reads
`useAppStore((s) => s.osName)` from there.

## Fix

**`scripts/e2e_browser.sh`** — two changes:

1. `ORIGINAL_OS_NAME=""` initialised at line 60 (before the trap is registered)
   so that `set -u` does not fire when the trap runs before Journey 5.

2. `_browser_cleanup` now restores `ORIGINAL_OS_NAME` if it is non-empty:
   ```bash
   if [ -n "${ORIGINAL_OS_NAME:-}" ]; then
       curl -sS ${CURL_OPTS} --connect-timeout 3 -m 5 \
           -X PATCH "${API_BASE}/api/settings" \
           -H 'content-type: application/json' \
           -d "{\"os_name\":\"${ORIGINAL_OS_NAME}\"}" > /dev/null 2>&1 || true
   fi
   ```
   This runs on EXIT (normal), INT, TERM, and HUP — covering all interruption
   paths. The inline restore in Journey 5's body is kept as an explicit
   verification step; the trap restore is the safety net.

## Original name recovery

`~/.youros/settings.json` is inaccessible from the sandbox. The original name
was unrecoverable. Set to `"torios"` (the user's known local brand) via:
```
PATCH /api/settings {"os_name":"torios"}
```
Confirmed: `curl /api/settings → os_name: 'torios'`

## Regression test

`scripts/tests/test_e2e_browser_cleanup.sh` — four assertions:

1. `_browser_cleanup` body references `ORIGINAL_OS_NAME`
2. `trap _browser_cleanup EXIT` is present
3. `ORIGINAL_OS_NAME=""` init appears before the `trap` line
4. Dynamic: spawns a subprocess with the fixed trap pattern, sends SIGINT
   mid-sleep, verifies the API has the original name restored afterwards

All 4 pass: EXIT 0.
