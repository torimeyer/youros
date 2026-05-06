# nr-enterprise overlay: maintenance model

This document explains how the `main` and `nr-enterprise` branches relate to each other, what keeps them from drifting apart, and what to do when things need attention.

## Why two branches?

`main` is the generic, publicly available version of this project. It contains no vendor-specific code and is meant to work for anyone.

`nr-enterprise` is a separate long-running branch that sits on top of `main` and adds integration with New Relic's enterprise platform. It is maintained as an overlay: everything in `main` is also in `nr-enterprise`, plus a small set of NR-specific additions.

This separation is a hard rule. No New Relic code, configuration, imports, or references ever go into `main`. If you are unsure whether something belongs in `main`, it belongs in `nr-enterprise`.

## The files involved

### NR-only files (live only on nr-enterprise)

These files exist only on `nr-enterprise`. They will never appear on `main`.

| File | Purpose |
|------|---------|
| `install-nr.sh` | Installation helper for NR environments |
| `settings.nr-default.json` | Default settings for NR deployments |
| `api/services/nr_enterprise.py` | Core NR integration service |
| `api/services/auth_extensions.py` | Auth extensions for NR SSO |
| `app/src/components/EnterpriseConnectCard.tsx` | UI component for NR connection |
| `api/tests/test_nr_enterprise.py` | Tests for the NR integration |

### Delta files (changed on both branches)

These 4 files exist on `main` but have been modified on `nr-enterprise` to wire in the NR features. Any change to these files on `main` is a potential future conflict when `nr-enterprise` is next rebased.

| File | What changed on nr-enterprise |
|------|------------------------------|
| `api/main.py` | NR middleware and router registration |
| `api/services/claude_code_provider.py` | NR-aware provider logic |
| `api/routers/settings.py` | NR settings endpoints |
| `app/src/components/OnboardingWizard.tsx` | Enterprise connection step in onboarding |

## How conflicts happen

`nr-enterprise` is rebased onto `main` periodically. When anyone on `main` edits one of the 4 delta files, that change sits quietly until the next rebase, at which point it may conflict with the NR-specific edits on `nr-enterprise`.

The earlier we know about this, the cheaper it is to fix. That is what the automation below is for.

## Automation

### Weekly rebase check (every Monday)

A GitHub Actions workflow runs every Monday at 14:00 UTC. It:

1. Tries to rebase `nr-enterprise` onto the latest `main` in a throwaway workspace.
2. Runs all tests (both the NR-specific tests and the main test suite) on the rebased result.
3. If there is a conflict or a test failure, opens a GitHub issue titled `nr-enterprise rebase needs manual attention` with a `nr-enterprise-maintenance` label, showing exactly which files conflicted and whether they were expected delta-files or a surprise.
4. If everything is clean, closes that issue if one was open.

You can also trigger this check manually from the Actions tab at any time.

### PR overlay warning

When a pull request to `main` touches any of the 4 delta files, the workflow automatically:

1. Posts a comment on the PR (or updates an existing one) listing the affected files and linking to this document.
2. Adds the `affects-nr-enterprise` label to the PR.

This is a heads-up, not a blocker. The PR can still be merged. It just means the next rebase of `nr-enterprise` will need to account for that change.

## Running a manual rebase locally

Use this when you want to prepare a rebase of `nr-enterprise` onto the latest `main` before pushing it, or when the weekly check has opened an issue and you want to resolve it.

**Prerequisites:**

- You are on the `main` branch with a clean working tree.
- `main` is up to date with `origin/main` (run `git pull` first).

**Run:**

```bash
bash scripts/rebase-nr-enterprise.sh
```

The script will:

1. Fetch the latest `origin` state.
2. Create a throwaway git worktree and branch.
3. Attempt the rebase there, leaving your current branch untouched.
4. If there is a conflict, print which files are affected (grouped by expected vs. unexpected) and leave the worktree in place so you can resolve manually.
5. If the rebase is clean, run both the NR-specific tests and the full main test suite in the rebased worktree and print a summary.
6. Clean up the throwaway worktree on success.

**Flags:**

```
--cleanup   Remove all .tmp-nr-rebase-* worktrees and their branches.
--help      Show usage and the delta-file list.
```

The script never pushes anything. Pushing is always a human decision.

## What to do when the weekly check opens an issue

1. Pull the latest `main` and run `bash scripts/rebase-nr-enterprise.sh` locally.
2. The script will show exactly which files conflicted and whether they are expected delta-files or something new.
3. Check out `nr-enterprise`, resolve the conflicts, run the tests (`pytest api/tests/`), and push.
4. Re-run the weekly workflow manually from the Actions tab to confirm it passes. The workflow will close the issue automatically.

If the conflict is in an unexpected file (not one of the 4 delta files), that is a signal that someone on `nr-enterprise` has been editing files that should only be changed on `main`. Investigate before merging.

## Where the hard rule lives

The rule "nothing NR ever in main" is documented in two places:

- This file.
- The project memory entry at `memory/project_main_vs_nr_enterprise_split.md`.

Both must agree. If you are changing the scope of the overlay, update both.
