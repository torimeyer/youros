# Release conventions

This document defines the canonical release process for myOS. Adhering to this sequence ensures consistency, automated verification, and high visibility of new features.

## The Release Checklist (ostk)

Follow these 8 steps for every release:

1. **Merge feature branches**: Ensure all work is merged into `main`.
2. **Bump version**: Update the version string in `app/package.json`.
3. **Draft Release Notes**:
   - Create `docs/releases/vX.Y.Z.md`.
   - Update `app/src/data/releaseNotes.ts`.
   - **Critical**: Do not start the body of the release notes with the version title (it's redundant). Focus ONLY on new features, never bug fixes.
4. **Verification (Smoke Test)**:
   - Run `./scripts/e2e_smoke.sh`.
   - Run backend tests: `api/.venv/bin/python3 -m pytest api/tests/`.
   - **Must be 100% green**.
5. **Commit**: `git add . && git commit -m "chore: release vX.Y.Z"`
6. **Tag**: `git tag vX.Y.Z`
7. **Push**: `git push origin main --tags`
   - *Note*: The pre-push hook runs `scripts/test_release_conventions.sh` automatically. If it fails, fix and retry.
8. **Publish**: `gh release create vX.Y.Z --title "myOS vX.Y.Z" --notes-file docs/releases/vX.Y.Z.md`
   - *Critical*: The release is not "shipped" until this step produces a public GitHub URL.

## Automated Checks

The `scripts/test_release_conventions.sh` script enforces these rules:

- **Version string consistency**: Tag format matches tarball format and URLs.
- **Release notes filenames**: Use strict semver (vN.N.N.md).
- **Notes presence**: Latest tag must have a corresponding notes file in `docs/releases/`.
- **In-app "What's New"**: Top entry must be dated within the last 14 days.
- **Tarball availability**: Every ostk tarball URL must return HTTP 200.
- **Privacy check**: No hardcoded personal paths (`/Users/torimeyer/`) in shipped code.
- **API Integrity**: No deprecated backend fields are being read by the frontend.

If any check fails, the push will be rejected. Fix the reported files and retry the push.
