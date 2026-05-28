# Vite 504 on Optimized Deps — Root Cause Report

**Needle**: →1777  
**Date**: 2026-05-28  
**Scope**: diagnose-only, no code changes  
**Vite version**: 8.0.3  

---

## Summary

Blank page in incognito is caused by a 504 on CJS interop chunk files (e.g. `react-3_O8oni9.js`). The chunk gets a `?v=HASH` query injected by Vite when `react.js` is transformed, but the hash embedded in the cached transform diverges from the hash the running Vite instance expects. The browser's request for the chunk with the stale hash hits the `throwOutdatedRequest` path and never loads React.

---

## Reproduction

With the frontend running at `https://127.0.0.1:3010`:

```bash
# Step 1: Get the hash Vite injects into entry modules
curl -sk "https://127.0.0.1:3010/src/main.tsx" | grep -oE "v=[a-f0-9]+"
# → v=9ed46be8   (this is the current live hash)

# Step 2: Confirm dep loads OK with live hash
curl -sk -o /dev/null -w "%{http_code}" "https://127.0.0.1:3010/node_modules/.vite/deps/react.js?v=9ed46be8"
# → 200

# Step 3: Get the chunk hash embedded in Vite's transformation of react.js
curl -sk "https://127.0.0.1:3010/node_modules/.vite/deps/react.js?v=9ed46be8" | head -2
# → import { t as require_react } from "/node_modules/.vite/deps/react-3_O8oni9.js?v=1e419c9f";

# Step 4: Request the chunk with that hash
curl -sk -o /dev/null -w "%{http_code}" "https://127.0.0.1:3010/node_modules/.vite/deps/react-3_O8oni9.js?v=1e419c9f"
# → 200   (works NOW because the module cache still has 1e419c9f as the chunk hash)

# Step 5: The on-disk _metadata.json still shows a THIRD stale hash
python3 -c "import json; d=json.load(open('/Users/torimeyer/claude/torios/app/node_modules/.vite/deps/_metadata.json')); print(d['browserHash'])"
# → a4df3f71   (written at Vite startup 07:57:28, never updated)
```

Three different hashes are in play simultaneously:
| Hash | Source | State |
|---|---|---|
| `a4df3f71` | `_metadata.json` on disk | Startup hash, never updated |
| `1e419c9f` | Cached transform of `react.js` (chunk URL) | From first re-optimization |
| `9ed46be8` | Live Vite injects into `main.tsx` | From second re-optimization (in-flight) |

---

## Root Cause

### Mechanism: CJS interop chunks get `?v=HASH` injected with the wrong hash

React is a CommonJS module. Vite pre-bundles it as `react.js` with a CJS-to-ESM interop wrapper that imports a shared chunk `react-3_O8oni9.js`:

```js
// react.js (on disk)
import { t as require_react } from "./react-3_O8oni9.js";
export default require_react();
```

When Vite **transforms** `react.js` for the browser, it resolves the `./react-3_O8oni9.js` import. Because the importer (`react.js`) is inside `node_modules`, Vite takes the `skipOptimization = true` path (Vite 8 source, `optimizerResolvePlugin`, line ~31927):

```js
if (skipOptimization) {
  const versionHash = depsOptimizer.metadata.browserHash;  // top-level hash
  if (versionHash && isJsType) newId = injectQuery(newId, `v=${versionHash}`);
}
```

This injects the **current top-level in-memory `browserHash`** into the chunk URL. Chunks do **not** have their own `browserHash` in the metadata (Vite 8 `parseDepsOptimizerMetadata` does not set per-chunk `browserHash`). So the chunk URL becomes `react-3_O8oni9.js?v=<whatever-hash-was-live-at-transform-time>`.

### Why the hash diverges: mid-session re-optimization

Vite 8's `load()` hook for optimized deps (line ~4851) compares the URL's `?v=HASH` against the **per-dep** `info.browserHash`. If they don't match → 504. But when Vite starts a re-optimization mid-session, it:

1. **Mutates** `metadata.browserHash` to the new hash in-place (line ~31294) — before optimization completes
2. Sets each dep's `info.browserHash = newHash` only **after** `commitProcessing` runs

