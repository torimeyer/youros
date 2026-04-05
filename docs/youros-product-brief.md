# YourOS Product Brief

**Version:** 1.0
**Date:** April 4, 2026
**Authors:** Tori Meyer, ToriOS

---

## What is YourOS?

YourOS is a personal operating system for managing your work, your AI agents, and your thinking. It's a web app that sits on top of ostk (the coordination kernel) and gives you a visual interface for everything ostk can do, plus an always-available AI chat that feels like talking to a teammate.

Think of it as a command center for people who work with AI. You can manage tasks, track goals, spawn AI agents to do work for you, browse your files, review agent transcripts, and chat with multiple AI models, all in one place. The name "YourOS" is the default, but every user can rename it to whatever they want (like "ToriOS").

---

## Who is this for?

**Primary audience:** Knowledge workers, product managers, engineers, and founders who already use AI tools (Claude, ChatGPT, Gemini) and want a single place to manage their work alongside AI agents.

**Secondary audience:** Teams that want a shared workspace where humans and AI agents collaborate on tasks, with full visibility into what the AI did and why.

**Not for:** People who don't use AI in their workflow yet. This is for people who are already bought in and want better tooling.

---

## Core Problems We Solve

1. **AI work is invisible.** When you spawn an AI agent, you can't see what it's doing, what it costs, or what it decided. YourOS makes all agent work visible with live sessions, transcripts, and cost tracking.

2. **Task management doesn't understand AI.** Existing tools (Jira, Linear, Notion) don't know about AI agents. YourOS tasks (needles) can be worked by humans or agents, and the system tracks who did what.

3. **Thinking is scattered.** Ideas live in Slack, notes in Google Docs, tasks in Linear. YourOS captures raw thinking (hay) and helps you turn it into actionable work (needles), all in one place.

4. **Context switching kills flow.** Switching between your chat tool, task tracker, file browser, and project docs breaks concentration. YourOS puts everything in one window with keyboard shortcuts to jump between views instantly.

---

## Product Sections

### 1. Home Dashboard

The landing page when you open YourOS. Shows you what matters right now.

**Widgets:**
- **Today's Focus** - AI-generated list of what you should work on today, based on priority and deadlines. Refreshable on demand.
- **Tasks** - Quick view of open tasks with inline add. Shows open/closed counts.
- **Goals** - High-level objectives with progress bars. Goals contain multiple tasks.
- **ostk Status** - Live connection to the ostk kernel. Shows open tasks, done count, unsorted notes, active threads, priority breakdown.
- **Quick Launch** - Customizable shortcuts to frequently-used actions or projects.
- **Session Activity** - Shows active AI sessions and recent activity.
- **Cost Tracker** - How much you've spent on AI tokens, with time filters (24h, 7d, 30d, all).

**Design notes:**
- Two-column widget layout on desktop, single column on mobile.
- Widgets are cards with headers, subtle borders, and color-coded accents.
- "Welcome to [YourOS name]" greeting at top.
- Dark theme by default, light theme available.

---

### 2. Tasks (Needles)

Full task management powered by ostk's needle system.

