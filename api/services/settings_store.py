import json
from pathlib import Path

from models.schemas import Settings

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
        return data

    def save(self, data: dict):
        if "features" in data and isinstance(data["features"], dict):
            data["features"] = _normalize_features(data["features"])
        SETTINGS_PATH.write_text(json.dumps(data, indent=2))

    def get(self, key: str, default=None):
        return self.load().get(key, default)

    def update(self, partial: dict):
        current = self.load()
        if "features" in partial and isinstance(partial["features"], dict):
            partial["features"] = _normalize_features(partial["features"])
        current.update(partial)
        self.save(current)


settings_store = SettingsStore()
