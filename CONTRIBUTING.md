# Contributing

Thanks for helping out. Here is everything you need to get started.

## Setup

Clone the repo and run `./install.sh`. You need Python 3.9+ and Node 18+.

## Running locally

Start the backend and frontend in separate terminals:

```bash
scripts/dev-backend.sh
scripts/dev-frontend.sh
```

The app opens at http://localhost:5173 (frontend dev server) and the API runs on port 8000.

## Tests

```bash
# Frontend (Vitest)
scripts/run-vitest.sh

# Backend (pytest)
pytest api/tests/

# TypeScript check
cd app && npx tsc -b
```

A pre-commit hook runs the relevant tests automatically when you commit. Install it with `scripts/install-git-hooks.sh`.

## Pull requests

Keep PRs focused. One feature or fix per PR. Tests are expected for new functionality.
