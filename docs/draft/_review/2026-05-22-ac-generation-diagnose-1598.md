---
title: Diagnose AC generation hang (→1598)
date: 2026-05-22
Task: "→1598"
---

## Symptom

When Tori clicks the "User memory store improvements" draft spec, a spinner
labeled "Generating acceptance criteria..." appears and never resolves.

## Root cause

The spinner at `app/src/pages/Specs.tsx:1345` is a **static conditional**,
not tied to any running fetch or background job:

```tsx
{doc.status === "draft" && !(doc.acceptance_criteria?.length) && (
  <span ... animate-spin /> Generating acceptance criteria...
)}
```

It shows whenever a draft exists with no server-side `acceptance_criteria`.
Nothing ever populates that field for this draft, so the spinner never stops.

The draft `docs/draft/user-memory-store-improvements.md` was created via
`ostk doc draft` CLI directly, bypassing `POST /specs/draft`. The API
endpoint (api/routers/specs.py:533) calls Anthropic to generate AC and
writes a placeholder if that fails. The CLI does neither — the file ended up
with only 5 lines of frontmatter and no body.

There is no standalone "generate AC" endpoint. No polling. No background job.
The spinner is permanently stuck.

## Secondary inconsistency

The "Next step: promote" message at line 1337 only checks
`doc.acceptance_criteria?.length` (server field). The Promote button at
line 1398 checks BOTH server AC and parsed `- [ ]` lines from the body
(`parsedAc`). So: a draft with body-only checkboxes shows the spinner
(lying) while the Promote button is actually enabled (correct). The `criteria`
local variable computed at line 1194 captures parsed AC but isn't used by
the spinner condition.

## Decision: REMOVE the spinner, replace with honest message

Reasons:
1. The spinner actively misleads — it says "Generating..." but nothing generates
2. No polling, no endpoint, no way it could resolve on its own
3. Implementing real background generation requires a new endpoint + poll loop
   (higher blast radius, depends on AI API availability)
4. An honest "no AC yet" message unblocks the user: they know to add `- [ ]` lines

## Fix scope

1. `app/src/pages/Specs.tsx` lines 1337-1354: replace both the "Next step"
   and spinner blocks with a single condition that uses `criteria` (already
   computed at line 1194) to cover both server AC and parsed body AC.
2. `docs/draft/user-memory-store-improvements.md`: add the placeholder AC text
   that `POST /specs/draft` would have written (api/routers/specs.py:619-626),
   so the stuck draft becomes promotable immediately.
3. `app/src/pages/Specs.test.tsx`: add a test asserting the honest message
   appears (testid: `no-criteria-indicator`) and NOT the spinner text.
