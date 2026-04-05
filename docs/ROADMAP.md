# YourOS Product Roadmap

## What YourOS is today

YourOS is your personal operating system. It runs locally on your machine and gives your one place to manage tasks, capture ideas, talk to AI, spawn background agents, browse files, review transcripts, and track costs. It is built on ostk and connects to Claude, Gemini, and otyour AI providers through youChat.

### Current features (April 2026)

- **Home Dashboard.** Focus tasks, day summary, quick launch, system status, active agents, and cost overview in one view.
- **Tasks.** Create, prioritize (P0/P1/P2), close, reopen. Filter by status, priority, and project. Group by goal. "What should I do next?" suggestion. List and grid views.
- **Goals.** Auto-generated from task tags. Progress bars, overall completion tracking.
- **Timeline.** Gantt-style view of tasks grouped by goal. Week, month, and quarter views.
- **Ideas.** Quick-capture thoughts. AI-powered clustering of related ideas. Convert ideas into tasks individually or in bulk.
- **Agents.** Spawn Claude Code agents with custom prompts, models, and budgets. Track running/completed/failed status. View transcripts. Send nudges to running agents. Kill agents. Agent templates. Duration estimation from historical data.
- **youChat.** Slide-out chat panel with Claude and Gemini. Tool mode (agent mode) for Claude with file editing, task creation, web search, and agent spawning. Multi-model conversations. GIF search. Image paste and vision. Conversation history.
- **Files.** Browse projects, navigate directories, preview text and image files, open files externally.
- **Transcripts.** Searchable archive of all AI conversations and agent sessions. Filter by date, type, and keyword.
- **Cost Tracking.** Budget allocation over time, breakdown by model, agent history table.
- **Settings.** OS name, theme, accent color, API keys, feature toggles, custom terminology, MCP server configuration.
- **Command Palette.** Cmd+K to quickly navigate or take actions.
- **Onboarding Wizard.** First-run setup for name, theme, and AI provider.
- **Keyboard shortcuts.** Cmd+1 through Cmd+8 for navigation, Cmd+L for chat, Cmd+N for new task.

---

## Year 1 (2026): Foundation and Daily Use

### Q2 2026: Polish and Reliability

The goal this quarter is to make YourOS solid and pleasant for daily use. No new features that are half-baked. Everything that exists should work smoothly.

1. **Notifications and alerts.** Surface important updates (agent finished, task overdue, idea cluster ready) as toast notifications and optional desktop alerts so you does not have to keep checking.
2. **Search everything.** A single search bar (or enhancement to the command palette) that finds tasks, ideas, transcripts, files, and agent output all at once.
3. **Drag-and-drop task ordering.** Let you manually reorder tasks and drag them between priority levels or goals instead of relying only on dropdown menus.
4. **Chat history persistence.** Save youChat conversations across browser refreshes so you never loses context mid-conversation.
5. **Mobile-friendly layout.** Make the dashboard and task list usable on a phone or tablet so you can check status on the go.

### Q3 2026: Integrations and Workflows

The goal this quarter is to connect YourOS to the tools you already uses, so she can stay in one place instead of switching between apps.

1. **Calendar integration.** Pull in Google Calendar events and show them on the dashboard and timeline. Let you block time for focus tasks directly from YourOS.
2. **Email summaries.** Connect to Gmail and surface unread email summaries on the dashboard. Let youChat answer questions about recent emails.
3. **Recurring tasks and routines.** Support tasks that repeat on a schedule (daily standup prep, weekly review) so you does not have to recreate them.
4. **Quick actions from chat.** Let youChat create tasks, set reminders, and update goals through natural conversation without switching pages.
5. **Export and sharing.** Export task lists, goal progress, and timeline views as clean documents or images you can share with your team.

### Q4 2026: Intelligence and Automation

The goal this quarter is to make YourOS proactively helpful instead of waiting to be asked.

