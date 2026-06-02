---
status: spec
promoted_at: 2026-06-01T16:24:23Z
title: 'Chat code block overflow: enforce max-width and scroll within message bubbles'
---

## Problem

Code blocks in chat messages overflow the visible chat panel. When an assistant response contains a long line of code, the `<pre>` element expands past the bubble width and the code bleeds off-screen with no way to scroll. Users cannot read the full code.

## Root cause

The assistant message bubble uses `display: inline-block` inside an outer container with `max-w-[85%] w-fit`. When a `<pre>` with a long code line is rendered inside:

1. `<pre>` has no max-width, so it expands to full content width.
2. `inline-block` bubble sizes to the `<pre>` content, also becoming very wide.
3. Outer div is capped at `max-w-[85%]` but has default `overflow: visible`, so the bubble overflows visually.
4. `overflow-x-auto` on the `<pre>` never fires because the `<pre>` is not constrained and sees no overflow from its own perspective.

## Goals

- Code blocks inside chat bubbles are horizontally scrollable when the code is wider than the bubble.
- Long code lines do not overflow the chat panel boundary.
- Normal prose text flow is unaffected.

## Non-goals

- Resizing or wrapping code inside `<pre>` blocks (preserves whitespace semantics).
- Changes to the markdown rendering pipeline beyond `<pre>` and bubble container CSS.

## Acceptance criteria

- [ ] `<pre>` elements in assistant message bubbles have `max-w-full` applied so they are constrained by the parent width.
- [ ] The bubble container uses `block` display (not `inline-block`) so it fills the available width up to `max-w-[85%]`.
- [ ] `overflow-x-auto` on `<pre>` activates when code is wider than the bubble, producing a horizontal scrollbar.
- [ ] A vitest/RTL test confirms a simulated wide code block does not exceed the bubble's rendered width.
- [ ] Existing chat message layout tests continue to pass.
