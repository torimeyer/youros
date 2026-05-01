# myOS

Your personal AI operating system. A local web app that gives you a workspace with AI chat, background agents, task management, and more. Built on [ostk](https://ostk.ai).

## How it works

myOS runs entirely on your computer. You open it in your browser at localhost, no account or login needed. All your data lives in `~/.myos/` and never leaves your machine unless you connect an integration like Gmail or Slack, in which case those requests go directly from your machine to the provider. myOS is not a cloud service.

Icons and fonts are loaded from Google Fonts (`fonts.googleapis.com`, `fonts.gstatic.com`). If your network blocks those hosts, the UI will still work but icons will render as text labels. See Troubleshooting below.

## Install

You need Python 3.9 or newer, Node 18 or newer, and git. Installing myOS does not require Homebrew. myOS runs on macOS and Linux.

### On macOS

```bash
git clone https://github.com/torimeyer/myos.git ~/myos
cd ~/myos
./install.sh
```

Python and Node come pre-installed on recent macOS, or you can download them from [python.org](https://python.org/downloads) and [nodejs.org](https://nodejs.org).

### On Linux

First install the basic tools with your system package manager. Pick the line for your Linux.

Ubuntu or Debian:

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates python3 python3-venv python3-pip nodejs npm
```

Fedora:

```bash
sudo dnf install -y git curl ca-certificates python3 python3-pip nodejs npm
```

Arch:

```bash
sudo pacman -Sy --noconfirm git curl ca-certificates python python-pip nodejs npm
```

Then clone and run the installer:

```bash
git clone https://github.com/torimeyer/myos.git ~/myos
cd ~/myos
./install.sh
```

If your distribution ships an older Node, install a newer one with nvm first:

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install 20
```

If you have SSH keys set up, you can also use `git clone git@github.com:torimeyer/myos.git ~/myos`.

## Updating

```bash
cd ~/myos
./update.sh
```

Your settings, chats, tasks, and labels live in `~/.myos/` (separate from the repo) and are never touched by updates. You can also verify this by running `ls -la ~/.myos/` before and after.

## Start

```bash
./start.sh
```

Or from anywhere after install:

```bash
myos
```

Your browser will open to http://localhost:8000.

For development (Vite hot reload on `app/` source), use the two-terminal setup instead:

```bash
scripts/dev-backend.sh      # backend on https://127.0.0.1:8000
scripts/dev-frontend.sh     # Vite on https://localhost:3010
```

## Stop

```bash
./stop.sh
```

`./start.sh` and `scripts/dev-backend.sh` both run a watchdog that restarts uvicorn on crash, so `Ctrl+C` alone can leave myOS running in the background. `stop.sh` kills the watchdog first, then uvicorn on port 8000 and Vite on port 3010.

If `stop.sh` isn't available, the manual equivalent is:

```bash
pkill -9 -f backend_watchdog.sh
lsof -ti tcp:8000 | xargs kill 2>/dev/null
lsof -ti tcp:3010 | xargs kill 2>/dev/null
```

## First Run

The setup wizard walks you through:
1. Enter your name
2. Name your OS (e.g. "MadisonOS", "AlexOS")
3. Pick a theme
4. Connect your AI provider (Anthropic or Google Gemini)
5. Turn a task you have been putting off into a plan

You can explore the dashboard, tasks, and ideas without an API key. Chat and agents need one.

## What's Inside

- **Dashboard** - home screen with smart focus card, day summary, session diff, quick launch, and labels
- **Chat** - talk to Claude or Gemini with tool access. Multiple chat tabs. Resizable panel. GIF search, emoji reactions, image paste.
- **Tasks** - create and manage work items with priorities. Labels for organization. Dependencies (blocks/needs). Task briefings. Health check for duplicates and missing info. Attributed commits.
- **Activity** - chronological feed of everything that happened, filtered by category (tasks, agents, ideas, system)
- **Agents** - spawn background AI agents. Permission requests (approve/deny). Delegation view for handing off tasks. Agent templates.
- **Ideas** - capture thoughts quickly. AI-powered clustering. Convert ideas into tasks.
- **Files** - browse your workspace with in-app file preview
- **Timeline** - visual view of tasks over time
- **Cost Tracking** - budget and spending by model and agent
- **Settings** - theme, accent color, default LLM, Google sign-in, MCP servers (ostk-managed and manual), feature toggles, export/import config
- **What's New** - release notes with badge for unseen updates
- **Search** - Cmd+K searches across all tasks and ideas by topic, plus quick commands

## ostk Integration

myOS covers the full ostk surface area:

| ostk Feature | myOS Integration |
|---|---|
| work add/close/list/next | Tasks page |
| work link/depends | Task dependencies |
| work near | Concept search (Cmd+K) |
| work activate | Task context briefings |
| work refine | Task health check |
| work radiate | Delegation view on Agents page |
| work hay/compile | Ideas page |
| thread create/list | Task groups |
| compounds | Smart focus card on Dashboard |
| os history | Activity page |
| os diff | "What Changed" on Dashboard |
| os clock | System status endpoint |
| doc draft/promote/decompose | Document planning (API ready) |
| commit --needle | Attributed commits on Tasks |
| trace | Attribution history on Tasks |
| secret set/get/list | Keychain-based key management in Settings |
| grant list/approve/deny | Permissions tab on Agents page |
| mcp list | MCP servers in Settings |
| kernel spawn/ps/reap | Agent management |

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Cmd+K | Command palette and search |
| Cmd+L | Toggle chat |
| Cmd+N | New task |
| Cmd+1-8 | Navigate pages |

## Google Sign-In Setup

To use Gemini through your Google account:

1. Create a project at [console.cloud.google.com](https://console.cloud.google.com)
2. Go to APIs and Services, then Credentials
3. Create an OAuth 2.0 Client ID (Web application)
4. Add redirect URI: `http://localhost:8000/api/auth/google/callback`
5. Copy client ID and secret into `api/.env` (see `api/.env.example`)

## Pre-commit Test Hook

myOS ships a lightweight git pre-commit hook that runs pytest, vitest, and `tsc -b` only on the files you staged. It catches broken tests at the commit that introduces them, instead of letting them pile up until release day. A typical commit stays under 30 seconds because only the relevant tests run.

Install once:

```bash
scripts/install-git-hooks.sh
```

Check status or uninstall:

```bash
scripts/install-git-hooks.sh --status
scripts/install-git-hooks.sh --uninstall
```

Bypass for a single commit (use sparingly):

```bash
git commit --no-verify
```

If you already have a local pre-commit hook, the installer preserves it as `.git/hooks/pre-commit.local` and runs it first, so nothing you had before is lost.

## Troubleshooting

**Icons render as text labels ("home", "checklist", "drag_indicator", etc.)**
The Material Symbols font failed to load. Usually a stale browser cache after an update — hard refresh with Cmd+Shift+R (Mac) or Ctrl+Shift+R (Linux). If it persists, rebuild the frontend: `cd app && npm run build`, then restart. If your browser's DevTools console shows `Content-Security-Policy` errors for `fonts.googleapis.com`, pull the latest — this was fixed by allowing Google Fonts in the CSP.

**`localhost:8000` still responds after you close the terminal**
`start.sh` runs a watchdog that respawns uvicorn on crash, so the app can keep running after you Ctrl+C. Use `./stop.sh` to shut down cleanly.

**Port 8000 is already in use**
Another process (often a leftover myOS from a previous session) is holding it. Run `./stop.sh`. If that doesn't clear it, `lsof -nP -iTCP:8000 -sTCP:LISTEN` shows what's there.

**401 on `/api/calendar/events` in the browser console**
Expected until you finish Google Sign-In. See "Google Sign-In Setup" above.

**Corporate network or VPN blocks Google Fonts**
Icons show as text because `fonts.googleapis.com` / `fonts.gstatic.com` are unreachable. The app is otherwise fully functional. If you need icons offline, open an issue — bundling the font locally is possible but not currently shipped.

## Roadmap

Open `roadmap.html` in your browser to see the three-year plan.
