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
    os_name: str = "myOS"
    user_name: str = ""
    dark_mode: bool = True
    accent_color: str = "blue"
    default_model: str = "@claude"
    use_ostk_terms: bool = False
    tour_complete: bool = False
    whats_new_last_seen: str = ""
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
    chat_memory_enabled: bool = True
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
    # Caller-side demo mode opt-in. When True, the spawn forces Haiku, the
    # compact mailbox block, and a 90 second wall-clock cap, even if the
    # matching agentfile does not have ``LIMIT demo_mode true``. Used by
    # demo surfaces (workflows materialised from built-in templates, spec
    # /build during a live demo, "build it" chat chain) so a single slow
    # agent never blows the demo budget.
    demo_mode: Optional[bool] = None
    # Caller marks the model as explicitly chosen by the user. When True,
    # demo_mode still enforces the 90 second wall-clock cap and the compact
    # mailbox block, but does NOT silently downgrade the model to Haiku.
    # The template-detail edit modal sets this when the user picks a model
    # other than the template default, so their choice wins over the
    # agentfile's ``LIMIT demo_mode true`` default. When False or absent,
    # demo_mode keeps its original behavior and forces Haiku.
    honor_explicit_model: Optional[bool] = None
    # Override the demo-mode wall-clock cap (default 90 seconds). Used
    # when several demo agents need to fit inside a single 90 second
    # budget: a 3-step built-in workflow caps each step at 30 seconds so
    # the whole workflow lands under 90s. Ignored unless demo_mode is
    # also True. Range is clamped server-side (min 5, max 90).
    deadline_seconds: Optional[int] = None
    # Links a step agent back to the workflow run that spawned it.
    # Set by run_workflow() so the reconcile pass can auto-cancel orphans.
    workflow_run_id: Optional[str] = None
    # Caller-side demo mode opt-in. When True, the spawn forces Haiku,
    # the compact mailbox block, and a 90 second wall-clock cap, even if
    # the matching agentfile does not have ``LIMIT demo_mode true``. Used
    # by demo surfaces (workflows materialised from built-in templates,
    # spec /build during a live demo, "build it" chat chain) so a single
    # slow agent never blows the demo budget.
    demo_mode: Optional[bool] = None
    # True when this register call is the Claude Code PreToolUse hook
    # pre-registering a subagent row before the subagent itself boots.
    # Used to distinguish "hook row" from "subagent self-register" so a
    # later self-register under a different name can MERGE into the hook
    # row instead of creating a duplicate. Unused outside the
    # claude-code source. Defaults to None/False.
    hook_preregister: Optional[bool] = None


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
