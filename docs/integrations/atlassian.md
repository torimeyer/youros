# Connect Jira and Confluence to myOS

This guide covers connecting your Atlassian account, what to expect after the update shipped in May 2026, and how the connection status check works.

---

## What myOS can do with Jira and Confluence

Once connected, myOS can:

- Show your open Jira issues and let you comment, transition, or reassign them without leaving myOS.
- Browse recent Confluence pages and pull their content into context.
- Promote a Jira issue into a tracked task (needle) in one click.

---

## Connecting for the first time

1. Go to **Settings** in myOS and find the **Atlassian** section.
2. Click **Connect Jira and Confluence**.
3. An Atlassian sign-in page opens in your browser. Sign in and approve the permissions myOS requests.
4. You are redirected back to myOS. The connection card shows your email and site name when it worked.

myOS uses OAuth. It never sees your Atlassian password. Your token is stored locally at `~/.myos/atlassian_config.json`.

---

## If you were already connected before May 2026, you need to reconnect once

**Why:** In May 2026 myOS updated the permissions it requests during sign-in. Atlassian deprecated the old permission names in 2024, and their newer API endpoints now reject tokens that only have the old ones. Tokens issued before this update are missing the new permissions, so Jira and Confluence calls will start returning 401 (access denied) errors.

**What to do:**

1. Go to **Settings > Atlassian**.
2. Click **Disconnect**.
3. Click **Connect Jira and Confluence** and sign in again.

The whole flow takes about 30 seconds. After reconnecting your token includes the updated permissions and everything works as before.

**You only need to do this once.** Future updates will not require it unless permissions change again.

### What changed in the permission list

myOS previously requested broad permission names like `read:jira-work` and `write:jira-work`. Those names still work in older Atlassian APIs but are deprecated and rejected by newer v2 endpoints.

myOS now requests the granular equivalents alongside the legacy ones during the consent step:

| What it covers | New permission name |
|---|---|
| Read Jira issues | `read:issue:jira` |
| Read Jira comments | `read:comment:jira` |
| Post Jira comments | `write:comment:jira` |
| Read issue transitions | `read:issue.transition:jira` |
| Apply issue transitions | `write:issue.transition:jira` |
| Read Confluence pages | `read:page:confluence` |
| Read Confluence spaces | `read:space:confluence` |
| Edit Confluence pages | `write:page:confluence` |
| Post Confluence comments | `write:comment:confluence` |

myOS also requests `offline_access`, which is what lets Atlassian issue a refresh token. Without it, your token would expire every hour and myOS would have to ask you to sign in again constantly.

---

## How myOS detects a stale or expired token

The `/api/atlassian/status` endpoint now includes an `expired` field in its response. myOS checks this when you open the Atlassian section and when the connection card loads.

```json
{
  "connected": true,
  "email": "you@example.com",
  "site": "yourco.atlassian.net",
  "jira_url": "https://yourco.atlassian.net/jira",
  "confluence_url": "https://yourco.atlassian.net/wiki",
  "expired": false
}
```

When `expired` is `true`, myOS shows a **reconnect banner** on the Atlassian card before any Jira or Confluence call is made. This means you get a clear prompt to reconnect instead of seeing a confusing error in the middle of a task.

Under the hood, the status check makes a lightweight call to the Atlassian API to verify your token is still valid. If that call fails, `expired` is set to `true`. If myOS cannot reach Atlassian at all (e.g., you are offline), it leaves `expired` as `false` so the banner does not appear incorrectly.

---

## Troubleshooting

**I see a "reconnect" banner even though I just connected.**
The token probe failed. Check that your Atlassian site is reachable and that the account you used still has access to the site. Disconnect and reconnect to issue a fresh token.

**I get a 401 error when loading Jira issues.**
Your saved token is missing the updated permissions. Follow the disconnect and reconnect steps above.

**The connect button opens Atlassian but I get "access denied" after signing in.**
Your Atlassian administrator may have restricted third-party OAuth apps. Ask them to allow apps requesting the permissions listed in the table above, or to approve `myOS` in the Atlassian admin panel.

**I connected but myOS shows no Jira issues.**
Make sure you have issues assigned to you that are not in a "Done" state. myOS only shows open, assigned issues by default.
