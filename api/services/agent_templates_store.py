"""Store for agent templates (PM-focused).

Built-in templates are hardcoded here. Custom templates are stored in
~/.myos/agent_templates.json so they survive git pulls without being clobbered.

Public surface:
- ``AGENT_TEMPLATES_PATH`` -- module-level Path constant (required by data-safety check)
- ``AgentTemplatesStore`` -- load / save / CRUD for custom agent templates
- ``agent_templates_store`` -- singleton instance
- ``BUILTIN_AGENT_TEMPLATES`` -- list of built-in template dicts (read-only)
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

AGENT_TEMPLATES_PATH = Path.home() / ".myos" / "agent_templates.json"

# No built-in PM-focused templates. Templates are now installed per-user
# based on their chosen persona during onboarding (see app/src/data/
# agentMarketplace.ts and the OnboardingWizard Persona step).
BUILTIN_AGENT_TEMPLATES: list[dict] = []


class AgentTemplatesStore:
    """CRUD for custom (user-defined) agent templates stored in ~/.myos/agent_templates.json."""

    def _ensure_exists(self) -> None:
        AGENT_TEMPLATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not AGENT_TEMPLATES_PATH.exists():
            AGENT_TEMPLATES_PATH.write_text("[]")

    def list_custom(self) -> list[dict]:
        self._ensure_exists()
        try:
            return json.loads(AGENT_TEMPLATES_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, templates: list[dict]) -> None:
        self._ensure_exists()
        AGENT_TEMPLATES_PATH.write_text(json.dumps(templates, indent=2))

    def create(self, data: dict) -> dict:
        templates = self.list_custom()
        new_template = {
            "id": f"custom-{uuid.uuid4().hex[:8]}",
            "name": data.get("name", "").strip(),
            "description": data.get("description", "").strip(),
            "icon": data.get("icon", "smart_toy").strip(),
            "prompt_template": data.get("prompt_template", "").strip(),
            "model": data.get("model", "sonnet").strip(),
            "budget": float(data.get("budget", 2.0)),
            "builtin": False,
        }
        templates.append(new_template)
        self._save(templates)
        return new_template

    def update(self, template_id: str, data: dict) -> Optional[dict]:
        templates = self.list_custom()
        for i, t in enumerate(templates):
            if t.get("id") == template_id:
                for field in ("name", "description", "icon", "prompt_template", "model"):
                    if field in data:
                        t[field] = str(data[field]).strip()
                if "budget" in data:
                    t["budget"] = float(data["budget"])
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
        return BUILTIN_AGENT_TEMPLATES + self.list_custom()

    def get_by_id(self, template_id: str) -> Optional[dict]:
        for t in self.list_all():
            if t.get("id") == template_id:
                return t
        return None


agent_templates_store = AgentTemplatesStore()
