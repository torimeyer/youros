# Advanced Tabs Research: Agents + Specs

## Current Agents Page

Tabs: Active, Recent, Templates (+ Delegate, Workspace in power-user mode)

Existing infrastructure:
- **Agent state**: full lifecycle tracking in agent_state.json (spawned_at, completed_at, status, transcript_bytes, current_step, worktree)
- **Duration stats**: rolling median/p75 from completed agents (agent_duration_stats.py)
- **Pattern analysis**: heuristic analyzer with per-template stats, success rates, model recommendations (agent_patterns.py, 763 lines)
- **Agentfiles**: declarative agent definitions with model, prompt, tools, token_limit, boot, pin (agentfile_parser.py)
- **Marketplace**: 25+ templates across 5 categories (agentMarketplace.ts)
- **Chat thread**: per-agent messaging via mailbox (AgentChatThread.tsx)
- **Undo**: revert agent changes (agent_undo.py, UndoAgentChange.tsx)

## Current Specs Page

Tabs: All, Drafts, Ready, Building, Done (status-based filtering)

Existing infrastructure:
- **Spec lifecycle**: draft -> ready -> in-progress -> complete
- **Templates**: 9 built-in with pre-written goals, AC, tasks, user inputs
- **AC generation**: AI-powered, Haiku model, 3 criteria per spec
- **Build flow**: decompose -> spawn per-task builders
- **Import/Export**: spec-kit YAML format
- **Drive sync**: publish/sync/pull to Google Docs (new in v3.9.0)
- **Task assignments**: in-memory map of task_id -> agent_name for spinner tracking

## Proposed: Agents Advanced Tab

### Section 1: Insights (from existing agent_patterns.py)
Already built but not surfaced in the UI:
- Per-template success rate, median duration, best model
- Recommendations: underbudgeted, wrong model, slow template
- Consecutive failure detection
- Proven templates (consistently successful)

**Build cost**: Low. Backend exists. Need a frontend component.

### Section 2: Performance Dashboard
- Total agents run (last 7d, 30d, all time)
- Success rate trend
- Average duration by template
- Token usage breakdown (if available from transcripts)

**Build cost**: Medium. Duration stats exist. Need aggregation + chart component.

### Section 3: Batch Operations
- Cancel all active agents
- Retry all failed agents from last N hours
- Clean up stale worktrees (scripts/worktree-reaper.sh exposed via API)

**Build cost**: Low. Cancel-all exists. Retry and worktree cleanup need thin endpoints.

### Section 4: Agent Comparison
- Same task, different models (Opus vs Sonnet vs Haiku)
- Side-by-side results
- Cost/quality/speed tradeoff display

**Build cost**: Large. Needs multi-spawn orchestration. Defer to Tier 2.

## Proposed: Specs Advanced Tab

### Section 1: SDD Wizard (the main event)
Multi-step wizard that produces a richer spec:
1. Problem statement
2. Scope (in/out)
3. Success criteria (AI-assisted)
4. Technical context (auto-populated)
5. Review + Build

**Build cost**: Medium-Large. New wizard component + enhanced backend AC generation.

### Section 2: Spec Health Score
Rate each spec on completeness:
- Has problem statement? (+20)
- Has success criteria? (+20)
- Has scope boundaries? (+20)
- Has non-goals? (+20)
- Has technical context? (+20)

Show as a simple progress ring. "This spec is 60% ready for agents."

**Build cost**: Low. Parse markdown sections, score, render.

### Section 3: Spec Analytics
- How many specs completed vs abandoned
- Average time from draft to complete
- Which templates produce the best results

**Build cost**: Medium. Need to mine spec file timestamps and status transitions.

## Recommended Build Order

**Wave 1 (this session):**
1. Agents Advanced tab with Insights section (agent_patterns.py already exists)
2. Specs SDD Wizard (the core ask)

**Wave 2 (follow-up):**
3. Spec Health Score
4. Agents Performance Dashboard
5. Batch Operations

**Wave 3 (later):**
6. Spec Analytics
7. Agent Comparison
