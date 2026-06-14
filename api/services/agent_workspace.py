"""Shared workspace service for agent collaboration.

Running agents can post findings, questions, results, and context here so
other agents can read them without going through the user.

Data is stored in ~/.youros/agent_workspace.json, never in the repo.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from services.youros_paths import youros_home

MYOS_DIR = youros_home()
WORKSPACE_FILE = MYOS_DIR / "agent_workspace.json"

VALID_TYPES = {"finding", "question", "result", "context"}


class WorkspaceMessage:
    def __init__(
        self,
        *,
        id: str,
        agent_name: str,
        content: str,
        message_type: str,
        timestamp: str,
    ):
        self.id = id
        self.agent_name = agent_name
        self.content = content
        self.message_type = message_type
        self.timestamp = timestamp

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_name": self.agent_name,
            "content": self.content,
            "message_type": self.message_type,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkspaceMessage":
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            agent_name=data.get("agent_name", "unknown"),
            content=data.get("content", ""),
            message_type=data.get("message_type", "finding"),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )


class AgentWorkspaceService:
    def _load(self) -> list[WorkspaceMessage]:
        MYOS_DIR.mkdir(parents=True, exist_ok=True)
        if not WORKSPACE_FILE.exists():
            return []
        try:
            raw = json.loads(WORKSPACE_FILE.read_text())
            return [WorkspaceMessage.from_dict(d) for d in raw]
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, messages: list[WorkspaceMessage]) -> None:
        MYOS_DIR.mkdir(parents=True, exist_ok=True)
        WORKSPACE_FILE.write_text(
            json.dumps([m.to_dict() for m in messages], indent=2)
        )

    def post_message(
        self,
        agent_name: str,
        content: str,
        message_type: str = "finding",
    ) -> WorkspaceMessage:
        if message_type not in VALID_TYPES:
            message_type = "finding"
        messages = self._load()
        msg = WorkspaceMessage(
            id=str(uuid.uuid4()),
            agent_name=agent_name,
            content=content,
            message_type=message_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        messages.append(msg)
        self._save(messages)
        return msg

    def get_messages(
        self,
        since_id: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> list[WorkspaceMessage]:
        messages = self._load()

        if agent_name:
            messages = [m for m in messages if m.agent_name == agent_name]

        if since_id:
            # Find the index of the message with since_id and return everything after it
            ids = [m.id for m in messages]
            if since_id in ids:
                idx = ids.index(since_id)
                messages = messages[idx + 1:]
            # If since_id not found, return all (conservative)

        return messages

    def clear_workspace(self) -> int:
        messages = self._load()
        count = len(messages)
        self._save([])
        return count

    def get_summary(self) -> str:
        """Return a compact text summary for injecting into agent prompts."""
        messages = self._load()
        if not messages:
            return ""

        lines: list[str] = ["Shared workspace context from other running agents:"]
        # Group by agent for readability
        by_agent: dict[str, list[WorkspaceMessage]] = {}
        for m in messages:
            by_agent.setdefault(m.agent_name, []).append(m)

        for agent, msgs in by_agent.items():
            lines.append(f"\n[{agent}]")
            for m in msgs:
                lines.append(f"  [{m.message_type.upper()}] {m.content}")

        return "\n".join(lines)


agent_workspace_service = AgentWorkspaceService()
