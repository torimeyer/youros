# Spec relevance assessment: specs 10-13

Assessed 2026-07-02 by agent read-specs-10-11-12-13-and-asses-31f0de.

---

## Spec 10: spec-driven-development-learnings-enhance-the-torios-specs-feature-e1-e5

**Bucket: RELEVANT BUT NEEDS UPDATES**

**Justification:** The problem is real and the foundation is well-built, but E1's frontend piece is missing and E3's "flag" action semantics need verification.

**What's shipped:**
- E2: `api/services/spec_drift.py` lines 91-169 parse the `(test: path, covers: a.py)` inline AC annotation syntax. File-existence checks fire on `covers:` entries. Done.
- E4: `api/routers/specs.py:2048-2060` adds `fresh: bool = False` to `verify_spec`. With `fresh=True`, `_fresh_verify_spec` runs using only requirements + referenced tests, not the author's context. Done.
- E1 backend: `api/services/clarity_suggest.py` exists. `PATCH /specs/{path}/clarity` (specs.py:1360) and `POST /specs/{path}/clarity/suggest` (specs.py:1548) are live. `needs_clarity` field is set in review data (specs.py:735).
- E3 backend: `POST /specs/{path}/drift/reconcile` exists and is wired to `drift-reconcile-btn` in SpecReview.tsx. A second `drift-ack-btn` exists (SpecReview.tsx:263).
- E5: Correctly deferred per spec. Not built.

**What's missing / needs edits:**

1. **E1 frontend display is absent.** `app/src/components/SpecReview.tsx` has zero occurrences of "clarity" (grep count: 0). The clarity endpoint runs but nothing surfaces the result in the review panel. The AC says "shown as a new line item in the review panel." That line item does not exist yet. This is the only unmet AC in the file. The fix is to add a clarity row to SpecReview.tsx that reads the `needs_clarity` field from the review response and renders warnings about vague or untestable criteria.

2. **E3 "flag" action semantics need a second look.** SpecReview.tsx has `drift-reconcile-btn` (update spec to match code) and `drift-ack-btn`. The spec's AC for E3 says two distinct choices: "update spec to match code" and "flag it as a real change in requirements that needs attention." It's unclear whether `drift-ack-btn` satisfies "flag as requiring attention" or just dismisses the drift notice. Worth checking the handler code before calling this done.

3. `api/services/spec_lint.py` is absent (spec mentioned it as an option alongside extending `spec_constitution.py`). `clarity_suggest.py` serves that role instead. References in spec are accurate enough; no edit needed.

**No file paths in the spec need updating.** All referenced files exist at the stated paths.

---

## Spec 11: team-mode-plan

**Bucket: RELEVANT AS-IS**

**Justification:** The problem is fully real and the spec's file references are accurate against current code. This is a pure design document with no implementation started, which is correct given several open questions are still unresolved.

**Evidence the problem persists:**
- `api/routers/projects.py:17` still has `TORIOS_DIR = PROJECT_ROOT` as the implicit single workspace root. No `Workspace` type, no `/workspaces` endpoint.
- No team endpoints exist anywhere in `api/routers/` (grep for `workspace`, `require_role`, `current_user`, `member_role` returns nothing).
- `api/services/enterprise_store.py` exists (398 lines per spec notes) but zero routers import it. No live callers, exactly as the spec states.
- `app/src/pages/Projects.tsx` has a `Project` interface with no `workspace_id` field (spec's verified-against-codebase section is current).

**Open questions in the spec that must be resolved before build:**
- Viewer role demand (3-role vs Owner+Member)
- Per-user OAuth token storage (shared vs per-member app)
- Shared-file write-conflict strategy
- Atlassian/Jira token scoping
- `→1410` users/ directory decision
- `team.json` fate

These are correctly called out in the spec. No implementation should start until they are answered.

**No file paths need updating.** The codebase references in the spec (`projects.py:14`, `projects.py:26-52`, `enterprise_store.py`) are accurate.

---

## Spec 12: text-youros-task-and-command-ingest-from-your-phone

**Bucket: ALREADY SHIPPED**

**Status field says "complete" — verified.**

**AC items checked:**

- `api/services/text_bridge.py` exists. `is_trusted_sender()` (line 47), `classify_and_dispatch()` (line 77), iMessage poller, Telegram support all present.
- `api/services/telegram_channel.py` exists with getUpdates long-poll and `sendMessage`.
- `api/routers/text_bridge.py` exists with `GET /text-bridge/status`, `PATCH /text-bridge/config`.
- `api/tests/test_text_bridge.py` exists. Covers: trusted_contacts survival under partial update, trusted_sender gate, telegram polling integration, mocked `get_ai_client` routing.
- `api/main.py:1090-1097` wires `text_bridge.start()` in the lifespan function and registers the router at line 245.
- `app/src/pages/Settings.tsx:1921-1926` has the "Text yourOS" section with enable toggle, channel checkboxes, iMessage contact picker state (lines 201-205), Telegram token + chat id state, and `confirm_commands` bound to a control (lines 210-216).
- Trusted-sender gate: settings-based `trusted_contacts` is the primary path (text_bridge.py:65).
- The cursor advances per-message (text_bridge.py:255-257) and Telegram offset persists to `text_bridge_state.json`.
- Classification uses `ai_backend.get_ai_client()` (text_bridge.py:77), preserving subscription auth.

All AC items map to existing code. The status "complete" is accurate.

---

## Spec 13: user-memory-store-improvements

**Bucket: ALREADY SHIPPED**

**Status field says "ready" — should be updated to "complete."**

All 14 FR items are implemented in the codebase:

- FR-001: `api/tests/test_user_memory_store_e2e.py` exists. Tests cover "remember I prefer plain language" creating MEMORY.md, idempotency, and "I prefer plain language" without the "remember" prefix all triggering memory writes.
- FR-002: `api/services/memory_trigger.py:63-75` has forget patterns for "forget", "stop remembering", "never mind" with exclusion guards.
- FR-003: `api/services/user_memory_store.py:116` has `remove_bullet(query)`.
- FR-004: Covered by `test_user_memory_store.py` and `test_memory_trigger.py` (both exist).
- FR-005: Websocket events `memory_removed`, `memory_remove_ambiguous`, `memory_remove_failed` are referenced in `memory_trigger.py` dispatch.
- FR-006: `app/src/components/MemoryToast.tsx` has `ForgotToast` (line 43) with Undo button (line 72) and undo fetch (line 5).
- FR-007: `app/src/components/MemoryPill.tsx` polls `/memory/count` (line 15-16), renders the pill with bullet count (line 26).
- FR-008: `app/src/pages/Settings.tsx:2345` renders "edited manually" for bullets without provenance comments.
- FR-009: `user_memory_store.py:220` has `split_into_topic(bullet_text, topic_name)`. `rename_topic` at line 287.
- FR-010: `user_memory_store.py:34` defines `_OVERFLOW_LINES = 150`. `compute_overflow_status()` at line 177.
- FR-011: `Settings.tsx:2282-2293` has overflow banner, `Settings.tsx:2293` has "Suggest topics" button, `Settings.tsx:665` calls `/memory/user/split-topic`.
- FR-012: `api/services/chat_providers.py:1998-2000` has overflow-aware injection (index + matching topic files when overflow mode active).
- FR-013: `Settings.tsx:2279` surfaces hard-cap "Trim memory" message.
- FR-014: Frontend test files cover MemoryToast, MemoryPill, Settings Memory section (test files exist per earlier check).

**Recommended action:** Update spec status from "ready" to "complete" via `ostk doc` or direct file edit.
