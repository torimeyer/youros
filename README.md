# YourOS

Your personal AI operating system. A local web app that gives you a workspace with AI chat, background agents, task management, and file browsing. Built on [ostk](https://ostk.ai).

## Install

You need Python 3.9+, Node 18+, and git.

```bash
git clone https://github.com/torimeyer/youros.git
cd youros
./install.sh
```

## Start

```bash
./start.sh
```

Or from anywhere after install:

```bash
youros
```

Your browser will open to http://localhost:8000.

## First Run

The setup wizard walks you through:
1. Enter your name
2. Name your OS (e.g. "MadisonOS", "AlexOS")
3. Pick a theme
4. Connect your AI provider (Anthropic, Gemini, or OpenAI)

You can explore the dashboard, tasks, and ideas without an API key. Chat and agents need one.

## What's Inside

- **Dashboard** - home screen with day summary, tasks, goals, and quick actions
- **Chat** - talk to Claude or Gemini with tool access (file editing, web search, agent spawning)
- **Tasks** - create and manage work items with priorities, grouped by goals
- **Agents** - spawn background AI agents to work on tasks for you
- **Ideas** - capture raw thoughts and turn them into tasks
- **Files** - browse your workspace with in-app file preview
- **Timeline** - Gantt-style view of tasks and goals
- **Settings** - customize theme, connect MCP servers, view keyboard shortcuts

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Cmd+K | Command palette |
| Cmd+L | Toggle chat |
| Cmd+N | New task |
| Cmd+1-8 | Navigate pages |

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for the three-year plan.
