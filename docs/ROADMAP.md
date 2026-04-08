# myOS Product Roadmap

## What myOS is today

myOS is your personal operating system. It runs locally on your machine and gives you one place to manage tasks, capture ideas, talk to AI, spawn background agents, browse files, and track costs. It is built on ostk and connects to Claude and Gemini.

### Current features (April 2026)

- **Dashboard.** Smart focus card ("do this first"), day summary, session diff ("what changed"), quick launch, labels overview. Next meeting widget (requires Google Calendar).
- **Tasks.** Create, prioritize (P0/P1/P2), close, reopen. Labels for organization. Dependencies (blocks/needs). Task briefings with full context. Health check for duplicates and missing info. Attributed commits linked to tasks. Attribution tracing (idea to commit history). Auto-labeling on creation and backfill on boot.
- **Activity.** Chronological feed of everything that happened. Filter by category (tasks, agents, ideas, system). Powered by ostk os history.
- **Chat.** Slide-out panel with Claude and Gemini. Multiple chat tabs. Tool mode (agent mode) for Claude with file editing, task creation, and agent spawning. Resizable. GIF search, emoji reactions, image paste. Default LLM chooser. Calendar and task context injected automatically when relevant.
- **Agents.** Spawn Claude Code agents with custom prompts, models, and budgets. Permission requests (approve/deny). Delegation view showing which tasks to hand off. Agent templates. Duration estimation. Ghost agent cleanup on boot.
- **Ideas.** Quick-capture thoughts. AI-powered clustering of related ideas. Convert ideas into tasks. Template picker (Feature idea, Problem to solve, Research spike, Meeting follow-up, Integration). Chat-to-idea ("save this as an idea" in chat). Idea aging badges. Admin-defined template library.
- **Files.** Browse projects, navigate directories, preview text, image, and Office files (.pptx, .pdf).
- **Google Drive.** Connect your Google account and browse, search, and preview Google Docs, Slides, and Sheets inside myOS. 1-hour preview cache. Drag-and-drop credentials setup.
- **Google Calendar.** View today's events and the next 7 days. Create tasks from events. "What's on my calendar today?" works in chat. Meet link shortcuts.
- **Notifications.** Bell icon with unread badge. Persistent notification store. Upgrade alerts, sync alerts, and agent completion notices.
- **Upgrade check.** myOS checks for new versions of itself and the ostk kernel on startup. One-click upgrade from the settings page.
- **Settings sync.** Keep settings, preferences, and idea templates in sync across machines via a private git repo. Auto-pull on boot.
- **Timeline.** Visual view of tasks over time with week, month, and quarter views.
- **Cost Tracking.** Budget allocation, breakdown by model, agent history.
- **Settings.** OS name, theme, accent color, default LLM, Google OAuth sign-in (connect/disconnect), MCP servers (ostk-managed and manual), feature toggles, export/import config (API keys excluded). Sync configuration.
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

1. **Chat history persistence.** Save conversations across browser refreshes.
2. **Mobile-friendly layout.** Dashboard and task list usable on phone or tablet.
3. **Email summaries.** Connect to Gmail and surface unread email summaries.
4. **Morning briefing.** Auto-generated daily summary when you open myOS. Built on top of calendar, tasks, and activity — most of the pieces are already there.
5. **Integration health dashboard.** One place to see which integrations (Drive, Calendar, Gmail) are connected and working. Surface errors before they become surprises.
6. **Meeting prep agent.** Before a calendar event, automatically gather relevant tasks, Drive docs, and recent chat context into a briefing.

### Q3 2026: Workflows and Automation

1. **Recurring tasks and routines.** Tasks that repeat on a schedule.
2. **Scheduled agents.** Agents that run at specific times or on a recurring basis.
3. **Export and sharing.** Export task lists, progress, and timeline views as documents.
4. **Plugin system via MCP.** MCP servers are already wired in. Formalize a plugin model so users can add integrations (Slack, Jira, Notion) by registering an MCP server, without modifying core code.
5. **Audit trail export.** One-click export of the ostk audit log as a formatted report. Needed for enterprise compliance reviews.

### Q4 2026: Intelligence and Voice

1. **Smart task suggestions.** Analyze work patterns and proactively suggest priority changes.
2. **Stale work detection.** Flag tasks and ideas that have not had activity in a while.
3. **Pattern recognition.** Learn which agent configurations and workflows work best.
4. **Drag-and-drop task ordering.** Manually reorder tasks between priority levels and labels.
5. **Voice interface.** Capture ideas and give instructions hands-free. Claude's voice APIs make this closer than the original 2027 estimate.
6. **Document indexing.** Index the content of Drive files, PDFs, and slide decks so they are searchable and usable as chat context. (Preview and file access already exist — indexing is the next step.)

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
2. **Weekly and monthly reviews.** Auto-generated progress reports with trends.
3. **Cross-project insights.** Identify connections between projects.
4. **Enterprise install packages.** Distributable myOS builds pre-configured for a specific organization (auth proxy, default settings, credentials). The NR enterprise path is the first example of this pattern.

---

## Year 3 (2028): Platform and Vision

### Q1-Q2 2028: Platform Capabilities

1. **Custom dashboards.** Create your own layouts with preferred widgets.
2. **Workflow builder.** Visual tool for multi-step automations.
3. **Team sharing.** Share views, labels, or agent outputs with teammates.
4. **Predictive planning.** Estimate goal timelines and warn about unrealistic deadlines.

### Q3-Q4 2028: Long-Term Vision

1. **Ambient awareness.** Monitor relevant signals and surface what matters.
2. **Career growth tracking.** Track skills, projects, and impact over time.
3. **Open ecosystem.** Publish patterns so others can build their own personal OS.
4. **Enterprise mode.** Team-wide ostk with shared audit trails and governance.
