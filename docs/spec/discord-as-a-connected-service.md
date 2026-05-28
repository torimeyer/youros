---
title: Discord as a connected service
status: draft
needle: →1706
created_at: 2026-05-28
---

## Problem

Tori's work doesn't live only in email and Slack. Teams that use Discord for
coordination, support, or community channels generate decisions, action items,
and follow-ups inside those channels, and today there is no way to surface
any of it in yourOS.

Concretely:

- A Discord message flagged as "needs action" has nowhere to go. It either
  gets lost in the channel or requires a manual copy-paste into a task.
- Tori can't see Discord messages alongside her Gmail and Slack feeds without
  context-switching to the Discord app.
- There's no way to turn a Discord message into a tracked task (needle) from
  inside yourOS.

The user need is the same one Slack already solves: read messages where work
happens, flag the ones that matter, and convert them to tasks without leaving
the tool.

## Goals

- [ ] Tori can connect a Discord bot to one or more servers (guilds) from
      Settings, using the same OAuth-style flow as Slack and GitHub.
- [ ] After connecting, she can browse the text channels she has access to
      and read recent messages in any channel.
- [ ] She can flag a Discord message as a follow-up, storing it in a
      persistent list the same way Slack follow-ups work.
- [ ] She can promote a Discord message to a tracked task (needle), pre-filled
      with the message text and a link back to the original.
- [ ] Connection status (connected / disconnected, guild name) is visible on
      the Settings page and reflected immediately after connect or disconnect.

## Non-goals (v1)

- **Posting messages from yourOS to Discord.** Read + flag first; write is a
  v2 concern once the read path is proven.
- **DM (direct message) support.** Guild text channels only. DMs require a
  different permission scope and raise privacy questions.
- **Slash command or interaction handlers.** Discord bot interactions
  (buttons, modals, `/commands`) are a distinct surface. Out of scope.
- **Real-time push (Gateway/WebSocket).** Discord provides a real-time
  Gateway API, but polling the REST API is sufficient for the follow-up use
  case and avoids a persistent connection. Gateway can be layered in later.
- **Multi-guild message search.** Discord's REST API does not expose
  cross-guild full-text search the way Slack does. Per-channel fetch only.
- **Notification forwarding.** Not relaying Discord @mentions or DMs to
  yourOS notifications in v1.

## Architecture overview

### OAuth and bot token flow

Discord's authorization model differs from Slack's:

1. **Bot app creation** (one-time, by Tori in the Discord Developer Portal):
   creates a bot application and generates a bot token + client ID/secret.

2. **Bot invite URL**: Tori visits a generated URL
   (`https://discord.com/api/oauth2/authorize?client_id=...&scope=bot&permissions=...`)
   which adds the bot to a chosen guild. This is distinct from the OAuth2
   user-identity flow.

3. **OAuth2 user token** (optional for v1): a separate
   `https://discord.com/oauth2/authorize` flow can obtain a user-scoped token
   identifying which guilds the user belongs to. Useful for listing guilds
   without relying on the bot being in them. See Open questions #1.

4. **Token storage**: bot token at `~/.myos/discord_token.json`; if we later
   support multiple bot apps or guild-scoped credentials, migrate to
   `~/.myos/discord_guilds/{guild_id}.json` matching the multi-workspace
   pattern in `api/services/slack.py`.

5. **Circuit breaker**: identical to Slack's implementation in
   `api/services/slack.py` — 2 consecutive failures trip a 5-minute cooldown
   so page loads are not blocked by Discord API instability.

### Message ingestion path

```
GET /discord/channels              → lists text channels the bot can see
GET /discord/messages/{channel_id} → fetches recent messages (REST, paginated)
POST /discord/followups            → stores a flagged message in ~/.myos/discord_followups.json
POST /discord/triage/promote       → creates a task (needle) from a message
```

All reads are REST pull. No Gateway connection in v1. The frontend polls
`/discord/status` on the same interval as `/slack/status`, served from the
existing `connections_cache` TTL layer.

### Where data lands

| Data | Path | Notes |
|---|---|---|
| Bot token | `~/.myos/discord_token.json` | Outside the repo, never committed |
| Follow-ups | `~/.myos/discord_followups.json` | Survives `git pull`; matches the location pattern for other user data |
| Tasks/needles | Written via `routers/tasks.py:create_task` | No new storage; same path as Slack promotes |

## Key surfaces

### Backend

