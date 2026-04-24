"""Backend: changing settings.files_dir reroutes get_files_dir()
and the module-level MYOS_FILES_DIR in caller modules that are not
patched by the autouse conftest fixture.
"""

from pathlib import Path


def test_default_files_dir_when_unset(tmp_path, monkeypatch):
    from services import files_dir as files_dir_mod
    from services.settings_store import settings_store

    monkeypatch.setattr(files_dir_mod, "DEFAULT_FILES_DIR", tmp_path / "d")
    cur = dict(settings_store.load())
    cur.pop("files_dir", None)
    settings_store.save(cur)

    assert files_dir_mod.get_files_dir() == tmp_path / "d"


def test_settings_files_dir_overrides_resolver(tmp_path):
    """Setting ``files_dir`` via settings_store changes what get_files_dir()
    returns at call-time, and modules that expose ``MYOS_FILES_DIR`` via
    PEP 562 ``__getattr__`` reflect it. (The autouse conftest fixture
    concretely pins ``routers.agents.MYOS_FILES_DIR`` to a tmp path for
    safety, so we check ``chat`` and ``automation_outputs`` here, both of
    which resolve dynamically.)
    """
    from services import files_dir as files_dir_mod
    from services.settings_store import settings_store

    target = tmp_path / "user_files"

    cur = dict(settings_store.load())
    original = cur.get("files_dir")
    cur["files_dir"] = str(target)
    settings_store.save(cur)

    try:
        assert files_dir_mod.get_files_dir() == target

        from routers import chat as chat_mod
        from services import automation_outputs as auto_mod

        assert chat_mod.MYOS_FILES_DIR == target
        assert auto_mod.MYOS_FILES_DIR == target
    finally:
        cur = dict(settings_store.load())
        if original is None:
            cur.pop("files_dir", None)
        else:
            cur["files_dir"] = original
        settings_store.save(cur)
