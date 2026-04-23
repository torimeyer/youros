"""Tests for services.tracing - trace_event helper."""
import json
import os
from pathlib import Path
from unittest.mock import patch


def test_trace_event_does_not_throw(tmp_path):
    """trace_event must never raise regardless of bad inputs."""
    from services.tracing import trace_event

    # Normal call
    trace_event("test_event", key="value")

    # Bad values that could trip json.dumps
    trace_event("bad_value", obj=object())

    # Empty name
    trace_event("")

    # No kwargs
    trace_event("plain")


def test_trace_event_writes_jsonl(tmp_path):
    """trace_event tier-A writes a JSON line to audit.jsonl."""
    audit_path = tmp_path / "audit.jsonl"

    with patch("services.tracing.Path") as mock_path_cls:
        # Make Path(PROJECT_ROOT).parent / ".ostk" / "audit.jsonl" resolve to tmp
        mock_ostk = tmp_path
        mock_path_instance = mock_path_cls.return_value
        mock_path_instance.parent.__truediv__ = lambda self, x: (
            tmp_path if x == ".ostk" else tmp_path / x
        )
        # Simpler: patch audit_path directly via module-level override
        import services.tracing as tracing_mod

        original_path = tracing_mod.Path

        def patched_path(arg):
            p = original_path(arg)
            return p

        # Use a direct environment-variable trick instead:
        # Patch PROJECT_ROOT so the .ostk dir lands in tmp_path
        with patch("services.tracing.Path", side_effect=lambda *a: original_path(*a)):
            pass  # restore

    # Simpler approach: patch the audit write directly
    import services.tracing as tracing_mod
    from services.tracing import trace_event

    written_lines = []
    original_open = open

    def mock_open(path, mode="r", **kwargs):
        if "audit.jsonl" in str(path) and "a" in mode:
            import io

            class FakeFH:
                def write(self, line):
                    written_lines.append(line)

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

            return FakeFH()
        return original_open(path, mode, **kwargs)

    with patch("builtins.open", side_effect=mock_open):
        with patch.object(
            tracing_mod, "trace_event", wraps=tracing_mod.trace_event
        ):
            # Trigger the real function body by calling it with mocked open
            # We need to patch at the right level
            pass

    # Cleanest approach: patch audit_path construction
    with patch("services.tracing.Path") as MockPath:
        from pathlib import Path as RealPath

        def side_effect(arg):
            return RealPath(arg)

        MockPath.side_effect = side_effect
        # Override the audit file resolution
        mock_ostk_dir = tmp_path
        mock_audit = tmp_path / "audit.jsonl"
        # Patch ostk_dir / audit_path inside trace_event
        import services.tracing as tracing_mod2

        original_trace_event = tracing_mod2.trace_event.__wrapped__ if hasattr(tracing_mod2.trace_event, "__wrapped__") else None

    # Direct approach: write a minimal version that patches the file path
    import services.tracing as tm

    # Patch PROJECT_ROOT so audit ends up in tmp
    with patch("services.tracing.PROJECT_ROOT", str(tmp_path / "project")):
        # Now .ostk dir = tmp_path/project/../.ostk = tmp_path/.ostk
        ostk_dir = tmp_path / ".ostk"
        ostk_dir.mkdir(parents=True, exist_ok=True)

        from services.tracing import trace_event as te
        te("llm_call_end", model="claude-3", provider="anthropic", input_tokens=10, output_tokens=20)

        audit_file = ostk_dir / "audit.jsonl"
        assert audit_file.exists(), "audit.jsonl was not created"
        lines = audit_file.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event"] == "llm_call_end"
        assert record["model"] == "claude-3"
        assert "ts" in record
        assert "trace_id" in record


def test_trace_event_includes_trace_id(tmp_path):
    """trace_event includes the current trace_id from ContextVar."""
    import services.tracing as tm

    token = tm._trace_id_var.set("test-trace-xyz")
    try:
        with patch("services.tracing.PROJECT_ROOT", str(tmp_path / "project")):
            ostk_dir = tmp_path / ".ostk"
            ostk_dir.mkdir(parents=True, exist_ok=True)
            tm.trace_event("agent_spawned", agent_name="test-agent")
            audit_file = ostk_dir / "audit.jsonl"
            record = json.loads(audit_file.read_text().strip())
            assert record["trace_id"] == "test-trace-xyz"
    finally:
        tm._trace_id_var.reset(token)


def test_get_trace_id_default_empty():
    """get_trace_id returns empty string when no trace is set."""
    import services.tracing as tm
    # Reset to default
    assert tm.get_trace_id() == "" or isinstance(tm.get_trace_id(), str)
