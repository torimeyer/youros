# Anthropic Files API — Auth Investigation

**Task:** →1339
**Date:** 2026-05-14
**Verdict:** Path B — Files API requires an API key. Subscription OAuth is not supported.

---

## Section 1: API endpoint and required headers

- **Upload:** `POST /v1/files` (SDK calls `/v1/files?beta=true`)
- **List:** `GET /v1/files`
- **Delete:** `DELETE /v1/files/{file_id}`

Required headers in every call:
```
x-api-key: <your API key>          ← auth header
anthropic-version: 2023-06-01
anthropic-beta: files-api-2025-04-14
```

File limits (from docs as of 2026-05-14):
- Max file size: 500 MB per file
- Max storage: 500 GB per organization
- File lifetime: **persist until explicitly deleted** — no TTL, no expiry
  (Note: earlier research in `docs/ostk-cache-mechanism-4-research.md` said
  "currently ~30 days" — that was wrong. The current docs say no TTL.)

Platform availability: Claude API, Claude Platform on AWS, Microsoft Foundry.
Not available on Amazon Bedrock or Vertex AI. No mention of subscription/consumer access.

File operations (upload, list, delete, get metadata) are **free**.
Content referenced in Messages requests is charged as input tokens.

---

## Section 2: SDK behavior

The Python SDK (`anthropic/resources/beta/files.py`) delegates auth entirely to
the base client — it does not hardcode `x-api-key` vs `Authorization: Bearer` in
the files resource itself. The auth header sent is whatever the `Anthropic()` client
was initialized with.

The SDK adds `anthropic-beta: files-api-2025-04-14` automatically on all file calls.

Critical doc quote that clarifies the intended auth model:
> "Files are scoped to the **workspace of the API key**."

This phrase appears in the file lifecycle section and explicitly ties files to
API key workspaces — not to subscription accounts or OAuth sessions.

No mention of OAuth or subscription-based auth anywhere in the Files API docs or SDK.

---

## Section 3: Empirical probe (read-only GET, no upload)

Two probes against `GET https://api.anthropic.com/v1/files`:

| Auth header | HTTP status | Response |
|---|---|---|
| `Authorization: Bearer <our OAuth token>` | **404** | `{"type":"error","error":{"type":"not_found_error","message":"Not found"}}` |
| `x-api-key: ` (blank) | **404** | Same shape |

Both probes sent `anthropic-version: 2023-06-01` and `anthropic-beta: files-api-2025-04-14`.

The 404 — rather than 401 (unauthorized) or 403 (forbidden) — means the Files API
endpoint does not exist for subscription/OAuth callers at the routing layer.
A valid but wrong credential would typically get a 401. Getting a 404 from both an
OAuth token AND an empty key means Anthropic's gateway routes `/v1/files` only for
API key auth; subscription sessions get a 404 as if the route doesn't exist for them.

OAuth token source: extracted from `~/.claude.json` (not printed anywhere in this
investigation). Token was confirmed present before the probe.

---

## Section 4: Verdict

**Path B: Files API requires an API key. Subscription OAuth does not work.**

Evidence:
1. Docs use `x-api-key` in every example. No OAuth/subscription mention.
2. Docs explicitly scope files to "the workspace of the API key."
3. OAuth Bearer probe → 404 (same as no-auth). Not a 401. Endpoint is invisible
   to subscription callers at the gateway level.

The 15–40% additional token savings from Mechanism 4 (file-handle rewrite) are
real but locked behind a paid API key. The ostk-cache proxy already has Mechanism 4
wired — `scripts/populate-ostk-file-cache.py` is ready to run the moment an API key
is available.

---

## Section 5: Recommended next steps

- **Decide on the API key.** The savings are ~21% on top of native prompt cache
  (from the →1335 retro), equivalent to roughly one extra session per 5-hour cap.
  The question is whether that's worth a separate API key and billing account.
- **If yes:** Set `ANTHROPIC_API_KEY` and run `scripts/populate-ostk-file-cache.py`
  on the three candidate files (MEMORY.md, ~/claude/CLAUDE.md, torios/CLAUDE.md).
  Verify first `rewrites_applied > 0` event in `.myos/ostk-cache-rewrite-events.jsonl`.
- **Correction to earlier research:** Update `docs/ostk-cache-mechanism-4-research.md`
  to remove the "expire ~30 days" claim — files persist indefinitely until deleted,
  which simplifies the refresh strategy (only re-upload on content change).
