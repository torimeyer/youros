from pydantic import BaseModel, Field
from typing import Dict, List, Optional


class TaskCreate(BaseModel):
    title: str
    priority: str = "P1"
    description: Optional[str] = None
    # When this task was created during a Claude Code session, the
    # caller can pass the parent session's UUID so the Tasks page can
    # show a "N tasks created in this session" count on the session row.
    parent_session_id: Optional[str] = None
    # When this task IS the auto-filed session task created by the
    # SessionStart hook, the caller can pass the session UUID so the
    # row can link back to the transcript.
    session_id: Optional[str] = None
    # Where this task came from. One of "slack", "meeting", "email", "manual".
    source: Optional[str] = None
    # Opaque identifier from the source system, max 500 chars.
    source_ref: Optional[str] = None
    # Strategic theme this task belongs to, from the org pillars list.
    # Null means untagged, which is the default and matches today's behavior.
    pillar: Optional[str] = None


class TaskClose(BaseModel):
    reason: Optional[str] = None


class TaskUpdate(BaseModel):
    priority: Optional[str] = None
    reason: Optional[str] = None
    # Rename a task's title and/or description. Both are optional so
    # callers can update one field without touching the other. Backfill
    # scripts use this to migrate legacy "Claude Code session ..." titles
    # to the friendlier "Session in <cwd>" format.
    title: Optional[str] = None
    description: Optional[str] = None
    # Free-text notes the user can attach to a task. Stored in the task
    # record alongside title and description. Set to empty string to clear.
    notes: Optional[str] = None
    # Move a task between "open" (waiting in the queue) and
    # "in_progress" (someone is actively working on it now). Closing and
    # pausing are handled by dedicated endpoints so they are intentionally
    # excluded from the set accepted here.
    status: Optional[str] = None
    # Strategic theme tag. Set to empty string to clear, same as notes.
    pillar: Optional[str] = None


class TaskReorder(BaseModel):
    task_id: str
    new_priority: str
    position: int


class TaskLink(BaseModel):
    target: str
    relation: str = "blocks"


class TaskDeleteAll(BaseModel):
    status: str = "all"  # "open", "closed", or "all"
    priority: Optional[List[str]] = None  # e.g. ["P0", "P1"]
    labels: Optional[List[str]] = None   # label IDs


class HayCreate(BaseModel):
    thought: str
    template_id: Optional[str] = None


class HayConvert(BaseModel):
    straw: str
    priority: str = "P1"
    delete_hay: Optional[bool] = False


