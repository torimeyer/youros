# ostk-cache Mechanism 4: File-Handle Rewrite — Research Findings

**Task:** →1336  
**Date:** 2026-05-14  
**Status:** Blocked on auth — requires Anthropic Files API access

---

## What Mechanism 4 does

The ostk-cache proxy intercepts outgoing `POST /v1/messages` requests. For any
inline text content block whose SHA-256 hash matches a `FileCacheEntry` in
`.ostk/file_cache.jsonl`, the proxy replaces the full inline content with:

```json
{"type": "document", "source": {"type": "file", "file_id": "<id>"}}
```

This rewritten body is forwarded to Anthropic as-is. Anthropic dereferences the
file_id from its own Files API store and charges only for the ~96-byte reference
instead of the full content. That is where the token savings come from.

---

## Key decision: file_id type

**Verdict: Case (a) — Anthropic-issued file_ids. NOT locally-generated.**

Evidence:

1. The rewritten body is forwarded directly to Anthropic (no intermediate
   expansion step in the proxy).  
2. The proxy comment states explicitly: *"Anthropic charges by input tokens;
   the document-handle wrapper is ~96 bytes vs. arbitrarily large inline
   content, so swaps yield real savings."*  
   This only works if Anthropic receives and accepts the file_id reference.
   Anthropic only accepts file_ids obtained from `POST /v1/files`.
3. Writing locally-generated file_ids (e.g. `"local-<sha256>"`) would cause
   Anthropic to return an error for every substituted request, breaking all
   Claude Code sessions routing through the proxy.

---

## FileCacheEntry schema

Discovered from `tests/rewrite_integration.rs` in `os-tack/ostk-cache`:

```rust
FileCacheEntry {
    path: String,           // source file path (informational)
    file_id: String,        // Anthropic-issued file_id from POST /v1/files
    uploaded_at: String,    // ISO-8601 UTC timestamp of upload
    size: u64,              // file size in bytes
    last_seen_gen: u64,     // ostk generation counter (set to 0 on manual insert)
    stale: bool,            // set false for active entries
    sha256: Option<String>, // hex SHA-256 of file content (the match key)
}
```

JSONL format (one entry per line in `.ostk/file_cache.jsonl`):

```json
{"path":"/path/to/CLAUDE.md","file_id":"file_01Abc...","uploaded_at":"2026-05-14T00:00:00Z","size":10240,"last_seen_gen":0,"stale":false,"sha256":"a3b4c5..."}
```

---

## Min-size threshold

Default: **512 bytes** (from `RewriteOptions::default()`).  
Content blocks smaller than this are left inline even if they have a cache entry.
Only files > 512 bytes are worth adding to the cache.

---

## Current proxy state

- Proxy running: pid 80283, cwd `/Users/you/claude/torios`  
- Cache file: `.ostk/file_cache.jsonl` — **does not exist**  
- Rewrite-events log: 202 events, all with `rewrites_applied: 0`, `hits: 0`, `misses: 11`  
  The proxy IS attempting the binding step ("Binding file_id / firmware materialization"
  appears in `/Users/you/.youros/ostk-cache.log`) but finds nothing.  
- The proxy reads `.ostk/` relative to its cwd: `/Users/you/claude/torios/.ostk/`

---

## What's needed to activate Mechanism 4

### Step 1: Upload files to Anthropic's Files API

For each high-value file, run:

```bash
curl -X POST https://api.anthropic.com/v1/files \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "anthropic-beta: files-api-2025-04-14" \
  -F "file=@/path/to/CLAUDE.md"
```

Response includes a `file_id` like `file_01AbCdEf...`.

### Step 2: Populate file_cache.jsonl

Run a script that:
1. Computes SHA-256 of the local file
2. Writes a `FileCacheEntry` JSONL row to `.ostk/file_cache.jsonl` with the
   Anthropic-issued file_id

A template populate script is at: `scripts/populate-ostk-file-cache.py`
(created as part of this Task — see companion commit).

### Step 3: Keep entries fresh

Anthropic Files API files persist indefinitely until explicitly deleted (per
empirical confirmation in →1339 follow-up research). When a file is modified
locally, its SHA-256 changes, so the old entry becomes a miss and the entry
should be re-uploaded + updated. Re-upload is only needed on content change.

---

## Auth situation

The running proxy forwards requests with the caller's auth headers intact.
On Claude subscription (OAuth), the proxy has NO dedicated `ANTHROPIC_API_KEY`
set — it relies on the session's OAuth token.

The Anthropic Files API (`POST /v1/files`) as of 2026-05-14:
- **Requires** `x-api-key` (API key auth)  
- **May not be available** on OAuth/subscription auth  
- Needs investigation: test whether subscription OAuth tokens are accepted on
  `POST /v1/files` before assuming they are

---

## Highest-value candidate files

From the savings projection (5-session baseline):

| File | Approx size | Appears in sessions |
|------|-------------|---------------------|
| `/Users/you/.claude/projects/-Users-you-claude-torios/memory/MEMORY.md` | ~30 KB | Most |
| `/Users/you/claude/CLAUDE.md` | ~10 KB | Most |
| `/Users/you/claude/torios/CLAUDE.md` | ~10 KB | Most |
| Recent plan files in `~/.claude/plans/` | 3–20 KB | Per-session |

---

## Estimated savings once unblocked

From the 5-session retro (Task →1335):
- **~21% additional token savings** on top of native Anthropic prompt cache
- Equivalent to fitting roughly one more full working session inside a 5-hour
  Max subscription cap

---

## Recommended follow-up Task

File a new Task with:
- **Goal:** Obtain an API key (or confirm OAuth works) for the Anthropic Files API
- **Steps:** (1) Test subscription OAuth against `POST /v1/files`, (2) if yes,
  run `scripts/populate-ostk-file-cache.py` on the candidate files, (3) verify
  first `rewrites_applied > 0` event in `rewrite-events.jsonl`
- **Scope:** Single script run + verification pass (~30 min)

---

## .gitignore note

`b195aba chore(ostk-cache): gitignore proxy telemetry paths (phase 1 install)` already
added `.ostk/file_cache.jsonl` to `.gitignore`. The cache file should NOT be
committed to the repo (it contains Anthropic file_ids tied to one account).
Only the populate script is committed.
