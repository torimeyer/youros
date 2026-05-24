# Windows Support Findings (→865)

## Research summary

ostk ships a native Windows binary: `ostk-x86_64-pc-windows-msvc.zip`. Windows CI was added in v4.6.1.

## Recommendation: WSL2-first

Full native Windows support for myOS dev scripts would take 2–4 weeks. WSL2-first takes 1–2 days (this doc + runtime fixes). Windows users run the myOS web app under WSL2, get all app features, and the dev environment is documented as WSL2-only for now.

## What works on Windows today (after this PR)

- myOS web app (frontend — no changes needed)
- ostk CLI (native binary)
- Backend file-open, path resolution (fixed in this PR)
- Keyboard shortcuts in UI show Ctrl instead of ⌘ (fixed in this PR)
- iMessage returns a clear "not available" message instead of crashing (fixed in this PR)

## What requires WSL2

- Dev scripts (dev-backend.sh, dev-frontend.sh, run-vitest.sh) — bash/zsh + lsof
- Cert setup (setup-localhost-cert.sh) — macOS Keychain tools

## Status

→865 closed. Gating research done. Runtime fixes shipped in →864.