Between step 1 and step 2 (optimization is in flight), the Vite module cache still has old transforms with old chunk URLs. When the browser requests a chunk using the old hash, Vite finds the chunk in metadata with `info.browserHash = undefined` (chunks never have it) → `undefined !== anyHash` → `throwOutdatedRequest` → 504.

### Why blank page is permanent in incognito

Because Vite's **module transformation cache** is not invalidated until an optimization completes and `commitProcessing` sends a full-page-reload. The stale `react.js` transform (with old chunk hash) stays in Vite's in-memory cache across reloads. Every new incognito load:

1. Entry module gets `?v=9ed46be8` (current live hash)
2. `react.js?v=9ed46be8` → served from Vite's stale module cache → chunk URL has old hash
3. If the old chunk hash no longer matches the live chunk hash → 504 on chunk → React never loads → blank page

### What triggered the re-optimizations

The v4.0.0 commit (`9ab19c4b`) changed `app/package-lock.json` by 1,200+ lines while keeping the same declared dependencies (only version field changed in `package.json`). This caused a lockfile hash mismatch at Vite startup, triggering re-optimization #1.

Additionally, `app/pnpm-lock.yaml` (last modified May 12, 16 days stale) exists alongside `package-lock.json`. Vite 8's `lockfileFormats` lists both. The combined or re-read lockfile hash may differ from what `_metadata.json` recorded, causing further re-optimization rounds on subsequent browser connections.

---

## Evidence Chain

| Test | Result | What it proves |
|---|---|---|
| `react.js` (no `?v=`) | 200 | File is readable; Vite's static server bypasses the plugin path |
| `react.js?v=a4df3f71` | 504 | On-disk hash no longer matches live in-memory hash |
| `react.js?v=9ed46be8` | 200 | `9ed46be8` is the current live dep hash |
| `react-3_O8oni9.js?v=9ed46be8` | 504 | Chunk hash in live memory is NOT `9ed46be8` (second re-opt still in flight) |
| `react-3_O8oni9.js?v=1e419c9f` | 200 | `1e419c9f` is the chunk hash from the first completed re-opt |
| `main.tsx` transformed | injects `9ed46be8` | Confirms live dep hash is `9ed46be8` |
| `react.js?v=9ed46be8` body | chunk URL has `1e419c9f` | Stale module cache: transform was built when hash was `1e419c9f` |
| `_metadata.json` mtime | `07:57:28`, unchanged | Second re-optimization has not completed; metadata never written |

---

## Workaround

Kill and restart Vite with the cache cleared:

```bash
kill $(lsof -tiTCP:3010 -sTCP:LISTEN)
rm -rf app/node_modules/.vite
scripts/dev-frontend.sh
```

This forces a fresh optimization from scratch. All three hashes align. On a clean start there is no mid-session re-optimization, so the module cache and the live hash stay consistent.

---

## Recommended Fix

Two independent fixes should both be applied:

**Fix A — prevent spurious lockfile re-optimizations**:  
Delete the stale `app/pnpm-lock.yaml`. The project uses npm (`package-lock.json`). Having both lockfiles present gives Vite ambiguous input for its lockfile hash, triggering re-optimization on every start where the two files have diverged.

```bash
git rm app/pnpm-lock.yaml
```

**Fix B — add `optimizeDeps.force: false` guard and clear cache in `dev-frontend.sh`**:  
`scripts/dev-frontend.sh` does not clear `node_modules/.vite` before starting. After a `package-lock.json` change (like the v4.0.0 bump), the stale cache forces Vite into a re-optimization run that causes mid-startup hash divergence. Add a cache clear:

```bash
# In dev-frontend.sh, before `node "$VITE_BIN"`:
rm -rf "$APP_DIR/node_modules/.vite"
```

Alternatively add `optimizeDeps.force: true` to `vite.config.ts` (always re-optimize on start), which eliminates the stale-cache scenario at the cost of a slightly slower cold start.

**Fix C — set `ignoreOutdatedRequests: true` in `vite.config.ts`**:  
Vite 8 added `optimizeDeps.ignoreOutdatedRequests` (default `false`). Setting it to `true` tells Vite to serve dep files even when the `?v=` hash doesn't match, preventing 504s during the optimization transition window. This is a safety net, not a root-cause fix.

---

## File a follow-up task

The recommended fix (A + B) is tracked as a separate implementation task (filed below via `ostk work add`).
