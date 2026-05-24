"""Regression tests that make sure user data never lives inside the repo.

If anyone adds a new store that writes files inside the repo directory, a
`git pull` could clobber that user's data on their next update. These tests
fail loudly when that happens.

Rule: every ``*_store.py`` module under ``api/services/`` must resolve its
storage path to something OUTSIDE the repo root (typically ``~/.myos/``).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# The repo root is three levels up from this file: api/tests/.. -> api -> repo
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _is_inside_repo(path: Path) -> bool:
    """True if ``path`` lives anywhere inside the repo."""
    try:
        path.resolve().relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


# Map of store module name -> the module-level path constant it exposes.
# Every new *_store.py added under api/services/ MUST be added here.
STORE_PATH_CONSTANTS = {
    "services.settings_store": "SETTINGS_PATH",
    "services.chat_history_store": "CHAT_HISTORY_PATH",
    "services.labels_store": "LABELS_PATH",
    "services.threads_store": "THREADS_PATH",
    "services.task_labels_store": "TASK_LABELS_PATH",
    "services.templates_store": "TEMPLATES_PATH",
    "services.task_order_store": "TASK_ORDER_PATH",
    "services.agent_templates_store": "AGENT_TEMPLATES_PATH",
    "services.recurring_tasks": "RECURRING_TASKS_PATH",
    "services.enterprise_store": "ENTERPRISE_PATH",
    "routers.indexing": "INDEX_PATH",
    "routers.knowledge": "KNOWLEDGE_PATH",
    "routers.growth": "GROWTH_PATH",
    "services.imessage": "IMESSAGE_CACHE_DIR",
    "services.dogwalk_store": "DOGWALK_LEADS_PATH",
    "services.prototypes_store": "PROTOTYPES_PATH",
    "services.task_source_store": "TASK_SOURCE_PATH",
    "services.gemini_captures_store": "GEMINI_CAPTURES_PATH",
    "services.gem_chat_history_store": "GEM_CHAT_HISTORY_DIR",
    "services.user_memory_store": "_MEMORY_PATH",
    "services.parked_tasks_store": "PARKED_TASKS_PATH",
    "services.turn_audit_store": "DATA_DIR",
}

_IN_MEMORY_STORES = frozenset({
    "services.plan_mode_store",
})


@pytest.mark.parametrize("module_name,const_name", list(STORE_PATH_CONSTANTS.items()))
def test_store_path_is_outside_repo(module_name: str, const_name: str):
    """Every store must write to a path outside the repo directory.

    If this test fails, your new store is at risk of being clobbered by
    ``git pull``. Fix it by moving the path resolver to ``~/.myos/`` (or
    another home-directory location).
    """
    module = importlib.import_module(module_name)
    assert hasattr(module, const_name), (
        f"{module_name} is missing the expected path constant {const_name}. "
        "Every store module must expose its storage path as a module-level "
        "constant so data safety can be checked."
    )
    path: Path = getattr(module, const_name)
    assert isinstance(path, Path), f"{module_name}.{const_name} must be a pathlib.Path"
    assert not _is_inside_repo(path), (
        f"{module_name}.{const_name} = {path} lives inside the repo at {REPO_ROOT}. "
        "User data inside the repo is at risk of being overwritten by git pull. "
        "Move this store to ~/.myos/ or another location outside the repo."
    )


def test_every_services_store_module_is_covered():
    """Fail if a new *_store.py is added to api/services/ but not listed above.

    This is the "trip wire" that forces anyone adding a new store to think
    about data safety. If they skip it, this test breaks.
    """
    services_dir = REPO_ROOT / "api" / "services"
    found_stores = sorted(
        f"services.{p.stem}"
        for p in services_dir.glob("*_store.py")
    )
    covered = sorted(STORE_PATH_CONSTANTS.keys())
    missing = set(found_stores) - set(covered) - _IN_MEMORY_STORES
    assert not missing, (
        f"New store modules are not covered by the data-safety check: {sorted(missing)}. "
        f"Add them to STORE_PATH_CONSTANTS in {__file__} with the name of the "
        "module-level path constant they expose."
    )


def test_task_suggestions_dismissed_path_is_outside_repo():
    """The smart-suggestions service writes a small "dismissed" file.

    It is not a *_store.py module so the parametrized test above does not
    cover it, but the data-safety guarantee still has to hold: a git pull
    must never wipe the user's dismissed list.
    """
    from services import task_suggestions

    path = task_suggestions.DISMISSED_PATH
    assert isinstance(path, Path)
    assert not _is_inside_repo(path), (
        f"task_suggestions.DISMISSED_PATH = {path} lives inside the repo. "
        "Move it under ~/.myos/ so user data is safe from git pull."
    )
    home = Path.home().resolve()
    expected_prefix = home / ".myos"
    try:
        path.resolve().relative_to(expected_prefix)
    except ValueError:
        pytest.fail(
            f"task_suggestions.DISMISSED_PATH = {path} is not under {expected_prefix}."
        )


def test_agent_durations_path_is_gitignored():
    """The agent duration history is a user-data file that the pattern
    analyzer reads. It lives under ``.ostk/`` today, which is gitignored,
    so a ``git pull`` cannot clobber it. If anyone moves it inside a
    tracked directory this test fails loudly.
    """
    import subprocess

    from services import agent_patterns

    path: Path = agent_patterns.AGENT_DURATIONS_PATH
    assert isinstance(path, Path)

    # Either the path is completely outside the repo, or it lives in a
    # gitignored location inside the repo. Both are safe from git pull.
    if not _is_inside_repo(path):
        return

    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        pytest.fail(f"agent_patterns.AGENT_DURATIONS_PATH = {path} could not be compared to the repo root")
    result = subprocess.run(
        ["git", "check-ignore", str(rel)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # git check-ignore exits 0 when the path IS ignored, 1 when not.
    assert result.returncode == 0, (
        f"agent_patterns.AGENT_DURATIONS_PATH = {path} lives inside the repo "
        f"and is NOT gitignored. Git pull could clobber learned agent durations. "
        f"Move it to ~/.myos/ or add its directory to .gitignore."
    )


def test_agent_patterns_proven_templates_path_is_outside_repo():
    """The proven templates file written by agent_patterns must live under
    ``~/.myos/`` so a ``git pull`` never clobbers it. The agent_patterns
    service writes to this path when promoting templates to proven status.
    """
    from services import agent_patterns

    path: Path = agent_patterns.PROVEN_TEMPLATES_PATH
    assert isinstance(path, Path)
    assert not _is_inside_repo(path), (
        f"agent_patterns.PROVEN_TEMPLATES_PATH = {path} lives inside the repo. "
        "Move it under ~/.myos/ so user data is safe from git pull."
    )
    home = Path.home().resolve()
    expected_prefix = home / ".myos"
    try:
        path.resolve().relative_to(expected_prefix)
    except ValueError:
        pytest.fail(
            f"agent_patterns.PROVEN_TEMPLATES_PATH = {path} is not under {expected_prefix}."
        )


def test_myos_home_dir_is_the_canonical_location():
    """Every tracked store should use ~/.myos/ as the home-directory prefix.

    This is a softer check. It enforces that stores all live under one
    predictable place the user can back up, inspect, and move around. If
    you intentionally pick a different home-directory location (e.g.
    ~/.local/share/myos/), update this test.
    """
    home = Path.home().resolve()
    expected_prefix = home / ".myos"
    for module_name, const_name in STORE_PATH_CONSTANTS.items():
        module = importlib.import_module(module_name)
        path: Path = getattr(module, const_name)
        resolved = path.resolve()
        try:
            resolved.relative_to(expected_prefix)
        except ValueError:
            pytest.fail(
                f"{module_name}.{const_name} = {resolved} is not under {expected_prefix}. "
                "All myOS stores should live under ~/.myos/ so users have one "
                "predictable place for their data."
            )