class Settings(BaseModel):
    onboarded: bool = False
    # Transient step index saved before an OAuth redirect so the wizard
    # can land back on the right step after the browser returns. Cleared
    # immediately after it is consumed (set to null). None = no pending restore.
    onboarding_step: Optional[int] = None
    # Absolute path where torios stores user-visible files (briefs,
    # roadmaps, automation outputs). None means fall back to the default
    # ~/.youros/files. See api/services/files_dir.py.
    files_dir: Optional[str] = None
    os_name: str = "yourOS"
    # Instance name shown in the app. yourOS is the product; every user
    # names their own instance. Default matches the product name so the
    # app still reads correctly for a user who never changes it. Tori's
    # instance is "toriOS". Set by the onboarding wizard and the
    # Settings page.
    instance_name: str = "yourOS"
    user_name: str = ""
    dark_mode: bool = True
    accent_color: str = "blue"
    default_model: str = "@claude"
    use_ostk_terms: bool = False
    tour_complete: bool = False
    whats_new_last_seen: str = ""
    agents_last_viewed: str = ""
    custom_agent_templates: List[Dict] = []
    auto_template_matching: bool = True
    features: Dict[str, bool] = {
        "Chat": True, "Tasks": True,
        "Agents": True, "Activity": True, "Projects": True,
        "Drive": True, "Calendar": True, "Gmail": True,
        "Slack": True, "GitHub": True, "Specs": True,
        "Automations": True, "Cost Tracking": True,
        # Legacy keys kept for existing user settings that may still reference them.
        "Transcripts": True,
    }
    power_user_mode: bool = False
    # When on, an approved plan is turned into a spec first (the spec-first
    # workflow) before any implementation. On by default.
    plans_become_specs: bool = True
    # When on, ToriOS watches incoming iMessages and may act on them (e.g. a
    # text like "spawn diagnose for task 1654" starts an agent). Off by
    # default: acting on inbound messages is side-effectful and must be opted
    # into. The poller baselines on first pass, so enabling never replays the
    # backlog as a spawn burst.
    inbound_imessage_routing_enabled: bool = False
    notifications: Dict[str, bool] = {
        "agent_complete": True, "agent_needs_input": True,
        "agent_failed": True, "approval_needed": True,
    }
    quiet_hours_enabled: bool = True
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "07:00"
    mcp_servers: List[Dict] = []
    auto_label_tasks: bool = True
    chat_backend_preference: str = "auto"
    briefing_enabled: bool = True
    gmail_unread_at_top: bool = True
    chat_memory_enabled: bool = True
    chat_receipts_gate_enabled: bool = True
    # When True, every chat message is sent to both Claude and Gemini
    # in parallel and the chat panel renders both replies in adjacent
    # columns. Persisted so the choice survives across sessions.
    side_by_side_enabled: bool = False
    # When True, chat requests routed to Gemini use the local `gemini` CLI
    # instead of the Google GenAI SDK.
    use_gemini_cli: bool = False
    # Compression is always on. This field is kept for internal flexibility and
    # schema compatibility but is intentionally not surfaced in the UI.
    context_compression_enabled: bool = True
    # Order and visibility of home dashboard widgets. Any widget id present
    # in this list renders, in the given order. Any widget id missing from
    # the list is hidden. This lets users build a simple custom dashboard
    # without a full layout engine.
    dashboard_widgets: List[str] = [
        "briefing",
        "focus_first",
        "todays_focus",
        "quick_launch",
        "next_meeting",
        "day_summary",
    ]
    # Appearance settings
    sidebar_position: str = "left"        # "left" | "right"
    compact_mode: bool = False
    font_size: str = "medium"             # "small" | "medium" | "large"
    icon_style: str = "filled"            # "filled" | "outlined"
    card_style: str = "glass"             # "glass" | "solid"
    dashboard_layout: str = "full"        # "full" | "focus"
    status_dot_style: str = "dots"        # "dots" | "badges"
    greeting_style: str = "time"          # "time" | "quote" | "none"
    instance_mode: str = "personal"       # "personal" | "team"
    # Standing instructions: a free-form block the user writes once and
    # every chat, agent spawn, and task follows. Prepended to the system
    # prompt in chat and to the agent prompt at spawn time. Empty by default.
    standing_instructions: str = ""
    default_confluence_space: str = ""
    # Wall-clock cap (in seconds) applied to each builder subagent spawned
    # when the user presses Build it on a spec. Default of 600 (10 minutes)
    # gives a normal run enough headroom for real work. Power users can
    # tighten this for faster feedback or loosen it for long-running specs.
    spec_build_deadline_s: int = 600
    default_provider: str = "claude"  # "claude" | "gemini"
    # ADHD mode configuration. Stored here so GET /settings always returns it
    # and the Settings page toggle can hydrate correctly on fresh installs.
    # The dedicated /adhd/config endpoints read and write this field directly.
    adhd_mode: Dict = {
        "enabled": False,
        "check_in_seconds": 30,
        "focus_mode": False,
    }


class MCPServer(BaseModel):
    name: str
    url: str
    auth_token: str = ""
    enabled: bool = True


