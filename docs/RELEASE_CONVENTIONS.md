# Release conventions

Short checklist that runs before every release tag. The script
`scripts/test_release_conventions.sh` enforces these automatically.
Read this when you need to understand why a check failed.

## The rules

1. **Version strings stay consistent.** The GitHub tag looks like `v4.0.0`.
   The tarball filename looks like `ostk-4.0.0-...` (no leading `v`). The
   URL path uses the tag (with the `v`). If you ever change this, update
   `install.sh` and this doc together.

2. **Release notes file names use strict semver.** Every file under
   `docs/releases/` must be named `vN.N.N.md`, for example `v3.5.0.md`.
   Names like `v3.5.md` or `v3.5.0-beta.md` fail the check.

3. **The latest tag has release notes.** If the most recent git tag is
   `v3.5.0`, then `docs/releases/v3.5.0.md` must exist. You cannot ship
   a release without a notes file.

4. **The in-app What's New is fresh.** The top entry in
   `app/src/data/releaseNotes.ts` must be dated within the last 14 days.
   If it is older, you probably forgot to add an entry for the release
   you are cutting today.

5. **Every ostk tarball URL loads.** The script asks GitHub for the
   latest ostk version, builds the filename install.sh would request
   for each platform (Mac ARM, Mac Intel, Linux x86_64), and verifies
   each URL returns HTTP 200. This is the check that catches the bug
   where the filename and URL path disagree about the `v` prefix.

6. **No hardcoded personal paths.** The script scans git-tracked files
   under `api/` and `app/src/` for `/Users/torimeyer/`. Test files are
   allowed to have them. Shipped code is not.

7. **No deprecated fields leak.** When the backend removes a public
   response field, add its name to the `DEPRECATED_FIELDS` list inside
   the script. It then fails the release if the frontend still reads
   that field.

## How it runs

- **Manually:** `./scripts/test_release_conventions.sh`
- **Automatically:** the git pre-push hook runs it before every push,
  right after `tests/test_install.sh`.

## When a check fails

- Read the FAIL line. It names exactly what is wrong.
- Fix the file the check points at, not the check itself, unless the
  rule needs to evolve. If you change the rule, update this doc and
  write a note in the PR.