**Features:**
- **Quick add** - "What needs to be done?" input at top.
- **Filters** - All, Open, Closed, Shelved, This week, by priority (P0/P1/P2), List vs Thread view.
- **Task rows** - Show task ID (like #122, #155), title, priority badge (color-coded), stale indicator.
- **"What should I do next?"** - AI-powered button that looks at your open tasks, priorities, and deadlines and recommends what to work on.
- **Ask ToriChat** - Send your task list to the chat for discussion.
- **Copy** - Copy task list to clipboard.
- **Thread view** - Group related tasks visually.
- **Live sync** - Tasks sync with ostk in real-time (Live indicator).
- **Goals tab** - Switch between tasks and goals views.

**Not including from old version:**
- Shelved status (keep it simpler: open, closed)
- 179+ task counts suggest it needs archiving/cleanup features

**Adding:**
- Drag-and-drop reordering
- Bulk actions (close multiple, change priority)
- Task detail panel (click to expand with description, history, linked agent work)

---

### 3. Timeline

Gantt-style view of goals and tasks on a calendar.

**Features:**
- **Date navigation** - Previous/next arrows, "Today" button, month view.
- **Goals as bars** - Long colored bars spanning their duration.
- **Needles as blocks** - Shorter blocks showing needle IDs, positioned on their target dates.
- **Filters** - Open/Closed, All/P0/P1/P2.
- **Color coding** - Goals and needles color-coded by priority.

**Adding:**
- Week and quarter view options
- Drag to reschedule
- Dependency arrows between related needles

---

### 4. Ideas (Hay)

Capture raw, unstructured thoughts before they become tasks.

**Features:**
- Quick capture input ("What's on your mind?")
- List of hay entries, sortable by date
- "Compile" action to turn hay into needles
- AI-assisted compilation (suggest which hay items become tasks, what priority)
- Cluster related hay items together

**This is the "ideas inbox."** Low friction, no structure required. Just type and save.

---

### 5. Agents

Spawn, monitor, and interact with AI agents.

**Features:**
- **New Agent** button - Spawn with name, prompt, model choice, and budget.
- **Status dashboard** - Connection status, active sessions count, last update time.
- **Session list** - Active and Recent tabs. Each session shows: agent name, running time, status (RUNNING/COMPLETE/FAILED), message count.
- **Live Output** - Stream agent's work in real-time.
- **Chat with agent** - Send messages to a running agent.
- **New Helper** - Spawn a sub-agent from within a session.
- **Metrics tab** - Token usage, cost, time spent per agent.
- **Automation tab** - Recurring/scheduled agents.
- **Hide helpers** - Filter to show only top-level agents.

**Adding:**
- Agent templates (pre-configured agents for common tasks like "research", "code review", "write tests")
- Agent history with searchable transcripts
- Budget alerts (notify when approaching limit)
- Kill/pause/resume controls

---

### 6. Projects (Workspaces)

Organize work into project spaces.

**Features:**
- **Project list** - Each project shows: name, briefing badge, task count, favorite star.
- **Briefing docs** - Each project can have a BRIEFING.md that provides context.
- **Folders and files** - Browse project contents.
- **Favorites** - Star projects for quick access.
- **Task scoping** - View only tasks related to a specific project.

**Adding:**
- Project creation wizard
- Archive projects
- Project-level settings (default AI model, budget limits)

---

### 7. Docs (File Browser)

Browse and manage files in your workspace.

**Features:**
- **Directory listing** - Folders and files with sizes and modification dates.
- **Breadcrumb navigation** - Path trail at top.
- **Browse/Recent/Upload tabs** - Switch between browsing, recently modified, and uploading new files.
- **Google Docs integration** - Import from and link to Google Docs.
- **Search** - Find files by name.
- **File preview** - View markdown files inline.

**Adding:**
- Drag-and-drop upload
- File type icons
- Preview for images, PDFs, code files
- Version history (via git)

---

### 8. Transcripts

Searchable archive of all agent conversations.

**Features:**
- **Count and filters** - Total transcripts, filter by Agents vs Reports.
- **Search** - Full-text search across all transcripts.
- **Transcript list** - Title, time ago, size, type badge (agent/report).
- **Click to view** - Full conversation with collapsible tool calls.

**Adding:**
- Export transcripts (PDF, markdown)
- Share transcript link
- "Resume" button to pick up where an agent left off

---

### 9. Chat (Always-On Panel)

Right-side panel for chatting with AI, always available from any screen.

**Features:**
- **Multi-model** - Tag different models with @claude, @gemini, etc.
- **New conversation** - Start fresh chats.
- **Session management** - Multiple active conversations.
- **Suggestions** - Quick action buttons ("Show my open P0 tasks", "What should I work on next?", "Summarize today's updates", "Spawn a research agent").
- **Context-aware** - Chat knows about your tasks, projects, and workspace.
- **Toggle** - Cmd+L to show/hide.
- **Search** - Search within chat history.
- **Image support** - Paste images into chat.

**This is the core interaction model.** When you type in this chat, it should feel like typing in Claude Code. The AI has full access to your ostk data, can create tasks, spawn agents, and take actions on your behalf.

**Adding:**
- Voice input
- Chat history sidebar
- Pin important messages
- Share chat excerpts

---

### 10. Command Palette

Quick navigation and actions via keyboard.

**Features:**
- **Cmd+K** to open.
- **Search everything** - Files, tasks, projects, commands.
- **Quick navigation** - Go to Home (Cmd+0), Tasks (Cmd+1), Agents (Cmd+2), Projects (Cmd+3), Docs (Cmd+4), Transcripts (Cmd+5), Settings (Cmd+,).
- **Actions** - Toggle Chat (Cmd+L), New Task (Cmd+N), New Note (Cmd+Shift+N).

---

### 11. Settings

Configuration and preferences.

**Features:**
- **Appearance** - Dark/light mode toggle.
- **Features** - Toggle individual features on/off (Chat, Tasks, Hay, Agents, Projects, etc.).
- **AI Provider** - Switch between providers (Anthropic, Google, others). API key management.
- **Data** - Export/import configuration as JSON.
- **Notifications** - Granular notification controls (Agent Complete, Agent Needs Input, Agent Failed, Approval Needed, Nudges, System). Quiet Hours.
- **Secret Vault** - Manage API keys and secrets securely (stored via ostk, never exposed in logs).
- **Keyboard Shortcuts** - View and customize.

**Adding:**
- Custom OS name (rename "YourOS" to anything)
- Theme colors/accent customization
- Workspace-level settings

---

### 12. Day Summary

One-click daily digest.

**Features:**
- Button in top bar to generate a summary of today's work.
- Shows: tasks completed, agents run, time spent, key decisions made.
- Can be shared or saved.

---

## Design Direction

### Visual Identity
- **Dark theme primary**, light theme secondary.
- **Color palette:** Blue (#3b82f6) as primary interactive, Pink (#ec4899) for accents/highlights, Orange (#f97316) for warnings/energy, Purple (#8b5cf6) for agents/AI, Cyan (#06b6d4) for supplementary.
- **Typography:** Clean sans-serif. Bold headings, medium body, muted secondary text.
- **Layout:** Fixed left sidebar for navigation. Optional right panel for chat. Main content area in center.
- **Cards everywhere.** Dashboard widgets, task rows, agent sessions, project items, all are cards with subtle borders and hover states.
- **Color-coded priorities:** P0 = pink-red, P1 = orange, P2 = blue.

### Interactions
- Every action has visual feedback within 100ms.
- Keyboard-first navigation with full mouse support.
- Progressive disclosure: show the essentials, reveal details on click/hover.
- Smooth transitions between views (no hard page reloads).

---

## Tech Stack (Recommended)

**Frontend:**
- React 18 + TypeScript
- Vite
- Tailwind CSS
- Zustand (state management)
- React Router (navigation)

**Backend:**
- Python FastAPI (bridges UI to ostk CLI and AI APIs)
- WebSockets (real-time agent streaming, live updates)
- Anthropic SDK + Google Gemini SDK (multi-model chat)
- ostk CLI integration (task/needle/hay management)

**Storage:**
- IndexedDB (client-side, offline-first for chat history and preferences)
- ostk's built-in storage (needles, hay, audit trail)
- File system (workspaces, transcripts, docs)

**Deployment:**
- Local-first (runs on your machine)
- Future: hosted version for teams

---

## What's Different from the Work Version

Starting fresh, so here's what we're changing:

1. **Generic by default.** No work-specific features (Publish to DEV, NR-specific integrations). The platform is for anyone.
2. **Customizable name.** "YourOS" is the default, user picks their name on first launch.
3. **Cleaner navigation.** The sidebar had too many items. Grouping into Overview/Work/System sections was good, keeping that.
4. **No "nerdpacks" or "feedback" features.** Too niche. Can add as plugins later.
5. **Better onboarding.** New users need a setup wizard: name your OS, connect your API key, pick a theme.
6. **Mobile-responsive.** The old version was desktop-only.

---

## MVP Scope (v0.1)

For the first sellable version, ship these in order:

**Phase 1: Foundation**
- Project scaffolding (React + Vite + Tailwind + FastAPI)
- Sidebar navigation with routing
- Command palette (Cmd+K)
- Settings page with theme toggle and API key entry
- Custom OS name on first launch

**Phase 2: Chat + ostk**
- Chat panel (right side, toggleable)
- Connect to Anthropic API for Claude conversations
- Connect to ostk CLI for task/needle/hay data
- Real-time sync with ostk

**Phase 3: Work Management**
- Tasks view (list, filter, quick-add, priorities)
- Ideas/Hay capture
- Goals with progress tracking
- Timeline view
- Today's Focus (AI-generated)

**Phase 4: Agents**
- Spawn agents through the UI
- Live output streaming
- Agent session management
- Transcripts view
- Cost tracking

**Phase 5: Projects + Docs**
- Project/workspace browser
- File browser with preview
- Briefing docs

**Phase 6: Polish + Ship**
- Onboarding wizard
- Light/dark theme
- Keyboard shortcuts
- Data export/import
- Documentation
- Landing page

---

## Success Metrics

- User can go from install to first chat in under 2 minutes
- All ostk features accessible through the UI (no terminal required)
- Agent spawn-to-output visible in under 5 seconds
- Works fully offline for local data (chat history, tasks, docs)
- Handles 500+ tasks and 300+ transcripts without lag

---

## Pricing (Future)

- **Free:** Local-only, single user, bring your own API keys
- **Pro ($X/mo):** Cloud sync, hosted agents, team features
- **Enterprise:** Self-hosted, SSO, audit logs, custom integrations

---

## Open Questions

1. Should chat support voice input in v1?
2. Do we want a plugin/extension system from the start?
3. Should the timeline view support dependencies (arrows between tasks)?
4. How do we handle multi-user/team features in the architecture from day one, even if we don't ship them yet?
