# →1427 Passive Commit Watcher — Implementation Plan

## What

Phase 3 of spec-auto-status: passive git-commit detection so specs flip to
**Building** even when nobody called `/claim` explicitly.

## Pieces

1. `scripts/ostk_post_commit_hook.sh` — bash post-commit hook. Reads latest
   commit message, greps for spec slugs (`~/.myos/specs/*.md` stems) and
   `→NNNN` task IDs, curls `POST /api/specs/<slug>/claim` with
   `source=passive`. Silent on failure, logs to `/tmp/ostk-post-commit.log`.

2. `scripts/install-post-commit-hook.sh` — copies the hook into
   `.git/hooks/post-commit` of the current repo. Intended for
   `scripts/dev-backend.sh` or manual invocation.

3. `api/services/spec_commit_scanner.py` — 60s cron fallback. Scans last 5
   commits in PROJECT_ROOT git repo via subprocess. Skips already-seen
   commit hashes. For matches, calls `_ensure_decomposed` and records into
   `_spec_claims` directly (no HTTP round-trip). Uses absolute spec paths as
   keys to match the claim endpoint's key format.

4. `main.py` — adds `schedule_spec_commit_scanner()` and calls it from
   `lifespan`, mirroring `schedule_merge_debt_watcher`.

5. Tests:
   - `api/tests/test_spec_commit_scanner.py` — unit tests mocking git log
   - `api/tests/test_specs.py` — add test for `POST /claim` with `source=passive`