| File | Role |
|---|---|
| `api/services/discord.py` | Bot token management, `is_connected()`, `list_guilds()`, `list_channels()`, `fetch_messages()`, `exchange_code()`, circuit breaker — mirrors `services/slack.py` |
| `api/routers/discord.py` | FastAPI router: `/discord/auth`, `/discord/callback`, `/discord/status`, `/discord/guilds`, `/discord/channels`, `/discord/messages/{channel_id}`, `/discord/followups`, `/discord/followups/{id}`, `/discord/triage/promote`, `/discord/disconnect`, `/discord/credentials` |
| `api/services/discord_followups.py` | Persistent follow-up CRUD (`~/.myos/discord_followups.json`); mirrors `services/slack_followups.py` |
| `api/main.py` | Register `discord.router` with `prefix="/api"` alongside the existing `slack.router` |

The router shape mirrors `routers/slack.py` exactly: `HTTPException` with
plain-language `detail` strings, TTL-cached `/status` via `connections_cache`,
and a `/credentials` endpoint so Tori can configure Client ID/Secret from
Settings without editing environment variables.

### Frontend

| Surface | What it shows |
|---|---|
| Settings > Connected Tools | Discord connection card: bot invite link, connection status dot, guild name, disconnect button. Mirrors the Slack card. |
| `/discord` route (new page) | Channel list sidebar + message feed, follow-up flag button per message, "Promote to task" button. Mirrors `/slack`. |
| Sidebar | Discord icon entry behind the same feature-flag mechanism as Slack (`system_features` toggle in Settings). |

### Events bus

No new event types needed for v1. `/discord/status` is polled by the frontend
on the same interval as other connection statuses. If we later want push
updates (e.g., a "new follow-up" badge increment), `notifications_events.py`
is the right place to emit a `discord.followup_added` event.

## Acceptance criteria

- [ ] Visiting Settings > Connected Tools shows a Discord card with a "Connect
      Discord bot" button that opens the bot invite URL in a new tab.
- [ ] After the user adds the bot to a guild and the callback completes,
      `GET /discord/status` returns `{ connected: true, guild_name: "..." }`.
- [ ] `GET /discord/channels` returns text channels the bot can see; the
      `/discord` page renders them in a scrollable list.
- [ ] `GET /discord/messages/{channel_id}` returns the 50 most recent messages;
      they render in the message feed with author name, text, and timestamp.
- [ ] Clicking "Flag follow-up" calls `POST /discord/followups` and the
      message appears in a follow-up list; `DELETE /discord/followups/{id}`
      removes it.
- [ ] Clicking "Promote to task" calls `POST /discord/triage/promote`, creates
      a needle, and the frontend shows a confirmation with the new task title
      and a link to the task.
- [ ] `DELETE /discord/disconnect` removes `~/.myos/discord_token.json` and
      `GET /discord/status` returns `{ connected: false }`.
- [ ] When the Discord API returns errors on 2 consecutive requests, the
      circuit breaker trips and subsequent calls return a plain-language error
      ("Discord is temporarily unavailable, try again shortly") without a
      stack trace reaching the UI.

## Open questions

1. **Bot invite vs. OAuth2 user token**: do we need the OAuth2 user-identity
   flow in v1, or is the bot invite URL sufficient? The bot token alone can
   list and read channels the bot is in. The user token is only needed to list
   which guilds the user belongs to (regardless of where the bot was added).
   Decision affects auth flow complexity and whether we need a client secret.

2. **Bot permissions bitmask**: which Discord permission bits do we request on
   the invite URL? Minimum is `View Channels` (1024) + `Read Message History`
   (65536) = 66560. If we add send in v2, add `Send Messages` (2048). Confirm
   the exact bitmask before building so the invite URL is correct from day one.

3. **Bot token vs. full OAuth app**: Slack requires a full OAuth app (client
   ID + secret) because it uses a user-facing consent screen. Discord's
   bot-only flow needs only the bot token. Do we want the full OAuth2 consent
   screen (so Tori can connect any guild she belongs to), or a simpler
   "paste your bot token" flow for personal use? Simpler is faster to ship;
   OAuth is more polished.

4. **Message content rendering**: Discord messages can include markdown, emoji,
   user mentions (`<@USER_ID>`), channel mentions (`<#CHANNEL_ID>`), and
   embeds. How much do we resolve in v1? Showing raw API output will display
   `<@123456>` instead of `@username`. A minimal resolver (replace `<@id>`
   with cached username) may be necessary for the UI to be readable.

5. **Rate limits**: the Discord message history endpoint allows 5 requests per
   second per channel. At what interval does the frontend poll
   `GET /discord/messages/{channel_id}`? We need to confirm the polling
   cadence won't trip rate limits when the user has multiple channels open
   simultaneously.
