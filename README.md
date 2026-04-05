# YourOS

Your personal operating system. A web app that sits on top of [ostk](https://ostk.ai) and gives you a visual interface for managing tasks, ideas, AI agents, and chat.

## Quick Install

```bash
./install.sh
```

Or if you already have the prerequisites (Python 3.9+, Node 18+):

```bash
cd api && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cd ../app && npm install && npm run build
```

## Start

```bash
./start.sh
```

This boots ostk, starts the server, and opens your browser to http://localhost:8000.

## First Run

When you open YourOS for the first time, a setup wizard walks you through:
1. Enter your name
2. Name your OS (e.g. "MadisonOS")
3. Pick a theme
4. Connect your AI provider (Anthropic, Gemini, or OpenAI)

You can explore the dashboard, tasks, and ideas without an API key. Chat and agents need one.

## What's Inside

- **Dashboard** - your home screen with tasks, goals, and quick actions
- **Tasks** - create and manage work items with priorities
- **Ideas** - capture raw thoughts and turn them into tasks
- **Agents** - spawn AI agents to do work for you
- **Chat** - talk to Claude, Gemini, or GPT from any screen
- **Files** - browse your workspace
- **Costs** - track what you're spending on AI
- **Settings** - customize everything
