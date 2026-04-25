# myOS vs Claude Cowork

A side-by-side look at how the two products overlap, where myOS is more powerful, and what Cowork has that we should consider adding. Every Cowork claim cites the source.

**Revision note:** an earlier draft of this doc asserted Cowork is single-agent. That was wrong. Cowork explicitly coordinates sub-agents. This draft corrects that and sharpens the real distinction, which is how sub-agents are isolated and what coordination state is addressable from outside the product.

## TL;DR

Claude Cowork is Anthropic's desktop agent for non-technical knowledge workers. You give it a goal, it plans, you approve (or not, if you toggle approval off), and it drives your computer to deliver a result. A main Cowork session coordinates specialist sub-agents bundled into plugins. Isolation between sub-agents is scope-based: each has bounded permissions on folders and connectors.

myOS is a coordination kernel (ostk). Sub-agents are first-class rows on disk. Isolation for code work is filesystem-level via git worktrees so many agents can edit the same repo in parallel without stepping on each other. Rules fire on every turn. Sessions, tasks, decisions, and memory all persist in a common store.

Both products run sub-agents. The real differences are the isolation model, whether coordination state is inspectable from outside the product, and who the audience is. Cowork is polished for non-engineers. myOS is built for parallel code and ops work where safe git commits across a fleet matter.

## What each product is

**Claude Cowork**
Desktop app on macOS and Windows. Research preview launched late January 2026, GA April 9, 2026. Sits next to Chat and Code inside Claude Desktop. Positioned as "Claude Code power for knowledge work" for researchers, analysts, operations, legal, and finance teams. Plugins bundle Skills (domain knowledge), Connectors (tool integrations), and Sub-agents (specialists that handle specific tasks end-to-end). Plan approval is on by default and can be toggled off.

