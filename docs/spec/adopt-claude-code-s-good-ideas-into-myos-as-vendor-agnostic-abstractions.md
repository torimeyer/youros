---
status: spec
title: Adopt Claude Code's good ideas into myOS as vendor-agnostic abstractions
created_at: 2026-05-24T00:49:38Z
promoted_at: 2026-05-24T00:53:34Z
---

# Adopt Claude Code's good ideas into myOS as vendor-agnostic abstractions

## Problem

myOS is a vendor-agnostic agent platform. The chat layer already supports Claude Code, Gemini CLI, and Anthropic API (with `chat_backend_preference` setting). But the subagent layer is hard-wired to spawn `claude` CLI ([api/routers/agents.py:4218](/Users/torimeyer/claude/torios/api/routers/agents.py)). Every feature added on top of that path widens the coupling. Without a portable abstraction, users on Gemini CLI get a degraded experience (chat works, agents broken). Users on future runtimes (open-source, ChatGPT) won't be supported at all.

The opportunity: Claude Code has a rich feature surface (hooks, skills, permissions, memory, plan mode, task graphs, agent teams). Most of these are runtime-neutral concepts that just happen to ship in Claude Code first. We can adopt them as first-class myOS primitives, then let each runtime "light up" the ones it natively supports. Mid-plan discovery: [os-tack/prism](https://github.com/os-tack/prism) already solves the static config layer (canonical `.agents/` compiled to per-tool config), so myOS only needs to own the runtime layer.

## Goals

- Decouple the subagent spawn layer from Claude Code so Gemini CLI (and future runtimes) work end-to-end.
- Adopt prism for the static config layer with a boundary table to prevent double-source-of-truth.
- Build myOS-native runtime primitives for: Agent Teams, Channels two-way, runtime event bus consolidation.
- Enable phone-based agent spawning via iMessage as the highest-leverage user-visible feature.
- Avoid re-implementing things that already exist (8 event buses; ostk run path; agents/*.agent definitions).

## Non-goals

- Not a re-implementation of Claude Code in myOS.
- Not a wrapper around the Claude API.
- Not adding Claude-specific features (Routines, claude.ai connectors, push notifications).
- Not Bedrock / Vertex / Foundry auth (out of scope for current user setup).
- Not multi-cloud orchestration.

## Context

The chat layer is already multi-vendor. The subagent layer is hard-coded to `claude` CLI. Every feature added on top of the Claude-Code spawn path widens the gap. The `MYOS_SPAWN_USE_OSTK_RUN=1` escape hatch already exists at [agents.py:4619](/Users/torimeyer/claude/torios/api/routers/agents.py); the work is to close its gaps and make it the default.

## The asymmetry to design around

| Layer | State today | Action |
|---|---|---|
| Chat | Two-provider (claude_code_provider, gemini_cli_provider, anthropic_api) with `chat_backend_preference` setting | Keep, extend with feature-flag capability map |
| Subagent spawn | Hard-wired to `claude --print --output-format stream-json` | **This is the gap.** Promote `ostk run <Agentfile>` to default |
| Worktrees | Path `.claude/worktrees/agent-*` baked into [scripts/worktree-reaper.sh](/Users/torimeyer/claude/torios/scripts/worktree-reaper.sh) and [api/services/spawn_isolation.py](/Users/torimeyer/claude/torios/api/services/spawn_isolation.py) | Rename to `.myos/worktrees/` or make path configurable |
| Hooks | Claude-Code-only ([.claude/settings.json](/Users/torimeyer/claude/torios/.claude/settings.json) wires 5 events to 11 scripts) | Define myOS event bus that any runtime can publish to; Claude Code hooks become one publisher |
| Memory | Auto-memory lives in `~/.claude/projects/<proj>/memory/MEMORY.md` | Mirror to `~/.myos/memory/` so non-Claude runtimes share it |
| Skills | None in myOS today; live in `~/.claude/skills/` | prism's `.agents/skills/` projects to each tool |
| Permissions | ostk has `trust`/`grant`; Claude Code has hierarchical `settings.json` | Surface ostk permissions through a runtime-neutral API |

## Full gap analysis: every Claude Code feature, mapped to myOS today

**Legend:** ✅ used | 🟡 partial | ❌ not used. For each gap, the vendor-neutral abstraction is in the right column.

### Subagents & multi-agent coordination

| Claude Code feature | myOS today | Vendor-neutral abstraction (if gap) |
|---|---|---|
| `Agent` tool spawn | ✅ via /api/agents/spawn, Claude-coupled | (the spawn path itself is AC3) |
| Worktree isolation per subagent | ✅ at `.claude/worktrees/` | Move to `.myos/worktrees/`; abstract path |
| Subagent tool ACL (`tools` whitelist) | 🟡 via Agentfile `TOOL` directive | Already vendor-neutral in Agentfile; surface in spawn API |
| Subagent isolation modes (none/worktree/forked) | 🟡 worktree only | Add "forked" mode (inherit parent context) and "none" |
| **Agent Teams** (shared task list, mailbox, TeammateIdle, roles) | 🟡 **half-built.** Have mailbox/nudge ([agents.py:6124](/Users/torimeyer/claude/torios/api/routers/agents.py)) + parallel spawn + heartbeat. Missing: shared task list visible to all teammates, role-based teammate identity, TeammateIdle quality gate, direct teammate-to-teammate messaging. | **"Team" primitive in myOS**: parent Task + N child Tasks + shared task graph + inter-agent inbox. Coordination lives in myOS, not in the runtime. See AC5. |
| Background agents view (`claude agents`) | ✅ Running Agents panel + WS delta bus | Already vendor-neutral |

### Skills & invocation

| Claude Code feature | myOS today | Vendor-neutral abstraction (if gap) |
|---|---|---|
| Skill registry (`.claude/skills/*/SKILL.md`) | ❌ no custom skills, only superpowers plugin | **Solved by prism** (`.agents/skills/`) |
| Slash command system (`/diagnose`, `/build`) | ❌ not at myOS layer | prism projects to each tool's native command syntax |
| Dynamic context injection (`` !`shell` `` preprocessing) | ❌ not used | Pre-execute `!`-prefixed lines in skill body via `shell.run` before send |
| Frontmatter controls (`disable-model-invocation`, `effort`, `paths`, `allowed-tools`) | ❌ not used | YAML frontmatter; honored by prism + myOS |
| `arguments` / `$0`, `$1` substitution | ❌ not used | Template substitution before send |
| Skill scope hierarchy (user/project/managed) | ❌ not used | prism: `~/.agents/` then `<repo>/.agents/` |

### Hooks & event automation

Claude Code has ~30 hook events. myOS uses 5 (`SessionStart`, `PreToolUse:Agent`, `PreToolUse:all`, `PostToolUse`, `SessionEnd`). Unused, with vendor-neutral mapping:

| Hook event | Vendor-neutral abstraction |
|---|---|
| `TeammateIdle` | Task-idle event when a teammate subagent exits with team still open. Re-prompt team lead via mailbox. |
| `ConfigChange`, `FileChanged`, `CwdChanged` | Filesystem watch publisher. ostk already audits gen_table; expose as SSE stream. |
| `PreCompact` / `PostCompact` | ostk context compaction is implicit; add hook surface around it. |
| `WorktreeCreate` / `WorktreeRemove` | Publish from [spawn_isolation.py](/Users/torimeyer/claude/torios/api/services/spawn_isolation.py). |
| `TaskCreated` / `TaskCompleted` | Publish from `ostk work add` / `ostk work close`. |
| `Stop`, `StopFailure`, `PermissionDenied` | Useful for retry-with-backoff policy and audit. |
| `Elicitation` / `ElicitationResult` | MCP user-input. Already supported via prompts. |
| `InstructionsLoaded` | When CLAUDE.md / MEMORY.md re-reads. Useful for cache invalidation. |
| `PostToolBatch` | Batch-of-parallel-tools complete. Useful for atomic checks. |
| `Notification` | Already in ostk audit; expose as event. |
| `Setup` | One-time install hook. Already partly in `install.sh`. |

**Abstraction:** prism handles compile-time hooks (`.agents/hooks/*.yaml`); myOS owns runtime events via the consolidated bus (AC6).

### MCP

| Claude Code feature | myOS today | Vendor-neutral abstraction (if gap) |
|---|---|---|
| MCP servers (stdio/HTTP/SSE) | ✅ ostk MCP wires this | Already standard |
| Tool search / deferral | ✅ used | Already standard |
| Resources (`@server:protocol://path/resource`) | ❌ not used | Expose ostk audit logs, Tasks, decisions as MCP resources |
| Prompts (`/mcp__server__prompt`) | ❌ not used | ostk verbs could ship as MCP prompts |
| `headersHelper` (dynamic auth) | ❌ not used | Per-server token-refresh script; standard MCP pattern |

### Memory & context

| Claude Code feature | myOS today | Vendor-neutral abstraction (if gap) |
|---|---|---|
| CLAUDE.md persistent instructions | ✅ vendor-neutral format | prism generates from `.agents/context.md`; mirrors to GEMINI.md, AGENTS.md |
| Auto memory (`~/.claude/.../MEMORY.md`) | ✅ used | Move source-of-truth to `~/.myos/memory/`; sync to Claude path |
| Path-scoped rules (`.claude/rules/*.md` with `paths:` frontmatter) | ❌ not used | prism's scoped subdirs (`.agents/src/billing/`) |
| Nested CLAUDE.md (monorepo hierarchy) | ❌ not used | Walk parent/child dirs at session start; concatenate in tree order |
| `@path` imports | 🟡 partial | Pre-process all CLAUDE.md/MEMORY.md reads to resolve @ imports |
| 1M context window | ✅ used (Opus 4.7[1m]) | Claude API feature; Gemini has its own large window |

### Permissions & sandboxing

| Claude Code feature | myOS today | Vendor-neutral abstraction (if gap) |
|---|---|---|
| Settings hierarchy (managed→user→project→local) | 🟡 partial | Generalize via ostk grant scope (already supports this shape) |
| Tool-specific specifier patterns (`Bash(npm run *)`, `Read(.env)`) | 🟡 partial | ostk grant has glob patterns; expose via `/api/permissions` |
| Permission modes (default/plan/acceptEdits/auto/bypass) | 🟡 plan-mode only | Lift mode to session-level state in myOS, signal to provider |
| Sandboxing (Linux/WSL2 fs+network isolation) | ❌ not used | macOS lacks equivalent. Postpone. |
| `apiKeyHelper` script | ❌ not used | Per-provider token-refresh hook in RuntimeProvider |

### Session continuity

| Claude Code feature | myOS today | Vendor-neutral abstraction (if gap) |
|---|---|---|
| Resume | 🟡 partial | Already in ostk audit + gen_table |
| Fork (`--fork-session`, `/branch`) | ❌ not used | New session snapshots current gen_table; cheap with ostk's gen tracking |
| Rewind / checkpoint (Esc-Esc, `/rewind`) | ❌ not used | ostk audit can rebuild any prior state; expose `/rewind` in UI |

### Reasoning control

| Claude Code feature | myOS today | Vendor-neutral abstraction (if gap) |
|---|---|---|
| Effort levels (low/medium/high/xhigh/max) | 🟡 partial (NEEDS_OPUS escalation) | **`RuntimeEffort` enum** mapped per provider: Claude (effort flag), Gemini (thinking budget), OpenAI (model selection o1/o3) |
| `ultrathink` keyword | ❌ not used | Pre-scan prompt for keyword, bump effort one tier on the next call |
| Extended thinking toggle (Option+T) | ❌ not surfaced | Toggle in myOS chat panel; provider-mapped flag |
| `opusplan` hybrid (Opus plan + Sonnet exec) | ❌ not used | Plan-mode forces high-tier model; exec-mode allows downgrade |

### Channels (external event input)

| Claude Code feature | myOS today | Vendor-neutral abstraction (if gap) |
|---|---|---|
| Channels: push events into a running session (Telegram/Discord/iMessage/webhook) | 🟡 **half-built.** Integration pages for Gmail, iMessage, Slack, Jira read data, but they don't push events to a running agent session. | **myOS Channel primitive**: incoming source (iMessage row, Slack message, webhook POST) → routing rule (which agent/skill/Task handles it) → response delivered back through the same channel. See AC1. |
| Two-way reply through same channel | ❌ not used | Same primitive; response goes back via channel adapter |

### Tasks & scheduling

| Claude Code feature | myOS today | Vendor-neutral abstraction (if gap) |
|---|---|---|
| TaskCreate / TaskList | ✅ ostk Tasks | Already vendor-neutral |
| `blockedBy` dependencies | 🟡 memory rule says we should, wiring unclear | Add `blocked_by` field to Task schema; auto-spawn on resolution |
| Background tasks | ✅ mcp__ostk__spawn | Already vendor-neutral |
| Monitor tool (mid-session event stream) | ✅ mcp__ostk__monitor | Already vendor-neutral |
| CronCreate / scheduled tasks | 🟡 schedule_auto_labels only | Generalize to "scheduled prompt" primitive |
| ScheduleWakeup (self-paced loops) | ❌ not used | Could power "agent checks back in 20s" already in memory rules |
| Routines (claude.ai hosted) | ❌ pass | Pass; would re-couple to Anthropic |

### Output / UI

| Claude Code feature | myOS today | Vendor-neutral abstraction (if gap) |
|---|---|---|
| Output styles (Default/Proactive/Explanatory/Learning + custom) | ❌ not used | **Prompt-style file** in `~/.myos/styles/`; injected as system-prompt prefix per chat. Ship "torios voice" as default. |
| Status line | ❌ N/A | myOS frontend IS the status surface |
| Fullscreen, inline diffs, terminal UI | N/A | Terminal-only; myOS frontend handles |

### Plugins / marketplace

| Claude Code feature | myOS today | Vendor-neutral abstraction (if gap) |
|---|---|---|
| Plugin manifest (plugin.json + bundled skills/agents/hooks/MCP) | ❌ not used | **Agentfile bundle**: directory with `.agent` files + skills + hooks + MCP shims; install via `ostk pkg install` |
| Marketplace (official/community/private) | ❌ not used | ostk decide log + Agentfile registry could be the seed |

### Voice / dictation

| Claude Code feature | myOS today | Vendor-neutral abstraction (if gap) |
|---|---|---|
| Voice hold-to-record (Claude Code CLI only) | ❌ not used | **Web Speech API in myOS frontend**. Vendor-neutral, in-browser. |

### GitHub integration

| Claude Code feature | myOS today | Vendor-neutral abstraction (if gap) |
|---|---|---|
| Auto-fix PRs (Claude responds to comments) | ❌ not used | webhook → channel → skill (`/fix-pr`) → spawn agent → push commit |
| Code review checks (inline comments) | ❌ not used | Skill `/code-review --comment` partially exists |
| Claude in CI (`-p` mode) | ❌ not used | `ostk run <agent>.agent` works headless; same pattern in CI |

### Cost & observability

| Claude Code feature | myOS today | Vendor-neutral abstraction (if gap) |
|---|---|---|
| `/usage` token + cost view | 🟡 CostTracking page exists | Extend to per-provider rollup |
| OTEL export | ❌ not used | Optional; not urgent |
| Telemetry | 🟡 partial | Already partially in ostk audit |

### Highest-leverage gaps (the headline)

Five gaps are both high-value AND have most of the vendor-neutral substrate already built in ostk/myOS. Prism shifts a few of these from "build in myOS" to "declare in `.agents/`":

1. **Agent Teams**: half-built. Mailbox + parallel spawn + heartbeat already there. Need: shared task list, role-based teammates, idle gate. Pure myOS runtime; prism doesn't touch this.
2. **Skills**: was a total gap; **now solved by prism**. Drop the plan to build a skill registry. Put skills in `.agents/skills/<n>/SKILL.md`, let prism project them to `.claude/skills/`, Cursor rules, Gemini settings, etc.
3. **myOS event bus**: split by prism, **now reframed as consolidation** after ultraplan caught that myOS already has ~8 event buses. Compile-time hooks become a prism concern (`.agents/hooks/*.yaml`). Runtime events stay in myOS but AC6 gates them on replacing ≥2 of the 8 existing buses rather than adding a 9th silo.
4. **Channels (two-way)**: half-built. iMessage/Slack/Gmail integrations already read; missing the write-back loop. Pure myOS runtime. **AC1 (MVP slice ships first, zero prereqs).** Spawn agents from your phone.
5. **Output styles**: total gap. Easy win; "torios voice" already encoded in CLAUDE.md, just needs to be a prompt-prefix file. Deferred to Tier 2.

## Critical update: prism solves the config layer

> **Status: proposed, not yet installed.** As of this spec, `prism` is not installed in this repo and no `.agents/` directory exists. All prism-related items below describe the intended design. Verify installation and `.agents/` structure before treating any prism behavior as established fact.

After drafting the gap analysis, Scott pointed at [os-tack/prism](https://github.com/os-tack/prism) (v0.9.0, schema v2 in soak before v1.0; last push 2026-05-18; same org as ostk). It does exactly what we'd otherwise build for the **config-compilation layer**: a Go tool that compiles a canonical `.agents/` directory into per-tool config files for Claude Code, Cursor, Gemini CLI, Cline, Continue, Windsurf, Copilot, and AGENTS.md.

**What prism does (we stop reinventing):**

- Canonical `.agents/` directory with named primitives: `context.md`, `skills/<n>/SKILL.md`, `commands/<n>.md`, `agents/<n>.md`, `hooks/<n>.yaml`, `permissions.yaml`, `mcp.yaml`. Scoped subdirs (e.g. `src/billing/`) project as cascade. Layered config: `~/.agents/` global merges with project `.agents/`.
- `prism compile` writes per-tool files (symlinks where identical, merges where transformation needed, preserves user keys in `settings.json`/`.mcp.json`).
- `prism init --from claude` imports our existing `.claude/` config in one step. Round-trip tested. No greenfield rewrite.
- `prism scope-guard` and `prism perms-guard` are bash wrappers prism emits to enforce path-scoping and allow/deny/ask permissions on tools whose native runtime lacks the primitive.
- Honest capability matrix per (tool × primitive × field): `native` / `degraded` / `unsupported`, with warnings on degradation. Visible with `prism capabilities`.

**What prism does NOT do (still myOS's job at runtime):**

- Subagent spawning. Prism compiles agent *definitions*; it doesn't run them.
- Mailbox / nudge routing between running agents.
- Heartbeat tracking, ghost reaping, status updates to the Agents page.
- Channel adapters (iMessage/Slack/Gmail/webhook two-way I/O at runtime).
- Runtime event bus (UI live updates from running agents).
- Task / task tracking (ostk owns this).
- Output styles applied at chat send time (prism handles compile-time context; styles are runtime injection).
- Session continuity (fork/rewind via ostk gen_table).
- The `RuntimeProvider` abstraction for spawning across providers (Claude CLI vs Gemini CLI vs Anthropic API).

**The clean split:**

| Layer | Owner | Concerns |
|---|---|---|
| **Config layer** | **prism** | What files exist where, what skills/hooks/permissions/MCP look like to each tool, how `.agents/` projects to `.claude/` / `.cursor/` / `GEMINI.md` / etc. |
| **Runtime layer** | **myOS** | What happens when an agent runs: spawning, mailbox, events, teams, channels, memory, sessions, the Agents page UI. |

## Recommended approach: the RuntimeProvider abstraction

Extend the existing chat-provider interface to cover the full runtime surface, then map Claude Code features onto it. Three new methods on the provider interface:

```python
class RuntimeProvider(Protocol):
    # Existing
    async def stream_chat(...) -> AsyncIterator[Event]: ...

    # New: subagent spawn (today is Claude-only)
    async def spawn_subagent(prompt, tools, model, isolation, capability_hints) -> AgentHandle: ...

    # New: capability advertisement
    def features(self) -> set[Feature]: ...  # {HOOKS, PLAN_MODE, WORKTREE, MONITOR, SKILLS, ...}

    # New: event subscription (hooks)
    def subscribe(event: Event, handler: Callable) -> None: ...
```

The myOS layer above this owns the **vendor-neutral primitives**: Tasks, memory, the event bus, the worktree convention, the permissions store. Each provider lights up what it can.

## Acceptance criteria

**Reordered after ultraplan review (2026-05-23).** Original order put config refactors first, which buried the highest-leverage shippable work (phone-based agent spawning) at position 6. New order leads with the MVP slice that needs zero prereqs, lets config (AC2) and runtime work (AC3, AC4) run in parallel, and reframes the event bus (AC6) as consolidation since myOS already has ~8 event buses (a 9th would be negative work).

### AC1: Channels MVP (phone-based agent spawning via iMessage)

Ship this first; requires NONE of AC2-AC6. All pieces already exist: `POST /api/agents/spawn`, `POST /api/agents/{name}/nudge`, `POST /api/imessage/send` ([routers/imessage.py:173](/Users/torimeyer/claude/torios/api/routers/imessage.py)), iMessage read ([routers/imessage.py:214](/Users/torimeyer/claude/torios/api/routers/imessage.py)), existing `agent_events` bus.

- [ ] Inbound iMessage poller built in new [api/services/channel_intent_parser.py](/Users/torimeyer/claude/torios/api/services/channel_intent_parser.py)
- [ ] Intent parser supports "spawn X to do Y" / "nudge Z" / "status"
- [ ] Routing rules table in new [api/routers/channel_routing.py](/Users/torimeyer/claude/torios/api/routers/channel_routing.py)
- [ ] Agent reply sent back via existing `POST /api/imessage/send` on completion
- [ ] Smoke test: text "spawn diagnose for Task 1654", agent spawns, completion reply lands in iMessage
- [ ] Settings page has Channel Routing Rules panel

### AC2: Adopt prism for the config layer, with a boundary table FIRST

Before running `prism init --from claude`, write a boundary table listing every existing config surface and whether prism owns it, generates it, or leaves it to myOS. Without this, `prism init` creates `.agents/` that drifts from what `ostk run` actually reads.

- [ ] Boundary table written and committed: CLAUDE.md, GEMINI.md (doesn't exist yet), `.claude/hooks/*.sh`, `.mcp.json.example`, `settings.default.json:mcp_servers`, `agents/*.agent`
- [ ] `agents/*.agent` (consumed by `ostk run` via `agentfile_parser.build_capabilities_summary` at [api/services/agentfile_parser.py:1101](/Users/torimeyer/claude/torios/api/services/agentfile_parser.py)) vs `.agents/agents/<n>.md` collision decision documented (which is canonical, whether one generates the other)
- [ ] `prism init --from claude --dry-run` runs cleanly with no boundary collisions
- [ ] CLAUDE.md content preserved (not flattened) in projection
- [ ] GEMINI.md generated and verified to work with `gemini` CLI
- [ ] `prism compile` wired into [scripts/dev-backend.sh](/Users/torimeyer/claude/torios/scripts/dev-backend.sh), [scripts/install.sh](/Users/torimeyer/claude/torios/scripts/install.sh), and a pre-commit hook
- [ ] prism version pinned in repo (v0.8.2 today; v0.9.x when schema v2 leaves soak)

### AC3: Promote `ostk run <Agentfile>` to default subagent spawn

Mostly already built: wrapper `ostk.run_agentfile()` exists at [api/services/ostk.py:3723](/Users/torimeyer/claude/torios/api/services/ostk.py), spawn path lives behind `MYOS_SPAWN_USE_OSTK_RUN=1` at [agents.py:4619](/Users/torimeyer/claude/torios/api/routers/agents.py). For read-only / research agents it's effectively shippable today. Runs in parallel with AC4.

- [ ] Worktree isolation gap closed
- [ ] Scaffold-commit watcher gap closed (`worktree_path` metadata at [agents.py:1463](/Users/torimeyer/claude/torios/api/routers/agents.py))
- [ ] `OSTK_PROJECT_ROOT` short-cwd gap closed
- [ ] **Exit criteria for removing bespoke-path fallback** defined and documented at [agents.py:4546-4592](/Users/torimeyer/claude/torios/api/routers/agents.py). Without this the flag lives forever and "default" silently isn't
- [ ] Default flag flipped to use ostk run for read-only / research agents first
- [ ] Verification: spawn 3 different agent types, all land via ostk run, none silently fall back

### AC4: `RuntimeProvider` interface with capability `features()`

Renamed from "v2" since there's no formal v1. Today `provider_detection.py` does credential detection only; `claude_code_provider.py` and `gemini_cli_provider.py` share `stream_chat()` shape but no Protocol. This is the first formal interface. Runs in parallel with AC3.

- [ ] New [api/services/runtime_provider.py](/Users/torimeyer/claude/torios/api/services/runtime_provider.py) created with Protocol definition
- [ ] `features()` enum enumerated: `subagents`, `hooks`, `streaming`, `isolation`, `worktrees`, `plan_mode`, `monitor`
- [ ] [api/services/claude_code_provider.py](/Users/torimeyer/claude/torios/api/services/claude_code_provider.py) implements the Protocol
- [ ] [api/services/gemini_cli_provider.py](/Users/torimeyer/claude/torios/api/services/gemini_cli_provider.py) implements the Protocol
- [ ] [api/routers/agents.py:4475](/Users/torimeyer/claude/torios/api/routers/agents.py) refactored to call `provider.spawn_subagent(...)`
- [ ] Test: each provider reports the correct feature set; switching providers does not break spawn

### AC5: Agent Teams primitive on top of the existing mailbox

Substrate already there: parallel spawn ([agents.py:5055](/Users/torimeyer/claude/torios/api/routers/agents.py)), nudge ([agents.py:8229](/Users/torimeyer/claude/torios/api/routers/agents.py)), reply ([agents.py:8401](/Users/torimeyer/claude/torios/api/routers/agents.py)), `agent_mailbox_instruction()` at [:764](/Users/torimeyer/claude/torios/api/routers/agents.py), heartbeat, ack-bot. Synergy with AC1: "spawn a team to review PR #123" from your phone.

- [ ] New [api/services/teams.py](/Users/torimeyer/claude/torios/api/services/teams.py) with Team primitive (parent Task + child teammates + shared task graph)
- [ ] Role-based teammate identity (`security_reviewer`, `frontend_lead`, etc.)
- [ ] TeammateIdle quality gate: teammate cannot exit while team's parent Task is open
- [ ] `agent_mailbox_instruction()` extended with team-shared section
- [ ] UI panel showing team membership + per-teammate status on Agents page
- [ ] Smoke test: spawn 3-teammate team via "spawn a team to review PR #X" from iMessage, observe all 3 register, work in parallel, complete

### AC6: Runtime event bus, as consolidation of 8 existing buses

myOS already has `agent_events`, `session_events`, `workflow_events`, `dashboard_events`, `notifications_events`, `locks_events`, `grants_events`, `calendar_events`. Adding a 9th silo is negative work. Gate: this item ships only if it replaces ≥2 existing buses. Ships LAST.

- [ ] At least 2 of the 8 existing buses migrated to consolidated [api/services/event_bus.py](/Users/torimeyer/claude/torios/api/services/event_bus.py)
- [ ] New event types added: `agent.spawned`, `agent.completed`, `channel.message_received`, `Task.created`, `Task.closed`, `team.member_idle`
- [ ] SSE stream exposed at `GET /api/events`
- [ ] Running Agents panel subscribes to consolidated bus
- [ ] `ls api/services/*_events.py` shows ≤6 buses (down from 8)

### Verification commands

- [ ] `ls api/services/*_events.py` confirms bus count is down (started at ~8)
- [ ] `grep -n "MYOS_SPAWN_USE_OSTK_RUN" api/routers/agents.py` shows fallback removal commit landed
- [ ] MVP smoke for AC1: `POST /api/agents/spawn` from a script, observe `agent_events` delta, then `POST /api/imessage/send`. This proves the loop without any new infra
- [ ] For AC2: `prism init --from claude --dry-run` and diff proposed `.agents/` against existing `agents/*.agent` to surface boundary collisions before committing
- [ ] `scripts/dev-backend.sh` and `scripts/dev-frontend.sh` come up clean
- [ ] Spawn a subagent via the UI: registers, runs, commits, lands
- [ ] Invoke a skill in chat (`/build`, `/diagnose`) works in both runtimes
- [ ] Worktree lands at `.myos/worktrees/agent-*` (new path)
- [ ] `scripts/e2e_smoke.sh` passes
- [ ] Repeat with `MYOS_RUNTIME=gemini`: same tests pass, capability map shows reduced features (no plan mode, no monitor)

## Deferred work

### Tier 2: high-value, build after AC1-AC6

- **Memory in `~/.myos/memory/`, mirrored to Claude.** Auto-memory becomes a myOS-owned directory, written by any runtime, read by all. ostk already audits file writes, so attribution is free.
- **Permissions as a runtime-neutral API.** ostk's `trust`/`grant` already exists; surface it through `GET /api/permissions` and a `Permission` field that providers consult before invoking tools.
- **Plan mode as a workflow primitive, not a runtime feature.** myOS gates a "planning" session state; the runtime is told "you are planning, write to this path, don't modify files." Works in Gemini CLI the same way.
- **Task graph with `blockedBy`.** Add `blocked_by` field to Tasks; auto-spawn when blockers resolve. Memory rule `feedback_autospawn_unblocked_queue.md` already says we should be doing this.
- **Output styles.** "torios voice" already encoded in CLAUDE.md; ship as a prompt-prefix file in `~/.myos/styles/`.

### Tier 3: defer further

- Plugins / marketplace (premature until skills + Agentfiles stabilize).
- Status line (myOS frontend IS the status surface).
- IDE integrations (myOS frontend already IS the IDE).
- Remote control / push notifications (out of scope until hosted backend).

### Pass entirely

- Routines (claude.ai scheduled agents). Would re-couple to Anthropic.
- Bedrock / Vertex / Foundry auth. Multi-cloud is non-goal for current user setup.
- Compaction. ostk already manages context; trust the kernel.

## What changes for users

| User has | Today | After this spec |
|---|---|---|
| Claude Code subscription | All features work | All features work, faster (subagent layer uses ostk substrate underneath) |
| Gemini CLI only | Chat works, agents broken | Chat + subagents + skills + memory + hooks all work |
| ChatGPT/open-source (future) | N/A | Provider plugs into `RuntimeProvider`; same features light up |
| Phone (any device) | N/A | Text iMessage → spawn agent → reply delivered back |

## Critical files to touch

**Backend:**
- [api/routers/agents.py](/Users/torimeyer/claude/torios/api/routers/agents.py): refactor spawn path to call provider
- [api/services/spawn_isolation.py](/Users/torimeyer/claude/torios/api/services/spawn_isolation.py): decouple from `.claude/` path
- [api/services/provider_detection.py](/Users/torimeyer/claude/torios/api/services/provider_detection.py): extend with feature capability map
- [api/services/ostk.py](/Users/torimeyer/claude/torios/api/services/ostk.py): existing `run_agentfile()` wrapper to extend
- New: [api/services/runtime_provider.py](/Users/torimeyer/claude/torios/api/services/runtime_provider.py)
- New: [api/services/teams.py](/Users/torimeyer/claude/torios/api/services/teams.py)
- New: [api/services/channel_intent_parser.py](/Users/torimeyer/claude/torios/api/services/channel_intent_parser.py)
- New: [api/routers/channel_routing.py](/Users/torimeyer/claude/torios/api/routers/channel_routing.py)
- New: [api/services/event_bus.py](/Users/torimeyer/claude/torios/api/services/event_bus.py) (consolidated)

**Scripts:**
- [scripts/worktree-reaper.sh](/Users/torimeyer/claude/torios/scripts/worktree-reaper.sh): scan path becomes configurable
- [.claude/hooks/*.sh](/Users/torimeyer/claude/torios/.claude/hooks): repoint to event bus (after prism adoption)
- [scripts/dev-backend.sh](/Users/torimeyer/claude/torios/scripts/dev-backend.sh): wire `prism compile`
- [scripts/install.sh](/Users/torimeyer/claude/torios/scripts/install.sh): install prism, wire `prism compile`

**Reused from existing code:**
- [agents/*.agent](/Users/torimeyer/claude/torios/agents): Agentfile format already provider-neutral
- ostk substrate: Tasks, audit, gen_table, locks, sessions, trust/grant
- [api/services/gemini_cli_provider.py](/Users/torimeyer/claude/torios/api/services/gemini_cli_provider.py): template for new providers

## References

- Original plan file: [/Users/torimeyer/.claude/plans/does-it-make-sense-enchanted-stroustrup.md](/Users/torimeyer/.claude/plans/does-it-make-sense-enchanted-stroustrup.md)
- Ultraplan refinement session: claude.ai session_01TwgPQicvLpf7jAyhkdo3Bx
- prism: [github.com/os-tack/prism](https://github.com/os-tack/prism)
- Related memory rules: `feedback_autospawn_unblocked_queue.md`, `feedback_specs_use_myos_pipeline.md`
- Recent related work: iMessage attributedBody decoder (commit fa1f19e, 2026-05-22, →1648)
