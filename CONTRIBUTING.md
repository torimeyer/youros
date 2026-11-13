# Contributing

Thanks for helping out. Here is everything you need to get started.

## Setup

Clone the repo and run `./install.sh`. You need Python 3.11+ and Node 18+.

## Running locally

Start the backend and frontend in separate terminals:

```bash
scripts/dev-backend.sh
scripts/dev-frontend.sh
```

The frontend dev server opens at `https://localhost:3010` (or `http://localhost:3010` if local TLS certificates are not installed) and proxies `/api` and `/ws` to the backend on port `8000`. Do not run `npm run dev` directly; `scripts/dev-frontend.sh` configures the local proxy and certificates cleanly.

## Tests

```bash
# Frontend unit tests (Vitest)
scripts/run-vitest.sh

# Backend unit tests (pytest via the local virtualenv)
api/.venv/bin/pytest api/tests/

# TypeScript check
cd app && npx tsc -b

# Installer and release convention checks
./tests/test_install.sh
```

A pre-commit hook runs the relevant tests automatically on staged files when you commit. Install it once with `scripts/install-git-hooks.sh`.

## Pull requests

Keep PRs focused: one feature or fix per PR, with root-cause fixes and regression tests for any bug or new behavior.
