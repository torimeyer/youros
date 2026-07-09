# Privacy: what stays on your laptop and what leaves

yourOS runs entirely on your own computer. This page answers the four questions a skeptical engineer would ask before installing. Every claim here was checked against the source code before being written down — file references are included so you can verify.

---

## 1. What lives on your laptop

All user data is stored in `~/.youros/` and never committed to the repo. The files and what they contain:

| File or folder | What it holds |
|---|---|
| `~/.youros/settings.json` | Theme, model preference, API keys, Gemini OAuth tokens |
| `~/.youros/github_token.json` | Your GitHub personal access token |
| `~/.youros/google_token.json` | Google Drive/Calendar/Gmail OAuth tokens |
| `~/.youros/google_credentials.json` | Google OAuth app credentials |
| `~/.youros/slack_workspaces/{team_id}.json` | Slack workspace tokens |
| `~/.youros/atlassian.json` | Atlassian site URL and email |
| `~/.youros/specs/` | Your spec files |
| `~/.youros/drafts/` | Draft documents |
| `~/.youros/upgrade_cache.json` | Cached version-check result (1-hour TTL) |

The repo directory holds code, configuration, and ostk state in `.ostk/`. It holds no user data. The pre-commit hook in this repo blocks any accidental commit of session content.

Code references: `api/services/youros_paths.py:30`, `api/services/settings_store.py:14`, `api/services/github.py:17`, `api/services/google_auth.py:26`, `api/services/slack.py:22`, `api/services/atlassian.py:32`.

---

## 2. What leaves your laptop

### Chat

When you send a message, the text goes to whichever model provider you picked in Settings:

- **Claude via Claude Code** (default): message is sent to the local Claude Code process, which uses your existing Claude subscription. No API key is stored by yourOS.
- **Anthropic API key**: message is sent to `api.anthropic.com` using the key you pasted.
- **Gemini API key or OAuth**: message is sent to Google's Gemini API.

yourOS does not see or store the content of your conversations beyond the local chat history in `~/.youros/`.

### Connected tools

When you ask yourOS to do something that requires a connected tool (read a GitHub issue, check your calendar, search Slack), the request goes to that tool's own API:

- GitHub: `api.github.com`
- Google (Drive, Calendar, Gmail): `*.googleapis.com`
- Slack: Slack's API
- Atlassian: `api.atlassian.com` or your own Atlassian site

These calls happen only when you actively use those tools. yourOS does not poll or sync in the background on its own schedule.

### Version check

When you open the Settings upgrade panel, yourOS checks `api.github.com/repos/os-tack/ostk.ai/releases/latest` to see if a newer version of ostk is available. The result is cached in `~/.youros/upgrade_cache.json` for one hour. No information about your installation is sent outbound in that request.

### UI fonts

The app loads Material Symbols icons from `fonts.googleapis.com` at startup. This is a standard font CDN request and carries no user data.

### Telemetry

**There is no telemetry.** No usage analytics, no crash reports, no error tracking. The codebase was searched for posthog, mixpanel, segment, amplitude, sentry, and datadog. None are present. The app's own in-app Privacy Policy page (`app/src/pages/PrivacyPolicy.tsx:74`) states the same.

---

## 3. Disconnecting a tool

Disconnecting a tool removes the stored credential immediately. Here is what actually happens for each provider, based on the code:

**GitHub** (`api/services/github.py:disconnect`): deletes `~/.youros/github_token.json` and clears the in-memory token cache. Subsequent API calls fail immediately. No call is made to GitHub to invalidate the token server-side; if you want that, revoke the PAT in your GitHub account settings.

**Slack** (`api/services/slack.py:disconnect`): deletes the per-workspace JSON file at `~/.youros/slack_workspaces/{team_id}.json`. Disconnecting all workspaces removes all files in that directory.

**Atlassian** (`api/services/atlassian.py:disconnect`): deletes `~/.youros/atlassian.json` and clears the matching entries in your macOS keychain (access token, refresh token, PAT). The keychain entries are zeroed out, not just removed, so the memory is overwritten.

**Google Drive/Calendar/Gmail** (`api/services/google_auth.py`): on token refresh failure Google marks the token revoked and sets a `revoked: true` flag in `~/.youros/google_token.json`. The code also calls `https://oauth2.googleapis.com/revoke?` to invalidate the token at Google's server.

**Google AI (Gemini OAuth)** (`api/routers/secrets.py:disconnect_google`): clears `gemini_oauth_access_token`, `gemini_oauth_refresh_token`, and `gemini_auth_method` to empty strings in `~/.youros/settings.json`.

Mid-session behavior: the credential is removed from disk immediately when you disconnect. The in-memory cache is also cleared at the same time. Any in-flight request that started before you disconnected may still complete (it already has the token in memory for that request), but no new requests will be made with the old credential.

---

## 4. Data flow at a glance

```
Your keyboard
     |
     v
yourOS frontend (localhost:3010)
     |
     v
yourOS backend (localhost:8000)
     |
     +---> model provider API (chat text only, when you send a message)
     |
     +---> connected tool APIs (when you use that tool)
     |       GitHub: api.github.com
     |       Google: *.googleapis.com
     |       Slack:  slack.com
     |       Jira:   api.atlassian.com / your-site.atlassian.net
     |
     +---> api.github.com/repos/os-tack/ostk.ai (version check only, hourly cache)
     |
     +---> fonts.googleapis.com (icon font, no user data)
     |
     x    (nowhere else)

Credentials: ~/.youros/          Disconnecting: file deleted + memory cleared
User data:   ~/.youros/          Telemetry: none
Repo:        code only
```

---

*Verified 2026-07-09 against the source. Checked files: `api/services/github.py`, `api/services/google_auth.py`, `api/services/slack.py`, `api/services/atlassian.py`, `api/services/settings_store.py`, `api/routers/secrets.py`, `api/services/upgrade_check.py`, `api/services/chat_providers.py`. Telemetry search covered all `.py`, `.ts`, `.tsx`, `.js` files.*
