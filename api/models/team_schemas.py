from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class TeamMember(BaseModel):
    id: str  # email address
    display_name: str
    joined_at: str  # ISO8601


class TeamConfig(BaseModel):
    team_repo: str  # absolute local path to shared git repo
    member_id: str  # this user's email
    display_name: str
    category_defaults: dict = {
        "decisions": "shared",
        "needles": "shared",
        "knowledge": "private",
        "transcripts": "private",
    }
    auto_sync: bool = True