class AgentSpawn(BaseModel):
    name: str
    prompt: str = ""
    model: str = "sonnet"
    budget: float = 2.0
    status: Optional[str] = None
    description: Optional[str] = None
    task: Optional[str] = None
    source: Optional[str] = None
    spawned_at: Optional[str] = None
    transcript_path: Optional[str] = None
    token_limit: Optional[int] = None  # max tokens before auto-stop
    # Optional Agentfile template name (e.g. "saa") to resolve by template
    # instead of by agent name. When set, the matching agents/<template>.agent
    # file supplies the PROMPT, AC gates, TOOL list, and LIMIT lines.
    template: Optional[str] = None
    # Links a step agent back to the workflow run that spawned it. Set by
    # services.workflows._execute_step so the reconcile pass can auto-cancel
    # orphans when the parent workflow ends.
    workflow_run_id: Optional[str] = None
    # Tier-based model selection. When set, ``model_tier`` is resolved via
    # ``services.model_routing.resolve_model`` and overrides ``model``.
    # Accepted values: "sonnet" (default), "opus", "haiku".
    # Use "opus" only for genuinely hard tasks; the spawn path will auto-
    # escalate from Sonnet if the agent emits NEEDS_OPUS or exits non-zero.
    model_tier: Optional[str] = None
    # True when this register call is the Claude Code PreToolUse hook
    # pre-registering a subagent row before the subagent itself boots.
    # Used to distinguish "hook row" from "subagent self-register" so a
    # later self-register under a different name can MERGE into the hook
    # row instead of creating a duplicate. Unused outside the
    # claude-code source. Defaults to None/False.
    hook_preregister: Optional[bool] = None
    # Originating task id. When a spawn is triggered from a task (e.g.
    # the Tasks page "Implement with saa" button), the UI passes the
    # task id here so the backend can flip the task's status to
    # in_progress once the agent starts. Optional so spawns that are
    # not tied to a task (ad-hoc, command palette) keep working.
    task_id: Optional[str] = None
    # Spawn isolation hint. When set to "worktree", the spawn forks a git
    # worktree under .claude/worktrees/agent-<agent_id> and runs the
    # subagent there so its commits stay isolated from the main tree. When
    # None, services.spawn_isolation.decide_isolation picks a sensible
    # default from the description/prompt verbs. "none" forces the main
    # worktree.
    isolation: Optional[str] = None
    # Originating needle id. When a spawn is triggered to work on an
    # ostk needle (e.g. "→922"), pass the bare or arrow-prefixed id
    # here so the task list endpoint can overlay in_progress status
    # while a live agent is working, without writing back to issues.jsonl.
    needle_id: Optional[str] = None
    # Mandatory path-lock declaration for edit-capable spawns. Every
    # entry is a file path or glob the spawn promises to touch. Before
    # the subprocess starts the server reserves each glob in a
    # process-wide registry; a second spawn asking for any overlapping
    # glob is rejected with HTTP 409 so parallel edit bursts cannot
    # race. Research-only spawns (isolation resolved to "none") may
    # pass ["*"] as an explicit "I won't edit anything" opt-out, or
    # omit the field entirely. Edit-capable spawns (isolation resolved
    # to "worktree") MUST pass a non-empty, non-wildcard list or the
    # server returns HTTP 400. See services.spawn_isolation.
    locks: Optional[List[str]] = None
    # Spawn provenance: identifies the user session and message that
    # triggered this spawn so peer sessions can distinguish
    # user-authored requests from autonomous hook auto-fires.
    # Set by task-isolation-bridge.sh from CLAUDE_SESSION_ID and
    # the tool_use_id in the hook payload. Both are nullable so
    # existing callers that omit them keep working unchanged.
    originating_session_id: Optional[str] = None
    originating_user_message_id: Optional[str] = None
    # True when the spawn was explicitly requested by a human (both
    # originating_session_id and originating_user_message_id are
    # present). Peer cancel-all guards check this field before
    # cancelling an agent they did not spawn themselves.
    user_authored: Optional[bool] = None
    # True when this spawn originates from a What's Next / follow-on action
    # button on a completed agent panel. These are content-generation tasks
    # (email drafts, slide decks, flashcards) — NOT code edits. The
    # decide_isolation verb heuristic misreads "write an email" / "create a
    # slide deck" as code-edit tasks because "write" and "create" are in
    # CODE_EDIT_VERBS. Setting follow_on=True forces isolation="none" before
    # the heuristic runs so these spawns are never rejected for missing locks.
    follow_on: Optional[bool] = None
    # Originating spec path. When a spawn is triggered to implement a spec
    # (e.g. a worktree agent spawned for a spec-linked needle), pass the
    # spec's relative path here (e.g. "~/.youros/specs/my-feature.md").
    # The spawn path calls _set_spec_status to flip the frontmatter from
    # "spec" (ready) to "building" so the Specs page shows the in-flight
    # state immediately. Optional so spawns with no spec association keep
    # working unchanged. (→1420)
    spec_id: Optional[str] = None
    # Opt-in flag to route the spawn through `ostk run <Agentfile>` instead
    # of the bespoke claude-code subprocess path. Default False preserves
    # existing behaviour for all callers. Set True only to pilot the kernel
    # spawn path without touching anything in the normal flow.
    use_ostk_run: bool = False
    # Dry-run: when True (and use_ostk_run is True), passes --dry-run to
    # `ostk run` so no process is actually spawned. Safe for integration
    # tests and smoke checks.
    dry_run: bool = False
    # Optional back-channel for completion notifications. Set by the text
    # bridge when a user starts an agent via iMessage so the agent can text
    # back when it finishes. Format: {"kind": "imessage", "chat_id": <int>}.
    # Only "imessage" kind is supported; ignored/skipped for any other value.
    notify: Optional[dict] = None


