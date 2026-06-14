import json
import os
from pathlib import Path

from models.schemas import Settings
from services.atomic_io import atomic_write_json
from services.youros_paths import youros_home

# Allow test suites and e2e launchers to redirect all settings I/O to an
# isolated directory by setting YOUROS_HOME in the environment before this
# module is imported.  When unset, the real user home is used as before.
# This is the primary isolation knob for →1345 (e2e settings pollution).
_YOUROS_HOME = Path(os.environ["YOUROS_HOME"]) if "YOUROS_HOME" in os.environ else youros_home()
SETTINGS_PATH = _YOUROS_HOME / "settings.json"

# Map old lowercase feature keys to canonical TitleCase labels used by the UI.
_FEATURE_KEY_MAP: dict[str, str] = {
    "chat": "Chat",
    "tasks": "Tasks",
    "agents": "Agents",
    "projects": "Projects",
    "docs": "Specs",
    "specs": "Specs",
    "transcripts": "Transcripts",
}


def _normalize_features(features: dict[str, bool]) -> dict[str, bool]:
    """Convert any old lowercase feature keys to canonical TitleCase labels.

    Also migrates the old "Docs" feature key to "Specs" so saved user
    preferences carry over after the rename.
    """
    normalized: dict[str, bool] = {}
    for key, value in features.items():
        canonical = _FEATURE_KEY_MAP.get(key, key)
        # Migrate old "Docs" TitleCase key to "Specs"
        if canonical == "Docs":
            canonical = "Specs"
        normalized[canonical] = value
    return normalized


# Keys whose default used to be False in older clients. On first load after
# upgrading to the "all features default on" rule, we flip any of these that
# are still False back to True. Once the migration runs we record a flag so
# future user-initiated toggles (e.g. turning Automations off in Settings)
# are preserved and never overridden.
_HISTORICAL_DEFAULT_FALSE_KEYS: set[str] = {"Automations"}


def _migrate_feature_defaults_all_true(data: dict) -> tuple[dict, bool]:
    """One-shot migration to the "all features default on" rule.

    For any feature key that historically defaulted to False (only
    ``Automations`` at the time of writing), promote a stored ``False`` to
    ``True`` the first time we load this settings file. Also backfill any
    feature key that is entirely missing with ``True`` so new system
    features appear enabled for existing users without a fresh install.

    We write a ``features_default_on_migrated: True`` flag so later user
    toggles are never overridden.
    """
    if data.get("features_default_on_migrated") is True:
        return data, False

    changed = False
    features = data.get("features")
    if isinstance(features, dict):
        for key in _HISTORICAL_DEFAULT_FALSE_KEYS:
            if features.get(key) is False:
                features[key] = True
                changed = True

        # Backfill any missing canonical feature key with True using the
        # schema defaults as the source of truth for the full set.
        from models.schemas import Settings as _Settings

        schema_features = _Settings().features
        for key, default_value in schema_features.items():
            if key not in features:
                features[key] = default_value if default_value is True else True
                changed = True
        data["features"] = features

    # Always mark the migration as run, even if no changes, so this is
    # truly a one-shot pass.
    data["features_default_on_migrated"] = True
    return data, True


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
        # One-shot migration: promote any historically default-False
        # feature (Automations, etc.) to True so every system feature
        # is on by default for existing users. Subsequent user toggles
        # are preserved via the ``features_default_on_migrated`` flag.
        data, features_migrated = _migrate_feature_defaults_all_true(data)
        if migrated or features_migrated:
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
