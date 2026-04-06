# myOS Product Roadmap

## What myOS is today

myOS is your personal operating system. It runs locally on your machine and gives you one place to manage tasks, capture ideas, talk to AI, spawn background agents, browse files, and track costs. It is built on ostk and connects to Claude and Gemini.

### Current features (April 2026)

- **Dashboard.** Smart focus card ("do this first"), day summary, session diff ("what changed"), quick launch, labels overview.
- **Tasks.** Create, prioritize (P0/P1/P2), close, reopen. Labels for organization. Dependencies (blocks/needs). Task briefings with full context. Health check for duplicates and missing info. Attributed commits linked to tasks. Attribution tracing (idea to commit history).
- **Activity.** Chronological feed of everything that happened. Filter by category (tasks, agents, ideas, system). Powered by ostk os history.
- **Chat.** Slide-out panel with Claude and Gemini. Multiple chat tabs. Tool mode (agent mode) for Claude with file editing, task creation, and agent spawning. Resizable. GIF search, emoji reactions, image paste. Default LLM chooser.
- **Agents.** Spawn Claude Code agents with custom prompts, models, and budgets. Permission requests (approve/deny). Delegation view showing which tasks to hand off. Agent templates. Duration estimation.
- **Ideas.** Quick-capture thoughts. AI-powered clustering of related ideas. Convert ideas into tasks.
- **Files.** Browse projects, navigate directories, preview text and image files.
- **Timeline.** Visual view of tasks over time with week, month, and quarter views.
- **Cost Tracking.** Budget allocation, breakdown by model, agent history.
- **Settings.** OS name, theme, accent color, default LLM, Google OAuth sign-in (connect/disconnect), MCP servers (ostk-managed and manual), feature toggles, export/import config (API keys excluded).
- **Search.** Cmd+K concept search across tasks and ideas, plus quick navigation commands.
- **What's New.** Release notes with badge for unseen updates.
- **Onboarding.** Setup wizard with name, theme, AI provider, and dream-to-plan feature.
- **Guided Tour.** Walkthrough of all major features for new users.
- **Keyboard shortcuts.** Cmd+K (search), Cmd+L (chat), Cmd+N (new task), Cmd+1-8 (navigation).

### ostk depth

myOS now covers roughly 70% of ostk's surface area, including: work management (add/close/list/next/link/depends/near/activate/refine/radiate/hay/compile), threading, compounds, document lifecycle (draft/promote/decompose), attributed commits, tracing, os operations (history/diff/clock/status/metrics), agent management (spawn/ps/reap), grants (list/approve/deny), secrets (set/get/list), and MCP server listing.

---

## Year 1 (2026): Foundation and Daily Use

### Q2 2026: Polish and Integrations

1. **Google Docs integration.** Connect to Google Docs for collaborative document planning.
2. **Chat history persistence.** Save conversations across browser refreshes.
3. **Mobile-friendly layout.** Dashboard and task list usable on phone or tablet.
4. **Calendar integration.** Pull in Google Calendar events on the dashboard and timeline.
5. **Email summaries.** Connect to Gmail and surface unread email summaries.

### Q3 2026: Workflows and Automation

1. **Recurring tasks and routines.** Tasks that repeat on a schedule.
2. **Scheduled agents.** Agents that run at specific times or on a recurring basis.
3. **Morning briefing.** Auto-generated daily summary when you open myOS.
4. **Meeting prep agent.** Gather context before calendar events into a briefing.
5. **Export and sharing.** Export task lists, progress, and timeline views as documents.

### Q4 2026: Intelligence

1. **Smart task suggestions.** Analyze work patterns and proactively suggest priority changes.
2. **Stale work detection.** Flag tasks and ideas that have not had activity in a while.
3. **Pattern recognition.** Learn which agent configurations and workflows work best.
4. **Drag-and-drop task ordering.** Manually reorder tasks between priority levels and labels.
5. **Notification center.** Desktop alerts for agent completion, task overdue, and idea clusters.

---

## Year 2 (2027): Power and Scale

### Q1-Q2 2027: Advanced Agent Orchestration

1. **Multi-agent workflows.** Jobs requiring several agents in sequence or parallel.
2. **Agent memory.** Agents remember past sessions and pick up where they left off.
3. **Agent collaboration.** Running agents share information through a shared workspace.
4. **Agent templates library.** Curated templates for common PM tasks.
5. **Budget controls.** Spending limits and alerts per agent, per day, per project.

### Q3-Q4 2027: Knowledge Management

1. **Personal knowledge base.** Searchable knowledge graph from transcripts, outputs, ideas, and notes.
2. **Document understanding.** Upload and index PDFs, slide decks, and spreadsheets.
3. **Weekly and monthly reviews.** Auto-generated progress reports with trends.
4. **Cross-project insights.** Identify connections between projects.
5. **Voice interface.** Capture ideas and give instructions hands-free.

---

## Year 3 (2028): Platform and Vision

### Q1-Q2 2028: Platform Capabilities

1. **Custom dashboards.** Create your own layouts with preferred widgets.
2. **Plugin system.** Add integrations (Slack, Jira, Notion) without modifying core code.
3. **Workflow builder.** Visual tool for multi-step automations.
4. **Team sharing.** Share views, labels, or agent outputs with teammates.
5. **Predictive planning.** Estimate goal timelines and warn about unrealistic deadlines.

### Q3-Q4 2028: Long-Term Vision

1. **Ambient awareness.** Monitor relevant signals and surface what matters.
2. **Career growth tracking.** Track skills, projects, and impact over time.
3. **Portable identity.** Move myOS to any machine with all data and preferences.
4. **Open ecosystem.** Publish patterns so others can build their own personal OS.
5. **Enterprise mode.** Team-wide ostk with shared audit trails and governance.