1. **Morning briefing.** Auto-generate a daily summary when you opens YourOS: what happened overnight (agent results, new emails), what is on the calendar, and suggested priorities for the day.
2. **Smart task suggestions.** Analyze patterns in how you works (what she does first, what she postpones) and proactively suggest task ordering and priority changes.
3. **Scheduled agents.** Let you schedule agents to run at specific times or on a recurring basis (e.g., "every Monday morning, check my project status and update the dashboard").
4. **Stale work detection.** Automatically flag tasks, ideas, and goals that have not had activity in a while and suggest whetyour to close, reprioritize, or break them down further.
5. **Meeting prep agent.** Before calendar events, automatically gatyour context (related tasks, recent notes, relevant transcripts) into a briefing doc.

---

## Year 2 (2027): Power and Scale

### Q1-Q2 2027: Advanced Agent Orchestration

The goal this half is to make agents dramatically more capable, letting you describe complex work and have YourOS coordinate multiple agents to get it done.

1. **Multi-agent workflows.** Define a job that requires several agents working in sequence or in parallel (e.g., "research this topic, then draft a document, then review it"). YourOS coordinates handoffs and tracks progress.
2. **Agent memory and context.** Agents remember what they have done in past sessions and can pick up where they left off. No more starting from scratch every time.
3. **Agent collaboration.** Multiple running agents can share information with each otyour through a shared workspace, so one agent's output feeds into another's input automatically.
4. **Approval workflows.** For high-stakes agent actions (sending emails, modifying important files, spending above a budget threshold), require your approval before proceeding.
5. **Agent templates library.** A curated set of ready-to-use agent templates for common PM tasks: writing PRFAQs, competitive analysis, stakeholder updates, data gathering.

### Q3-Q4 2027: Knowledge Management and Learning

The goal this half is to make YourOS a second brain that gets smarter over time.

1. **Personal knowledge base.** Automatically organize transcripts, agent outputs, ideas, and notes into a searchable knowledge graph. Ask youChat questions like "what did I decide about X last month?" and get accurate answers.
2. **Pattern recognition.** YourOS learns your work patterns over weeks and months. It notices which types of tasks take longer than expected, which ideas lead to successful outcomes, and which agent configurations work best.
3. **Document understanding.** Upload PDFs, slide decks, and spreadsheets. YourOS indexes them and makes them available as context for chat and agents. Ask questions about your own documents.
4. **Weekly and monthly reviews.** Auto-generated progress reports showing what was accomplished, what slipped, how time was spent, and trends over time.
5. **Cross-project insights.** When you works on multiple projects, YourOS identifies connections and dependencies between them that might not be obvious.

---

## Year 3 (2028): Platform and Vision

### Q1-Q2 2028: Platform Capabilities

The goal this half is to make YourOS extensible, so it can grow with your needs without requiring engineering work.

1. **Custom dashboards.** Let you create your own dashboard layouts with the widgets she cares about most, arranged the way she wants.
2. **Plugin system.** A simple way to add new integrations (Slack, Jira, Notion, etc.) without modifying YourOS core code. Each plugin adds new data sources, actions, and dashboard widgets.
3. **Workflow builder.** A visual tool for creating multi-step automations: "when X happens, do Y, then Z." No coding required. Think of it as personal if-this-then-that for work.
4. **Team sharing (optional).** Ability to share specific views, goals, or agent outputs with teammates who also run YourOS. Collaboration without centralized servers.
5. **Voice interface.** Talk to YourOS using voice. Capture ideas, ask questions, and give instructions hands-free.

### Q3-Q4 2028: Long-Term Vision

The goal this half is to make YourOS feel like a true operating system for work, not just a productivity app.

1. **Predictive planning.** Based on historical data, YourOS estimates how long goals will take to complete, warns about unrealistic timelines, and suggests how to restructure work to hit deadlines.
2. **Ambient awareness.** YourOS passively monitors relevant signals (project repos, team channels, industry news) and surfaces only what matters to you, without your having to go looking.
3. **Career growth tracking.** Track skills developed, projects delivered, impact created, and relationships built over time. Generate narratives for performance reviews and career conversations.
4. **Portable identity.** your data, preferences, workflows, and agent configurations are fully portable. She can move YourOS to a new machine, a cloud instance, or a different platform and everything comes along.
5. **Open ecosystem.** Publish ostk and YourOS patterns so others can build their own personal operating systems. YourOS becomes a reference implementation, not a walled garden.
