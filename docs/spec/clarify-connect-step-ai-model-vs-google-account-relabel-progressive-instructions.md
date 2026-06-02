---
created_at: 2026-06-01T21:55:36Z
title: 'Clarify Connect step: AI model vs Google account, relabel, progressive instructions'
promoted_at: 2026-06-01T21:56:36Z
status: spec
---

## Problem

The onboarding **Connect your providers** step (and the matching Settings area) mixes two unrelated things under one "Google" heading and mislabels one of them:

1. The card labeled **"Google Workspace — <email> — Connected"** is actually the Google **account** connection for Drive/Calendar/Gmail data. For a personal `@gmail.com` it is wrongly called "Google Workspace" (the paid enterprise product). `GoogleWorkspaceSetupCard.tsx` hardcodes the label.
2. That card renders under **both** the Claude tab and the Gemini tab (it keys off `googleOAuthAvailable`, not the selected AI provider), so it looks like part of choosing Gemini.
3. The Gemini paths (Vertex AI vs AI Studio vs "my Gemini subscription") are unexplained, and a key truth is missing: a paid consumer **Gemini Advanced** subscription has **no API access**, so it can't be used. The screen "Recommends" the heavy Google Cloud / Vertex 3-step flow and buries the free AI Studio key.
4. The Cloud-setup instructions are always expanded, even though most personal users never need them.

## Goals

- Separate "which AI runs your chat" from "connect your Google apps" so they read as two distinct things.
- Label the Google account card by account type (personal vs Workspace) instead of always "Google Workspace".
- Lead the Gemini flow with the free, one-click AI Studio key for personal users, and state plainly that a paid Gemini app subscription can't be used.
- Hide the Google Cloud / Vertex setup steps behind an "Advanced" disclosure, collapsed by default.
- Apply the same to Settings for consistency.
- Frontend-only this pass (no backend changes); derive personal-vs-Workspace from the email the app already returns.

## Non-goals

- Backend hosted-domain detection for exact personal-vs-Workspace (`detect_vertex_gemini()` already computes `hosted_domain` but `/providers/detect` drops it). Deferred to a later pass.
- New providers, new auth flows, or changes to how keys are stored/validated.
- Changing the actual Vertex / AI Studio / Drive OAuth backends.

## Acceptance criteria

- [ ] Connect step shows a section titled "Which AI runs your chat" (Claude / Gemini tabs) distinct from a section "Connect your Google apps (optional)".
- [ ] The Google-apps section has a caption clarifying it connects Drive/Calendar/Gmail and does not change which AI runs chat.
- [ ] The Google account card renders once, identically under both AI tabs (no longer nested under the Gemini-provider heading).
- [ ] The Google account card reads "Google account" for a `@gmail.com` / `@googlemail.com` email and "Google Workspace" only for a custom domain.
- [ ] When Gemini is selected, the free AI Studio key is the lead/recommended path; the Cloud Console / Vertex steps are collapsed behind an "Advanced" disclosure (collapsed by default).
- [ ] A plain-language note states a paid Gemini app subscription (Gemini Advanced) can't be used as a key, and the free AI Studio key works with the same Google account.
- [ ] The same labels and framing appear in Settings.
- [ ] `scripts/run-vitest.sh` for `OnboardingWizard.test.tsx` and `Settings.test.tsx` passes; `tsc -b` is clean.
- [ ] Verified live in the running app: a personal Gmail shows "Google account", and the advanced Cloud steps are hidden until expanded.

## Design & references

Full implementation plan: `~/.claude/plans/this-is-confusing-to-stateless-waffle.md`.

Key files (all frontend): `app/src/components/OnboardingWizard.tsx` (ConnectStep ~1227–1448), `app/src/components/GoogleWorkspaceSetupCard.tsx` (relabel + rename to `GoogleAccountSetupCard`), `app/src/pages/Settings.tsx` (provider area ~416–475). Reuse: `detectedProvider` mapping (`OnboardingWizard.tsx:78-88`), `/drive/auth/status` `email` (`drive.py:143-148`), existing AI Studio key input and Vertex OAuth button.

Built in an isolated git worktree off `origin/main`; no other agent's worktree is touched; merged only after re-checking concurrent onboarding worktrees.