Source quote: "Sub-agents: Specialized agents that handle specific tasks end-to-end" and "Bundle any skills, connectors, and sub-agents together to turn Claude into a specialist for your role, team, and company." (https://claude.com/product/cowork)

**myOS**
Coordination kernel (ostk) plus a web app. Agents are first-class citizens of the OS: they spawn, register, get budget, send mailbox messages, hold locks, commit to their own worktrees when doing code work, and finalize through a handoff. Built for people who orchestrate many agents at once.

## Side-by-side

| Capability | Claude Cowork | myOS |
|---|---|---|
| **Agent model** | Main session + specialist sub-agents bundled in plugins | Main session + many sub-agents spawned on demand via API |
| **Sub-agent isolation** | Scope-based. Each sub-agent has permissions on specific folders and connectors | Scope-based for non-code work; filesystem-level (git worktrees) for code work (`api/services/spawn_isolation.py`) |
| **Git-safe parallelism** | Not a Cowork concept. Cowork is not aimed at parallel edits to the same repo | Native. `scripts/worktree-reaper.sh` classifies absorbed vs unique, cleans up safely |
| **Parallel sub-agent spawn API** | Plugins define sub-agents; runtime spawn is orchestrated inside Cowork and not exposed | `POST /agents/spawn` and `POST /agents/fleets/spawn` (`api/routers/agents.py`), budget ledger per agent |
| **Coordination state** | Internal to the desktop app | On disk. `.ostk/sessions/{id}/events.jsonl`, decisions.jsonl, registration rows. Inspectable with `ls` and `jq` |
| **Rule enforcement** | Plan approval before action (toggleable). Scoped permissions per agent | Hook system injects non-negotiable rules on every UserPromptSubmit; live agent snapshot + unacknowledged-completion blocks (`.claude/hooks/standing-rules.sh`) |
| **Agent lifecycle tracking** | Working-session concept, UI-only | Register, heartbeat, nudge, reply, complete, budget (`/agents/{name}/...` endpoints) |
| **Persistence** | Conversation history stored locally on device (Teams/Enterprise) | Append-only event log per session in `.ostk/sessions/{id}/events.jsonl`, survives backend restart |
| **Work tracking** | Task list per session | hay/idea/needle model with decisions.jsonl audit trail, auto-labels, phase tags, session-to-task linkage |
| **Scheduled tasks** | Yes. Daily email checks, weekly digests, metric pulls | Yes. `/schedule` and `/loop` skills, cron routines |
| **Computer use** | Dispatch mode (March 23, 2026). Opens apps, fills spreadsheets, drives the browser. Connectors preferred, browser second, screen last resort | computer-use MCP first-class, tiered access (read for browsers, click for IDEs, full otherwise) |
| **File ops** | Rename, sort, deduplicate folders. Receipt and invoice to spreadsheet | Standard fs_ops plus shell, git worktrees, plus computer-use for GUI apps |
| **Integrations** | Slack, Google Drive, Gmail, DocuSign, FactSet, Zoom MCP, Chrome connector | Gmail, Calendar, Drive, GitHub, Slack, iMessage, plus fcp-gdocs, fcp-pdf, fcp-sheets, fcp-slides, fcp-midi, fcp-python, stitch (`.mcp.json`) |
| **Plugin marketplace** | Yes. Private marketplaces per org (Feb 24, 2026) | No marketplace. Any MCP server works, wired via `.mcp.json` |
| **Mobile pairing** | Research preview for Pro and Max. Send task from phone, desktop executes, result comes back | Not yet |
| **Design / UI generation** | Claude Design plugin. Prompts to design systems, sites, decks (April 17, 2026) | stitch MCP integration generates UI screens |
| **Enterprise controls** | RBAC, org spend limits, Analytics API, OpenTelemetry export to SIEM | Per-agent budget tracking, cost tracking page, session and task visibility filters. No RBAC, no org dashboard |
| **Regulated workloads** | Explicitly not supported. "Do not enable Cowork for HIPAA, FedRAMP, or FSI regulated workloads" | Self-hosted, no such restriction out of the box. Compliance is our call |
| **Deployment** | Desktop app (Mac, Windows), also via Amazon Bedrock, Google Vertex AI, Microsoft Foundry | FastAPI backend + React frontend, runs locally |
| **Distribution** | Direct download, plus Microsoft Copilot Cowork (March 10, 2026) | Self-hosted for now |
| **Pricing** | Pro $17-20/mo, Max 5x $100/mo, Max 20x $200/mo, Team $20/seat, Enterprise custom | N/A (self-hosted, user brings their own Claude API key) |
| **Target user** | Non-engineer knowledge workers | Engineers and power users running multi-agent work |
| **Openness** | Closed product, Anthropic-hosted (or cloud-marketplace) | MCP-first, any server plugs in, state is inspectable on disk |

## Where myOS is more powerful

### 1. Coordination is a kernel service with addressable state

Cowork coordinates sub-agents inside the desktop app. The state lives in the app. Outside tools cannot inspect it, replay it, or integrate with it. myOS exposes every agent row, every session event, every decision as files and HTTP endpoints. An external observer (dashboard, SIEM, automation, another agent) can read the coordination state the same way it reads any other file.

Evidence: `api/routers/agents.py` (55 agent endpoints), `.ostk/sessions/*/events.jsonl`, `.ostk/decisions.jsonl`, ostk verb set (shell, search, lock, session, needle, hay, decide, near, handoff, tack).

### 2. Git-safe parallel code edits

Both products run sub-agents. Only myOS boxes code-editing sub-agents into git worktrees so many of them can edit the same repo in parallel without conflicting. When they finish, `worktree-reaper.sh` classifies each worktree as absorbed (diff vs main is empty) or unique, and deletes only the absorbed ones. Cowork's audience is not doing parallel git work, so this is not a feature they need. For us, it is the whole game.

Evidence: `api/services/spawn_isolation.py`, `scripts/worktree-reaper.sh`, `POST /agents/fleets/spawn`.

### 3. Per-turn rule enforcement, not per-session approval

Cowork's safety model is plan approval at the start of a session. Scope permissions are defined per sub-agent. Both are start-of-run gates. myOS enforces rules on every single model call via a UserPromptSubmit hook that reinjects standing rules, live agent snapshots, and an unacknowledged-completion check. Behavior drift is caught mid-turn, not after the fact.

Evidence: `.claude/hooks/standing-rules.sh`, feedback memory entries, MEMORY.md meta-rule.

### 4. Per-sub-agent budget

`POST /agents/{name}/budget` sets a token cap per spawned sub-agent. Cost tracking rolls up per session. Cowork tracks spend at the org level (enterprise dashboard) but not at the sub-agent level: the product page notes "Cowork consumes limits faster than Chat" and "coordinates multiple sub-agents and tool calls," but the billing surface is the plan's overall rate limit, not a per-agent quota.

Evidence: `/agents/{name}/budget` endpoints, `app/src/pages/CostTracking.tsx`.

### 5. Knowledge capture model

hay (raw insight) to idea (shaped) to needle (tracked). Decisions are recorded separately. Memory is typed (user, feedback, project, reference) and indexed. Cowork does not advertise a structured capture layer. Plugins can carry Skills (domain knowledge baked in) but there is no hay-style raw capture at the session level.

Evidence: `ostk hay`, `ostk idea`, `ostk needle`, `ostk decide`, memory system under `~/.claude/projects/.../memory/`.

### 6. Open substrate

Cowork runs plugins from an Anthropic-curated catalog or private marketplaces approved by org admins. myOS accepts any MCP server, full stop. The tradeoff is polish: Cowork's plugin marketplace is a UX win for non-engineers. For anyone who wants to wire up an unvetted MCP server today, myOS just works.

## Where Cowork has something myOS does not (candidate needles)

Each one is a candidate needle when the comparison comes up again after the upcoming implementation plan and ostk release.

1. **Mobile pairing.** Send a task from your phone, desktop executes, result comes back. Research preview for Pro and Max. myOS has no mobile surface.
2. **Plan approval as a first-class toggleable UX.** Cowork's "plan then approve then execute" flow is clean. The toggle to turn it off is also clean. myOS has plans inside agent transcripts but no dedicated approval surface.
3. **Template-matched doc and deck generation.** "Combine your company templates with source materials. Claude follows your formatting conventions and produces polished docs, decks, or reports." myOS has fcp-slides and fcp-gdocs but no template library.
4. **Receipts and invoices to spreadsheet.** Packaged extraction workflow. myOS has fcp-sheets and computer-use but no packaged flow.
5. **Plugin marketplace.** Curated install experience, with private org marketplaces.
6. **Chrome as a fallback connector.** Cowork reaches for connectors first, Chrome second, screen last. myOS has computer-use but no explicit browser-as-fallback tier ordering.
7. **Zoom MCP connector.** Meeting summaries, action items, transcripts into workflows.
8. **DocuSign and FactSet connectors.** Domain-specific enterprise integrations. Lower priority for us.
9. **RBAC.** Role-based access controls for Enterprise.
10. **OpenTelemetry export.** Streams tool calls, file access, and approval states to SIEM. Compliance and security teams care about this.
11. **Org spend limits and Analytics API.** Group-level spend cap plus external API for analytics.
12. **Multi-cloud deployment.** Bedrock, Vertex AI, Microsoft Foundry. myOS is self-hosted only.
13. **Desktop app packaging.** If the audience is non-engineers, the dev-server dance is a barrier.
14. **Claude Design plugin.** Brief-to-design-system flow. myOS has stitch (screens) but no equivalent.
15. **Dispatch mode.** Computer use as a named, scoped operating mode with its own safety wrapper. myOS has computer-use but no scoped mode.

## Where myOS could take an explicit counter-position

A few places where Cowork's product decisions create openings for myOS to say "we are different on purpose."

- **Regulated workloads.** Cowork explicitly excludes HIPAA, FedRAMP, and FSI. myOS is self-hosted and can be deployed inside a compliant boundary. That is a real differentiator for healthcare, government, and regulated finance buyers.
- **Coordination state as data.** Cowork sub-agents coordinate inside a closed app. myOS coordination state is files. Anyone who wants to build dashboards, observability, cross-session automation, or replay tooling can do that on top of myOS. Cowork cannot offer that.
- **Engineer primary.** Cowork is explicitly for non-coding work. myOS is at home in a repo. Where they drew a boundary, we can own the other side cleanly.

## How to pitch this

Lead with coordination. Every conversation about myOS starts with "we coordinate a fleet of agents with state on disk and git-safe parallel commits," not "we call Claude too."

When someone asks what myOS does that Cowork does not:
- Git-safe parallel code edits via worktrees.
- Coordination state on disk, inspectable and addressable from outside the product.
- Rules enforced every turn, not just at plan approval.
- Per-sub-agent budget, not just per-org.
- Any MCP server, not a curated list.
- Works inside regulated boundaries.

When someone asks what Cowork does that myOS does not, be honest:
- Mobile pairing.
- Non-engineer-ready desktop app with plan approval UX.
- Packaged extraction and template-matched doc workflows.
- RBAC, OpenTelemetry, org analytics.
- Multi-cloud deployment options.
- Private plugin marketplace.

Those are product polish and distribution choices we have not prioritized yet. The coordination substrate underneath myOS is what makes the hard problems (many agents editing the same repo, rule enforcement at scale, inspectable coordination state) tractable. Cowork's architecture does not address those problems because its audience does not have them.

## Sources

All URLs fetched during the research for this doc.

- [Claude Cowork product page (claude.com)](https://claude.com/product/cowork) — quoted directly in this draft
- [Claude Cowork product page (anthropic.com)](https://www.anthropic.com/product/claude-cowork)
- [Cowork research preview blog (claude.com)](https://claude.com/blog/cowork-research-preview)
- [Claude Cowork GA announcement (testingcatalog.com)](https://www.testingcatalog.com/anthropic-launches-claude-cowork-in-general-availability/)
- [Enterprise Cowork connectors (axios.com)](https://www.axios.com/2026/01/30/ai-anthropic-enterprise-claude)
- [Microsoft Copilot Cowork (winbuzzer.com)](https://winbuzzer.com/2026/03/10/microsoft-copilot-cowork-anthropic-claude-m365-agent-xcxwbn/)
- [Claude Design launch (techcrunch.com)](https://techcrunch.com/2026/04/17/anthropic-launches-claude-design-a-new-product-for-creating-quick-visuals/)
- [Agent Teams in Claude Code (code.claude.com)](https://code.claude.com/docs/en/agent-teams)
- [Claude Managed Agents blog (claude.com)](https://claude.com/blog/claude-managed-agents)
