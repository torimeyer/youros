"""Store for custom idea templates.

Built-in templates are hardcoded here. Custom templates are stored in
~/.youros/templates.json so they survive git pulls without being clobbered.

Public surface:
- ``TEMPLATES_PATH`` -- module-level Path constant (required by data-safety check)
- ``TemplatesStore`` -- load / save / CRUD for custom templates
- ``templates_store`` -- singleton instance
- ``BUILTIN_TEMPLATES`` -- list of built-in template dicts (read-only)
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

from services.atomic_io import atomic_write_json, atomic_write_text

TEMPLATES_PATH = Path.home() / ".youros" / "templates.json"

BUILTIN_TEMPLATES: list[dict] = [
    {
        "id": "builtin-feature",
        "name": "Feature idea",
        "description": "Describe something new you want to build.",
        "icon": "build",
        "prompt_template": "I want to build [what] for [who], because [problem]",
        "breakdown_hint": "This is a product feature request. Break it into design, build, and ship tasks.",
        "builtin": True,
    },
    {
        "id": "builtin-problem",
        "name": "Problem to solve",
        "description": "Start with a pain point, not a solution.",
        "icon": "warning",
        "prompt_template": "I keep running into [problem] when [situation]",
        "breakdown_hint": "This is a problem statement. Break it into tasks that investigate and resolve the root cause.",
        "builtin": True,
    },
    {
        "id": "builtin-research",
        "name": "Research spike",
        "description": "Explore something before committing to a direction.",
        "icon": "science",
        "prompt_template": "I need to understand [topic] before I can [decision]",
        "breakdown_hint": "This is a research spike. Break it into tasks that gather information and produce a summary or recommendation.",
        "builtin": True,
    },
    {
        "id": "builtin-meeting",
        "name": "Meeting follow-up",
        "description": "Turn a meeting outcome into action.",
        "icon": "meeting_room",
        "prompt_template": "From [meeting name], the thing I own is [outcome]",
        "breakdown_hint": "This is a meeting follow-up action item. Break it into the concrete steps needed to deliver the outcome.",
        "builtin": True,
    },
    {
        "id": "builtin-integration",
        "name": "Integration",
        "description": "Connect two systems together.",
        "icon": "cable",
        "prompt_template": "Connect [A] to [B] so that [benefit]",
        "breakdown_hint": "This is a system integration. Break it into tasks for auth, data mapping, error handling, and testing.",
        "builtin": True,
    },
    {
        "id": "builtin-website",
        "name": "Build a website",
        "description": "Start here if you want a website but have no idea where to begin.",
        "icon": "language",
        "prompt_template": "I want a website for [what it's for] that helps people [do what]. I [have / don't have] a domain name. My budget is [rough amount or free].",
        "breakdown_hint": "This is a website build for someone with no coding background. Break it into plain-language tasks anyone can follow: deciding what the site needs to do, picking a no-code platform (Squarespace, Webflow, Carrd, or similar), choosing and customizing a template, writing the key pages (home, about, contact), adding any images or content, connecting a domain if they have one, and publishing. Every task description must be written as if the reader has never built a website before. No code, no jargon.",
        "builtin": True,
    },
]


class TemplatesStore:
    """CRUD for custom (user-defined) templates stored in ~/.youros/templates.json."""

    def _ensure_exists(self) -> None:
        TEMPLATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not TEMPLATES_PATH.exists():
            atomic_write_text(TEMPLATES_PATH, "[]")

    def list_custom(self) -> list[dict]:
        self._ensure_exists()
        try:
            return json.loads(TEMPLATES_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, templates: list[dict]) -> None:
        self._ensure_exists()
        atomic_write_json(TEMPLATES_PATH, templates)

    def create(self, data: dict) -> dict:
        templates = self.list_custom()
        new_template = {
            "id": f"custom-{uuid.uuid4().hex[:8]}",
            "name": data.get("name", "").strip(),
            "description": data.get("description", "").strip(),
            "icon": data.get("icon", "lightbulb").strip(),
            "prompt_template": data.get("prompt_template", "").strip(),
            "breakdown_hint": data.get("breakdown_hint", "").strip(),
            "builtin": False,
        }
        templates.append(new_template)
        self._save(templates)
        return new_template

    def update(self, template_id: str, data: dict) -> Optional[dict]:
        templates = self.list_custom()
        for i, t in enumerate(templates):
            if t.get("id") == template_id:
                for field in ("name", "description", "icon", "prompt_template", "breakdown_hint"):
                    if field in data:
                        t[field] = str(data[field]).strip()
                templates[i] = t
                self._save(templates)
                return t
        return None

    def delete(self, template_id: str) -> bool:
        templates = self.list_custom()
        updated = [t for t in templates if t.get("id") != template_id]
        if len(updated) == len(templates):
            return False
        self._save(updated)
        return True

    def list_all(self) -> list[dict]:
        """Built-ins first, then custom templates."""
        return BUILTIN_TEMPLATES + self.list_custom()


templates_store = TemplatesStore()
