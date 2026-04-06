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


class HayCreate(BaseModel):
    thought: str


class HayConvert(BaseModel):
    straw: str
    priority: str = "P1"
    delete_hay: Optional[bool] = False


class Settings(BaseModel):
    os_name: str = "myOS"
    user_name: str = ""
    dark_mode: bool = True
    accent_color: str = "blue"
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    default_model: str = "@claude"
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


class AgentNudge(BaseModel):
    message: str
