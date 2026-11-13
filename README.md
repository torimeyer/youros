# yourOS

Your local-first personal AI operating system for macOS and Linux, built on [ostk](https://ostk.ai).

> **Looking for toriOS?** You are in the right place. **yourOS** began as **toriOS**, my personal AI workspace (used to build projects like Treehouse Hideaway). When you install yourOS, you name your own instance during setup (`toriOS`, `alexOS`, or whatever fits you).

## Why yourOS if you already use Claude Code or Gemini CLI?

Terminal coding tools do great work inside a single window, then lose the broader picture the moment that session closes. yourOS wraps **Claude Code** and **Gemini CLI** in a persistent local workspace powered by [ostk](https://ostk.ai):

- **Specs, tasks, and memory that carry across sessions:** Turn rough ideas into structured specs, break specs into dependency-linked tasks, and keep full context across restarts.
- **Supervise background agents in one place:** Dispatch agents in isolated git worktrees, watch live progress, approve permissions, and inspect commit and test receipts before merging.
- **Works with the subscriptions you already pay for:** Connect your local Claude Code subscription or Gemini CLI login in seconds with no API key required (or paste an Anthropic or Gemini API key if you prefer).

## How it works

yourOS runs entirely on your computer. You open it in your browser at `https://localhost:8000` with no cloud account or login wall.

All of your personal data (settings, chat history, specs, drafts, and local task state) lives in `~/.youros/`, outside the repository directory. Pulling updates never touches your data. Nothing leaves your machine except messages sent to the AI provider you selected and requests to external tools you explicitly connect and enable. See [PRIVACY.md](PRIVACY.md) for the full breakdown.

Icons and UI fonts load from Google Fonts (`fonts.googleapis.com` and `fonts.gstatic.com`). If your network blocks those hosts, the app still works and renders text labels in place of icons.

## Install

You need **Python 3.11 or newer**, **Node 18 or newer**, `curl`, and `git`. Installing yourOS does not require Homebrew. yourOS runs on macOS and Linux.

### On macOS

Make sure Python 3.11+ ([python.org](https://python.org/downloads)) and Node 18+ ([nodejs.org](https://nodejs.org)) are installed, then run:

```bash
git clone https://github.com/torimeyer/youros.git ~/youros
cd ~/youros
./install.sh
```

On macOS, `install.sh` sets up `ostk` (if not already installed), creates the Python virtual environment in `api/.venv`, builds the frontend in `app/`, seeds `~/.youros/settings.json` without overwriting existing settings, configures background `launchd` agents so the backend recovers automatically, and adds the `youros` and `youros-update` commands to your shell config.

### On Linux

First install the basic prerequisites with your system package manager.

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
git clone https://github.com/torimeyer/youros.git ~/youros
cd ~/youros
./install.sh
```

If your Linux distribution ships a Python older than 3.11 or Node older than 18, install newer versions first (for example with `pyenv` and `nvm`):

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install 22
```

For contributors who already have a GitHub SSH key set up: `git clone git@github.com:torimeyer/youros.git ~/youros`

### Subagent tracking in other repositories (optional)

When you spawn Claude Code subagents, yourOS can register them on its **Agents** page so you have one dashboard for in-flight AI work. By default, tracking only activates inside the `yourOS` repository itself. To track work in other repositories, pick whichever scope fits your workflow:

| Scope | Command | Behavior |
|---|---|---|
| **Per-repo** | `cd some-project && youros-track` | Enables persistent tracking in that repository by writing `.claude/settings.local.json`. Run `youros-track --remove` to reverse it. |
| **Per-session** | `cd some-project && youros-claude` | Launches a single tracked Claude Code session and cleans up on exit, leaving subsequent plain `claude` runs untracked. |
| **Machine-wide** | `./install.sh --with-claude-hooks` | Installs a hook in `~/.claude/hooks/register-agent.sh` so every Claude Code session on your machine reports to yourOS. `./uninstall.sh` removes it. |

All three options point at `~/.youros/hooks/register-agent.sh`, which `install.sh` stages automatically.

## Start, Stop, Update, and Uninstall

### Start

Open a new terminal window so your shell picks up the installed aliases, then run:

```bash
youros
```

Or directly from the repository directory:

```bash
./start.sh
```

Your browser opens automatically to `https://localhost:8000` (or `http://localhost:8000` if local TLS certificates were skipped).

For active frontend development with Vite hot reload, run the backend and frontend in separate terminals instead:

```bash
scripts/dev-backend.sh      # FastAPI backend on https://127.0.0.1:8000
scripts/dev-frontend.sh     # Vite dev server on https://localhost:3010
```

### Stop

```bash
./stop.sh
```

On macOS (`launchd`) and in dev mode (`backend_watchdog.sh`), a supervisor restarts `uvicorn` automatically if it exits unexpectedly, so pressing `Ctrl+C` alone can leave port `8000` running. `./stop.sh` unloads the supervisor cleanly before stopping ports `8000` and `3010`.

### Update

```bash
youros-update
```

Or from inside the repository:

```bash
cd ~/youros
./update.sh
```

`update.sh` pulls the latest code, refreshes Python and Node dependencies, rebuilds the frontend, and upgrades `ostk` when a newer release is available. Your data in `~/.youros/` is never modified by updates.

### Uninstall

```bash
./uninstall.sh
```

Stops running processes and removes build artifacts created by `install.sh` (`api/.venv`, `app/node_modules`, `app/dist`, `.mcp.json`, `launchd` plists, and the `youros`, `youros-update`, `youros-track`, and `youros-claude` shell aliases) while preserving `~/.youros/` so re-running `./install.sh` restores your workspace immediately.

To wipe everything including `~/.youros/` (permanently deletes local user data), `ostk`, and the localhost certificate:

```bash
./uninstall.sh --purge
```

Prompts before each destructive step (pass `--yes` to skip confirmation prompts). Remove the cloned repository folder itself with `rm -rf ~/youros` when finished.

## First Run

Opening yourOS for the first time launches a three-step setup wizard:

1. **Connect:** Automatically detects local **Claude Code** and **Gemini CLI** subscriptions, lets you optionally paste Anthropic or Gemini API keys, and checks `ostk` kernel and `HUMANFILE` signature health.
2. **Tracking:** Lets you choose whether background agent tracking stays scoped to `yourOS`, per-project (`youros-track`), or machine-wide.
3. **Personalize:** Sets your display name, names your OS instance (for example `toriOS` or `alexOS`), and picks Light or Dark mode.

## What's Inside

- **Dashboard:** Smart focus card, daily summary, session diff ("What Changed"), and quick launch actions.
- **Chat:** Multi-tab chat with Claude and Gemini, streaming tool execution, file and image attachments, and quick task creation.
- **Specs:** Draft specs, promote approved designs, and decompose specs directly into dependency-ordered tasks.
- **Tasks:** Priorities (`P0` to `P3`), labels, dependency graphs (`blocks` / `needs`), pre-flight context briefings, duplicate health checks, and attributed git commits.
- **Agents:** Spawn background agents in isolated git worktrees, monitor live progress, approve or deny permission requests, delegate tasks, and edit `Agentfile` recipes.
- **Ideas:** Fast thought capture with automatic topic clustering and one-click promotion into tasks.
- **Activity & Timeline:** Chronological history of agent runs, commits, specs, and task milestones.
- **Files:** Browse workspace files with in-app markdown and code previews.
- **Breakroom:** Lightweight arcade mini-games tucked into the bottom bar while background agents finish a test suite or build.
- **Cost Tracking:** Token and spend visibility broken down by model and agent run.
- **Settings:** Theme and accent customization, model defaults, provider credentials, MCP servers, backup/restore, and integration toggles.

## ostk Integration

yourOS is built directly on [ostk](https://ostk.ai), a local coordination kernel for AI agents and human operators:

| ostk Command | yourOS Surface |
|---|---|
| `ostk work add / close / list / pull / edit` | Tasks page |
| `ostk work link / unlink` | Task dependency graph (`blocks` / `needs`) |
| `ostk work activate` | Task pre-flight context briefings |
| `ostk work show --radiate` | Delegation view on Agents page |
| `ostk work capture / triage` | Ideas capture and clustering |
| `ostk work compounds` | Smart focus card on Dashboard |
| `ostk doc draft / promote / decompose` | Specs workflow |
| `ostk agentfile` | Agent templates and Agentfile editor |
| `ostk spawn / interact / session` | Background agent supervision |
| `ostk handoff / context-store / context-restore` | Session handoffs and tiered context memory |
| `ostk commit` / `ostk trace` | Attributed git commits and lineage history |
| `ostk search / recall` | Command palette (`Cmd+K`) and memory search |
| `ostk session-history / show` | Activity feed and system status |
| `ostk bail` | Portable workspace backup and restore (`scripts/youros-bail.sh`) |
| `ostk boot / inspect` | Runtime health and `HUMANFILE` trust checks |

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Cmd+K` | Command palette and workspace search |
| `Cmd+L` | Toggle chat panel |
| `Cmd+N` | Create a new task |
| `Cmd+1` to `Cmd+8` | Jump between workspace pages |

## Connecting AI Providers and Integrations

- **Claude Code & Gemini CLI (zero API key):** Install `@anthropic-ai/claude-code` or `@google/gemini-cli` and sign in once from your terminal. yourOS detects active CLI sessions automatically during setup and in **Settings**.
- **API Keys:** You can paste an Anthropic API key or Gemini API key directly in **Settings** (or copy `api/.env.example` to `api/.env`).
- **Google Cloud / Vertex AI:** If you have `gcloud auth application-default login` configured locally, Gemini calls can authenticate through Application Default Credentials without a separate API key.
- **Google OAuth (optional):** To sign in with your own Google OAuth client, create a Web Application OAuth 2.0 Client ID in [Google Cloud Console](https://console.cloud.google.com/apis/credentials), add `http://localhost:8000/api/auth/google/callback` as an authorized redirect URI, and add `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` to `api/.env` (see `api/.env.example`) or upload credentials in **Settings**.
- **Atlassian (Jira & Confluence, optional):** Connect via OAuth in **Settings > Atlassian**. Setup and permission details live in [docs/integrations/atlassian.md](docs/integrations/atlassian.md).

## Pre-commit Test Hook

Contributors can install a fast git pre-commit hook that runs `pytest`, `vitest`, and `tsc -b` only against staged files:

```bash
scripts/install-git-hooks.sh
scripts/install-git-hooks.sh --status
scripts/install-git-hooks.sh --uninstall
```

Any existing local pre-commit hook is preserved as `.git/hooks/pre-commit.local` and executed first.

## Troubleshooting

Run `scripts/youros-doctor.sh` for an automated health check with exact fix commands, or see [docs/recovery.md](docs/recovery.md) to back up and restore your workspace with `scripts/youros-bail.sh`.

- **Icons render as text labels (`home`, `checklist`, `drag_indicator`):** The Material Symbols font failed to load, usually from a stale browser cache after an update. Hard refresh with `Cmd+Shift+R` (macOS) or `Ctrl+Shift+R` (Linux). If needed, rebuild the frontend with `cd app && npm run build` and restart.
- **`localhost:8000` still responds after closing the terminal:** `launchd` (macOS) or `backend_watchdog.sh` keeps the backend alive in the background. Run `./stop.sh` to shut everything down cleanly.
- **Port 8000 is already in use:** Run `./stop.sh`. If another process is still bound to port 8000, run `lsof -nP -iTCP:8000 -sTCP:LISTEN` to inspect it.
- **`ostk` reports an invalid `HUMANFILE` signature:** Run `ostk boot` in your terminal to inspect trust status and re-sign your `HUMANFILE` after local edits, then click **Re-check** in Onboarding or Settings.

## Roadmap

Open `roadmap.html` in your browser to view the multi-year direction.

## Contributing: why some internal identifiers still say "myos"

Before becoming **toriOS** and open-sourcing as **yourOS**, early internal prototypes used the working codename `myos`. User-facing paths (`~/.youros/`, `com.youros.*` launchd agents, and the `youros*` CLI commands) all use `youros`, while a handful of low-level engineering identifiers keep `myos` for backward compatibility:

- `MYOS_*` environment variables used by test harnesses and daemon tuning
- `myos.*` browser `localStorage` keys (renaming would reset saved UI preferences)
- `/api/health` returning `{"service":"myos-api"}` (checked by smoke tests and watchdogs)
- `BACKEND_SESSION_PREFIX = "myos-api-"` for internal `ostk` API session directories

See [CONTRIBUTING.md](CONTRIBUTING.md) to set up a local development environment and run the test suites.
