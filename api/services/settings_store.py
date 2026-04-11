import json
from pathlib import Path

from models.schemas import Settings
from services.atomic_io import atomic_write_json

SETTINGS_PATH = Path.home() / ".myos" / "settings.json"

# Map old lowercase feature keys to canonical TitleCase labels used by the UI.
_FEATURE_KEY_MAP: dict[str, str] = {
    "chat": "Chat",
    "tasks": "Tasks",
    "hay": "Hay/Ideas",
    "agents": "Agents",
    "projects": "Projects",
    "docs": "Docs",
    "transcripts": "Transcripts",
}


def _normalize_features(features: dict[str, bool]) -> dict[str, bool]:
    """Convert any old lowercase feature keys to canonical TitleCase labels."""
    normalized: dict[str, bool] = {}
    for key, value in features.items():
        canonical = _FEATURE_KEY_MAP.get(key, key)
        normalized[canonical] = value
    return normalized


def _migrate_briefing_keys(data: dict) -> tuple[dict, bool]:
    """One-shot migration of the old morning_briefing keys to briefing.

    Renames ``morning_briefing_enabled`` to ``briefing_enabled`` and
    replaces ``"morning_briefing"`` with ``"briefing"`` inside the
    ``dashboard_widgets`` list. Returns the updated data and a flag
    indicating whether any change was made so callers can persist it.
    """
    changed = False
    if "morning_briefing_enabled" in data:
        # Only copy the old value if the new key is not already set.
        if "briefing_enabled" not in data:
            data["briefing_enabled"] = data["morning_briefing_enabled"]
        data.pop("morning_briefing_enabled", None)
        changed = True

    widgets = data.get("dashboard_widgets")
    if isinstance(widgets, list) and "morning_briefing" in widgets:
        new_widgets: list = []
        for w in widgets:
            if w == "morning_briefing":
                if "briefing" not in new_widgets:
                    new_widgets.append("briefing")
            elif w not in new_widgets:
                new_widgets.append(w)
        data["dashboard_widgets"] = new_widgets
        changed = True

    return data, changed


class SettingsStore:
    def __init__(self):
        self._ensure_exists()

    def _ensure_exists(self):
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not SETTINGS_PATH.exists():
            self.save(Settings().model_dump())

    def load(self) -> dict:
        data = json.loads(SETTINGS_PATH.read_text())
        if "features" in data and isinstance(data["features"], dict):
            data["features"] = _normalize_features(data["features"])
        # One-shot migration: the setting used to be called
        # ``morning_briefing_enabled`` and the dashboard widget id used
        # to be ``morning_briefing``. Carry the old user values over to
        # the new keys, drop the old ones, and rewrite the file in place
        # so future loads skip this branch.
        data, migrated = _migrate_briefing_keys(data)
        if migrated:
            atomic_write_json(SETTINGS_PATH, data)
        # Backfill any fields missing from an older settings.json with the
        # pydantic schema defaults. This is how new server-backed settings
        # like ``tour_complete`` show up for users who were onboarded before
        # the field existed, without rewriting their file on disk. New
        # writes (``save`` / ``update``) still carry only what the caller
        # passed plus what was already on disk.
        defaults = Settings().model_dump()
        for key, default_value in defaults.items():
            if key not in data:
                data[key] = default_value
        return data

    def save(self, data: dict):
        if "features" in data and isinstance(data["features"], dict):
            data["features"] = _normalize_features(data["features"])
        atomic_write_json(SETTINGS_PATH, data)

    def get(self, key: str, default=None):
        return self.load().get(key, default)

    def update(self, partial: dict):
        current = self.load()
        if "features" in partial and isinstance(partial["features"], dict):
            partial["features"] = _normalize_features(partial["features"])
        current.update(partial)
        self.save(current)


settings_store = SettingsStore()
