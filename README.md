# myOS

Your personal AI operating system. A local web app that gives you a workspace with AI chat, background agents, task management, and more. Built on [ostk](https://ostk.ai).

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

## Roadmap

Open `roadmap.html` in your browser to see the three-year plan.
