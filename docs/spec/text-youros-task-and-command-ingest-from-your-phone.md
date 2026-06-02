---
promoted_at: 2026-06-01T00:11:25Z
title: Text yourOS - task and command ingest from your phone
status: spec
created_at: 2026-06-01T00:09:42Z
---

# Text yourOS — task & command ingest from your phone

## Problem

When you're away from your Mac you can't get a thought into myOS. You want to text a task or a
command from your phone and have myOS handle it: drop it into chat, file it as a task, or run it,
then text you back what it did. The catch is architectural: myOS is a **localhost-only FastAPI
app on your Mac**. It cannot receive public SMS/Twilio webhooks, because those require the Mac to
be reachable from the open internet. So the channel has to be something that works
**outbound-only or fully local**, and the design has to respect a live P0 where the backend
wedges under event-loop load.

## Goals

- Let you text a natural-language message from your phone and have myOS classify it and act:
  **chat**, **create a task**, or **run a command / spawn an agent**.
- Reply on the same channel with exactly what happened (task title, command result, agent name),
  so every action stays visible and reversible.
- Support two channels that fit the local-only constraint: **iMessage** (local) and **Telegram**
  (outbound long-poll). Carrier SMS is explicitly out of scope.
- Only act on messages from **one dedicated, user-chosen contact/thread** (chat.db holds
  everyone's messages, so a trusted-sender gate is mandatory).
- Make "ask before running commands" a **stored preference**, set on first use via a text-back
  question and remembered, also editable in Settings.
- Reuse existing subsystems (iMessage I/O, the tool-dispatch engine, the auth-aware Claude
  client, settings storage) rather than building parallel implementations.

## Non-goals

- Carrier SMS / Twilio / any inbound public webhook (cannot reach a localhost backend).
- A new chat UI or a new page (extend Settings only).
- Multi-user / multi-contact routing (one trusted thread per channel).
- Changing the existing chat, task, or agent-spawn internals (reuse them unchanged).
- Running an always-on hot loop (must be off by default and self-gated — see the P0 constraint).

## Constraints

- **Live P0 `->2018` (backend wedges under event-loop/GIL load):** the poller must be **off by
  default**, return immediately when disabled, do **all** chat.db access via `asyncio.to_thread`,
  run on a modest interval (~12s), and honor the iMessage circuit breaker. Never a hot loop on
  the event loop.
- **Auth:** classification must go through the auth-aware client so it keeps the user's
  subscription instead of forcing API-key billing.
- **Privacy:** the trusted-sender gate is non-negotiable; untrusted senders are dropped silently.

## Design

### Flow (per inbound message)
```
 start_loop()  (self-gates; OFF by default), every ~12s per enabled channel via asyncio.to_thread:
 phone --text--> chat.db (iMessage) / getUpdates (Telegram)
   -> trusted-sender gate (drop if handle/chat_id != configured)
   -> classify_and_dispatch(text):
        client = ai_backend.get_ai_client()
        resp = await client.messages.create(tools=[create_task,run_command,spawn_agent], tool_choice="auto", ...)
        - tool_use block present -> execute_tool(name, input)   (command/agent path runs the confirm gate)
        - no tool_use (plain text) -> CHAT: append user+reply to chat history
   -> reply on same channel  -> advance cursor
```

### Components
- **`api/services/text_bridge.py` (NEW)** — `start_loop()` (self-gates on the enabled flag);
  `classify_and_dispatch(text)` using `ai_backend.get_ai_client()` + `.messages.create(...)` with
  the three schemas filtered from `tool_executor.TOOL_DEFINITIONS`; a `tool_use` block ->
  `execute_tool(...)`, no `tool_use` -> chat path (absence of a tool call *is* the chat signal);
  trusted-sender gate; confirm state machine; cursor + pending-confirmation persisted to
  `~/.myos/text_bridge_state.json` via `atomic_io`.
- **iMessage adapter (in text_bridge)** — reuse `imessage.py`: resolve the contact to a `chat_id`,
  poll `get_messages_sync` filtered to `is_from_me == 0` and `date > cursor`, reply with
  `reply_to_chat`. On non-macOS the channel is inactive (no crash), mirroring `_require_macos`.
- **`api/services/telegram_channel.py` (NEW)** — `getUpdates` long-poll + `sendMessage` against
  api.telegram.org with a bot token from settings (outbound HTTPS only), same trusted-id gate.
- **`api/routers/text_bridge.py` (NEW)** — `GET /api/text-bridge/status`,
  `PATCH /api/text-bridge/config`, `POST /api/text-bridge/test`; config under a `text_bridge`
  key via `SettingsStore`.
- **`api/main.py` (EDIT)** — include the router; in `lifespan` register
  `_keep(asyncio.create_task(text_bridge.start_loop()))`.
- **`app/src/pages/Settings.tsx` (EDIT)** — "Text yourOS" section: enable toggle, channel
  checkboxes, iMessage contact picker (reuse `/imessage/contacts/search`), Telegram token + chat
  id, and an "Ask before running commands" Always/Never control bound to `confirm_commands`.

### Confirm-before-command state machine
`text_bridge.confirm_commands` in `{ null, "always", "never" }`. While `null`, a command/agent
message is **held** and myOS texts back "Want me to ask before running commands like this? Reply
YES to confirm first, NO to just run them." — the next yes/no becomes the stored preference and is
applied to the held action. `"always"` -> texts "Run `<command>`? Reply YES." and waits;
`"never"` -> runs immediately. Tasks and chat are always immediate. Pending state lives in
`text_bridge_state.json` so a restart mid-confirmation doesn't lose or double-run it.

### Storage
- Config + Telegram token -> `~/.myos/settings.json` `text_bridge` key (via `SettingsStore`).
- Cursor + pending confirmation -> `~/.myos/text_bridge_state.json` (via `atomic_io`).

## Acceptance Criteria

- [ ] With the bridge **disabled** (default), `start_loop()` returns immediately and adds no
      measurable load; P0 `->2018` does not regress under a 1-agent spawn.
- [ ] In Settings, a "Text yourOS" section can enable the bridge, choose channels, pick a
      dedicated iMessage contact (via `/imessage/contacts/search`), enter a Telegram bot token +
      chat id, and set the "Ask before running commands" preference; all persist to
      `~/.myos/settings.json` under `text_bridge`.
- [ ] A message from the configured iMessage contact reading like "remind me to call the dentist
      tomorrow" creates a task (confirmed in `ostk work list`) and sends a reply naming the task.
- [ ] A message like "what's running?" is appended to chat history (visible in `ChatPanel`) and a
      chat reply is sent; no task/command is created.
- [ ] A message classified as a command (e.g. "run the smoke test") with `confirm_commands` unset
      triggers the first-run preference question; the answer persists and is honored on the next
      command; tasks/chat are never gated.
- [ ] Messages from any sender other than the configured contact/chat id are dropped (not
      classified, not acted on).
- [ ] The same end-to-end flow works over Telegram once a bot token + chat id are configured.
- [ ] Classification uses `ai_backend.get_ai_client()` (not a direct `anthropic.AsyncAnthropic`),
      so subscription auth is preserved.
- [ ] The cursor advances past processed messages and the same message is never processed twice
      across a poll cycle or a restart.
- [ ] `api/tests/test_text_bridge.py` covers: trusted-sender gate, classifier routing (task /
      command / chat via mocked `get_ai_client`), cursor advance/no-reprocess, and the confirm
      state machine (null->prompt->store->apply, always->prompt, never->run). Tests pass.
- [ ] Frontend: `scripts/run-vitest.sh` green for the Settings section; `tsc -b` clean.

## Files

- **Create:** `api/services/text_bridge.py`, `api/services/telegram_channel.py`,
  `api/routers/text_bridge.py`, `api/tests/test_text_bridge.py`.
- **Edit:** `api/main.py`, `app/src/pages/Settings.tsx`.
- **Reused unchanged:** `api/services/imessage.py`, `api/services/ai_backend.py`,
  `api/services/tool_executor.py`, `api/routers/chat.py` (history helpers),
  `api/services/settings_store.py`, `api/services/atomic_io.py`.

## Verified against the codebase (2026-05-31)

- `api/services/ai_backend.py`: `get_ai_client()` (141) returns an `anthropic.AsyncAnthropic` or
  a `ClaudeCliClient` (123) shim, both exposing `.messages.create(**kwargs)`; `resolve_ai_backend`
  (37). There is **no** `ai_backend.complete()` — classification uses `get_ai_client()` +
  `.messages.create(...)`, which also preserves subscription auth.
- `api/services/tool_executor.py`: `TOOL_DEFINITIONS` (39), `execute_tool` (609), `_run_command`
  (763), `_create_task` (878), `_spawn_agent` (1801).
- `api/services/imessage.py`: `get_messages_sync` (599), `send_message` (813), `reply_to_chat`
  (917), `_breaker_is_open` (49), `_apple_epoch_to_unix` (194); router `_require_macos` gate.
- `api/services/settings_store.py`: `SettingsStore` (122) `load/save/get/update`
  (131/160/165/168), `SETTINGS_PATH` (13) + `MYOS_HOME` (12); `atomic_io.py` present.
- `api/services/chat_ack_bot.py`: `start_for_running_agents` (590) — startup-loop precedent.
- `api/routers/chat.py`: chat history GET/PUT (750/756); `call_model` (666) is WebSocket-bound and
  intentionally not reused. `api/main.py` `lifespan` (45) + `_keep` background-loop pattern.
- Pre-design audit: `SmsInbox`/`SmsIngest`/`TwilioWebhook` CLEAR; `TextToTask` only matched
  unrelated in-app quick-add modals.

