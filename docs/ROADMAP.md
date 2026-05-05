---
kind: roadmap
title: myOS 3-Year Roadmap
---

# myOS 3-Year Roadmap
**April 2026 — April 2029**

---

## Where we are now

myOS v2.2.0 is a fully working personal AI operating system. You can manage tasks, capture ideas, spawn AI agents, chat with multiple models, preview and work with files, and connect Gmail, Calendar, Drive, Slack, GitHub, and iMessage — all in one place. The foundation is solid: 56 API modules, 25 pages, 2,400+ tests, and a weekly release cadence.

The next three years are about making myOS indispensable, first for you personally, then for small teams, then for the enterprise.

---

## Year 1: Become the sharpest personal tool you own (2026)

**Theme: myOS knows you**

Right now myOS is very good at *capturing* — tasks, ideas, conversations, agent work. Year 1 is about making it *intelligent about what it captures*: surfacing the right thing at the right time, learning your patterns, and eliminating the cognitive overhead of managing a complex workflow.

### Goals
- myOS can answer "what's on my mind?" with one click, pulling from tasks, emails, Slack, notes, and agent transcripts
- You never lose context when switching between work streams
- Agent work is visible and predictable, not a black box

### Key milestonse

**Q2 2026 — v3.0: "Context everywhere"**
- Full-text and semantic search across everything: tasks, ideas, transcripts, emails, files
- Knowledge timeline: scroll backward through everything that happened on a given day
- Smart briefing: morning summary generated from your real data (calendar, open tasks, unread messages)
- Recurring tasks with natural language scheduling ("every Monday", "end of sprint")

**Q3 2026 — v3.1: "myOS learns your rhythm"**
- Automatic daily/weekly reviews generated from actual work done (agents, tasks closed, ideas captured)
- Priority suggestions: "based on what's open, these 3 things matter most today" — backed by data, not vibes
- Agent memory: agents remember context from prior sessions so you don't re-explain yourself
- Predictions: warn you when a deadline is at risk based on task completion velocity

**Q4 2026 — v3.2: "Reach farther"**
- Integration depth: move beyond reading to acting. Triage Gmail with one keypress. Create calendar blocks from tasks. Comment on GitHub PRs from inside myOS.
- Slack intelligence: surface messages that require action and create tasks from them automatically
- iMessage on any device (no macOS-only limitation)
- Voice: dictate tasks, ideas, and agent instructions

---

## Year 2: Bring your team inside (2027)

**Theme: myOS for small teams**

The hardest part of team coordination is context. Everyone has a different view of what's happening. Year 2 extends myOS from a personal tool to a shared workspace — where a PM, a designer, and two engineers can see the same picture without syncing in meetings.

### Goals
- A small team (2–10 people) can run their entire work loop inside myOS
- Every person sees what agents are doing, what tasks are open, and what decisions were made — and why
- myOS becomes the connective tissue between human work and AI work

### Key milestones

**Q1 2027 — v4.0: "Team workspaces"**
- Multi-user orgs: invite teammates, assign roles, separate workspaces with shared views
- Shared specs: one spec, multiple contributors, comment threads, version history
- Agent delegation: assign an agent task to a teammate; they can monitor and redirect it
- Team dashboard: live view of what every agent and person is working on right now

**Q2 2027 — v4.1: "Structured collaboration"**
- Prototype review workflow: design → feedback → approval in one loop, no Figma/Notion back-and-forth
- Decision log: record why a decision was made, what options were weighed, which agent helped
- Shared knowledge base: documents, notes, and agent outputs that the whole team can query
- Notification routing: the right person gets alerted when something needs them, not everyone

**Q3 2027 — v4.2: "Smarter together"**
- Fleet agents: spawn multiple agents that coordinate with each other on a shared task
- Cross-team visibility: PMs see engineering agents; engineers see product specs; no silos
- Auto-standup: myOS writes your standup from actual work done. You review and send.
- Workflow builder goes live: drag-and-drop pipelines that trigger agents, update tasks, and send notifications

**Q4 2027 — v4.3: "Enterprise scaffolding"**
- SSO / SAML: connect your company's identity provider
- Role-based access: admin, member, viewer — with meaningful permission boundaries
- Policy controls: budget caps per agent, approved models list, required approval thresholds
- Audit log UI: compliance-ready view of who did what and when

---

## Year 3: Become the OS for AI-native organizations (2028)

**Theme: myOS as a platform**

By 2028 the question won't be "should we use AI agents at work?" — it will be "how do we govern, coordinate, and get value from the agents we already have?" myOS positions itself as the answer: the operating layer that makes AI work legible, auditable, and trustworthy at scale.

### Goals
- Enterprise organizations can standardize on myOS as their AI coordination layer
- Third-party teams can build integrations and agent templates on top of myOS
- myOS proves ROI quantitatively: hours saved, decisions accelerated, costs reduced

### Key milestones

**Q1 2028 — v5.0: "myOS as a platform"**
- Public API: any tool or agent can push tasks, read context, and trigger workflows via REST
- Agent marketplace: publish and subscribe to agent templates built by the community or your org
- Webhook system: myOS notifies external systems when tasks close, agents complete, specs ship
- SDK: write a myOS integration in under an hour

**Q2 2028 — v5.1: "Intelligence at scale"**
- Org-level analytics: cost per project, agent ROI, time-to-close by task type
- Anomaly detection: "this agent has been running 3x longer than usual and spending 10x budget"
- Capacity planning: given your team's backlog and velocity, here's what you can actually ship this quarter
- Benchmark mode: compare your team's AI usage patterns to similar orgs (anonymized)

**Q3 2028 — v5.2: "Trust and compliance"**
- SOC 2 Type II certification
- Data residency options (US, EU)
- Granular audit export: filtered by user, agent, integration, or time window
- Self-hosted option: deploy myOS in your own infrastructure with your own keys

**Q4 2028 — v5.3: "Ambient intelligence"**
- myOS runs in the background and surfaces what matters without being asked
- Proactive task creation: email arrives → myOS drafts a task and asks if you want to add it
- Calendar intelligence: "you have a meeting about X in 2 hours — here's the relevant context"
- Cross-session continuity: pick up exactly where you left off, on any device, mid-agent-run

---

## What stays constant across all 3 years

1. **Real data, not vibes.** Every recommendation, priority, and summary is backed by actual events. No hallucinated statuses.
2. **Plain language everywhere.** No jargon in labels, tooltips, or error messages. If a PM can't read it, it ships with a rewrite.
3. **You own your data.** `~/.myos/` is yours. No data leaves your machine without your explicit action.
4. **ostk as the substrate.** The kernel coordinates agents; myOS surfaces the results. The two layers stay separate and composable.
5. **Ship weekly.** Small, tested increments. The smoke test passes before every release. Semver is honest.

---

## Success measures by year

| Metric | End of Year 1 | End of Year 2 | End of Year 3 |
|---|---|---|---|
| Integrations | 12 | 20 | 30+ |
| Active users | You + 5 | 50 teams | 500+ orgs |
| Agent templates | 15 | 40 | 100+ (community) |
| Time to answer "what should I do?" | < 5 sec | < 3 sec | < 1 sec |
| Enterprise compliance | None | Policies + SSO | SOC 2 + self-hosted |

---

*Last updated: April 17, 2026. Version targets are aspirational; actual version numbers will follow the weekly cadence and may shift.*
