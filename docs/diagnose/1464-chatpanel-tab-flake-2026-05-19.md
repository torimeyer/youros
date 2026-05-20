# →1464 ChatPanel tab-state flake — root cause + fix

**Date:** 2026-05-19  
**File fixed:** `app/src/components/__tests__/ChatPanel.test.tsx`  
**Commit:** 60b05e6

## Symptom

`ChatPanel tabs > each tab has independent messages` failed on every vitest run:

```
Unable to find an element with the placeholder text of: /Message claude/i
```

## Root cause

Test line 145 used `screen.getByPlaceholderText(/Message claude/i)` to locate the chat input. The actual placeholder in `ChatPanel.tsx:3161` was changed at some point to `'Type / for commands, or just start chatting.'` and the test was never updated. No tab-state leakage, no timing race — purely a stale selector.

The input already carries `data-testid="chat-input"` (line 3155 of ChatPanel.tsx), which is the stable selector to use.

## Fix

One line in the test:

```diff
-    const input = screen.getByPlaceholderText(/Message claude/i)
+    const input = screen.getByTestId('chat-input')
```

## Verification

```
Test Files  1 passed (1)
      Tests  10 passed (10)
```