class AgentNudge(BaseModel):
    message: str
    # Kind tags the nudge so the UI and the agent can distinguish a
    # plain follow-up message from a course-correcting instruction.
    # The /nudge endpoint defaults to "user_message" and the /correct
    # endpoint forces "correction" so the agent's mailbox poll can
    # treat the two cases differently.
    kind: Optional[str] = None


class AgentNudgeReply(BaseModel):
    message: str
    in_reply_to: Optional[str] = None
    # Kind tags the reply so the inline chat UI can render the warm
    # ack bot reply ("On it, give me a sec.") in a lighter tone and
    # the substantive subagent reply as the primary bubble. Real
    # subagent replies default to "real" when omitted; the ack bot
    # always sets "ack" through the chat_ack_bot module.
    kind: Optional[str] = None


class GrantApprove(BaseModel):
    ttl: int = 0
    scope: Optional[str] = None


class GrantDeny(BaseModel):
    reason: str = "not permitted"


class CommitCreate(BaseModel):
    message: str
    needle: Optional[str] = None
    spec: Optional[str] = None
    section: Optional[str] = None
    agent: Optional[str] = None


class ThreadCreate(BaseModel):
    name: str
    needle_ids: Optional[List[str]] = None


class ThreadUpdate(BaseModel):
    name: Optional[str] = None


class SpecDraft(BaseModel):
    title: str = Field(..., max_length=500)
    fallback_ac: bool = Field(False)
    kind: str = Field("task")  # "task" (default) or "spec"


class SpecPromote(BaseModel):
    path: str = Field(..., max_length=1000)


class SpecDecompose(BaseModel):
    path: str = Field(..., max_length=1000)


class ResolveDuplicateBody(BaseModel):
    keep_id: str
    discard_id: str


class ResolveDuplicatesBulkBody(BaseModel):
    threshold: float = 0.8


# Backward-compatible aliases so any code still using the old names works.
DocDraft = SpecDraft
DocPromote = SpecPromote
DocDecompose = SpecDecompose
