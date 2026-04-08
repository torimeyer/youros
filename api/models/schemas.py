from pydantic import BaseModel
from typing import Dict, List, Optional


class TaskCreate(BaseModel):
    title: str
    priority: str = "P1"
    description: Optional[str] = None


class TaskClose(BaseModel):
    reason: Optional[str] = None


class TaskUpdate(BaseModel):
    priority: Optional[str] = None


class TaskReorder(BaseModel):
    task_id: str
    new_priority: str
    position: int


class TaskLink(BaseModel):
    target: str
    relation: str = "blocks"


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
        "Chat": True, "Tasks": True, "Hay/Ideas": True,
        "Agents": True, "Projects": True, "Docs": True,
        "Transcripts": False,
    }
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
    morning_briefing_enabled: bool = True


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


class AgentNudge(BaseModel):
    message: str


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


class DocDraft(BaseModel):
    title: str


class DocPromote(BaseModel):
    path: str


class DocDecompose(BaseModel):
    path: str
