"""Tests for the FCP export foundation (services/fcp_export.py).

Covers:
- detect_transport returns correct transport for each server
- get_server_command returns correct command list
- FCPExporter initialization
- Export method with mocked subprocess
- Actionable error messages on failure
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.fcp_export import (
    _SERVER_REGISTRY,
    detect_transport,
    get_server_command,
    FCPExporter,
    FCPExportError,
    EXPORTS_DIR,
)


# ---------------------------------------------------------------------------
# detect_transport
# ---------------------------------------------------------------------------

class TestDetectTransport:
    def test_fcp_sheets_returns_uvx(self):
        assert detect_transport("fcp-sheets") == "uvx"

    def test_fcp_slides_returns_uvx(self):
        assert detect_transport("fcp-slides") == "uvx"

    def test_ostk_fcp_drawio_returns_npx(self):
        assert detect_transport("@ostk-ai/fcp-drawio") == "npx"

    def test_ostk_fcp_pdf_returns_npx(self):
        assert detect_transport("@ostk-ai/fcp-pdf") == "npx"

    def test_ostk_fcp_slides_returns_npx(self):
        assert detect_transport("@ostk-ai/fcp-slides") == "npx"

    def test_unknown_at_package_returns_npx(self):
        """Names starting with @ are assumed to be npm packages."""
        assert detect_transport("@my-org/some-server") == "npx"

    def test_unknown_plain_name_returns_uvx(self):
        """Unknown plain names default to Python/uvx."""
        assert detect_transport("my-custom-server") == "uvx"


# ---------------------------------------------------------------------------
# get_server_command
# ---------------------------------------------------------------------------

class TestGetServerCommand:
    def test_fcp_sheets_command(self):
        cmd = get_server_command("fcp-sheets")
        assert cmd == ["uvx", "fcp-sheets"]

    def test_fcp_slides_command(self):
        cmd = get_server_command("fcp-slides")
        assert cmd == ["uvx", "fcp-slides"]

    def test_ostk_fcp_drawio_command(self):
        cmd = get_server_command("@ostk-ai/fcp-drawio")
        assert cmd == ["npx", "-y", "@ostk-ai/fcp-drawio"]

    def test_ostk_fcp_slides_command(self):
        cmd = get_server_command("@ostk-ai/fcp-slides")
        assert cmd == ["npx", "-y", "@ostk-ai/fcp-slides"]

    def test_unknown_npx_server_command(self):
        """Unknown npm-scoped server gets npx -y prefix."""
        cmd = get_server_command("@org/server")
        assert cmd == ["npx", "-y", "@org/server"]

    def test_unknown_uvx_server_command(self):
        """Unknown plain server gets uvx prefix."""
        cmd = get_server_command("custom-server")
        assert cmd == ["uvx", "custom-server"]

    def test_returns_new_list(self):
        """get_server_command returns a copy, not the registry original."""
        cmd1 = get_server_command("fcp-sheets")
        cmd2 = get_server_command("fcp-sheets")
        assert cmd1 is not cmd2


# ---------------------------------------------------------------------------
# FCPExporter initialization
# ---------------------------------------------------------------------------

class TestFCPExporterInit:
    def test_default_timeout(self):
        exporter = FCPExporter("fcp-sheets")
        assert exporter.server_name == "fcp-sheets"
        assert exporter.timeout == 30

    def test_custom_timeout(self):
        exporter = FCPExporter("fcp-slides", timeout=60)
        assert exporter.timeout == 60

    def test_request_id_starts_at_zero(self):
        exporter = FCPExporter("fcp-slides")
        assert exporter._request_id == 0

    def test_next_id_increments(self):
        exporter = FCPExporter("fcp-slides")
        assert exporter._next_id() == 1
        assert exporter._next_id() == 2
        assert exporter._next_id() == 3


# ---------------------------------------------------------------------------
# FCPExporter.export with mocked subprocess
# ---------------------------------------------------------------------------

class TestFCPExporterExport:
    @pytest.mark.asyncio
    async def test_export_success(self, tmp_path):
        """export() returns {path, filename, size_bytes} on success."""
        output_file = tmp_path / "test-output.pptx"

        def mock_jsonrpc_response(*args, **kwargs):
            """Simulate readline returning valid JSON-RPC responses."""
            return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n"

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.stdout.readline = MagicMock(side_effect=mock_jsonrpc_response)

        # Write the file so export sees it
        output_file.write_bytes(b"PK\x03\x04fake-pptx-data-here")

        with patch("services.fcp_export.subprocess.Popen", return_value=mock_proc):
            exporter = FCPExporter("fcp-slides")
            result = await exporter.export(
                mutations=[{"verb": "add_slide", "args": {"layout": "title"}}],
                output_path=output_file,
            )

        assert result["path"] == str(output_file)
        assert result["filename"] == "test-output.pptx"
        assert result["size_bytes"] > 0

    @pytest.mark.asyncio
    async def test_export_file_not_found_error(self, tmp_path):
        """FileNotFoundError gives an actionable error about installing the tool."""
        output_file = tmp_path / "missing.pptx"

        with patch("services.fcp_export.subprocess.Popen", side_effect=FileNotFoundError):
            exporter = FCPExporter("fcp-slides")
            with pytest.raises(FCPExportError) as exc_info:
                await exporter.export(
                    mutations=[],
                    output_path=output_file,
                )

        msg = str(exc_info.value)
        assert "fcp-slides" in msg
        # Must include how to fix
        assert "install" in msg.lower()

    @pytest.mark.asyncio
    async def test_export_npx_file_not_found_error(self, tmp_path):
        """FileNotFoundError for npx server mentions Node.js."""
        output_file = tmp_path / "missing.pptx"

        with patch("services.fcp_export.subprocess.Popen", side_effect=FileNotFoundError):
            exporter = FCPExporter("@ostk-ai/fcp-slides")
            with pytest.raises(FCPExportError) as exc_info:
                await exporter.export(
                    mutations=[],
                    output_path=output_file,
                )

        msg = str(exc_info.value)
        assert "Node" in msg or "npx" in msg

    @pytest.mark.asyncio
    async def test_export_server_error_response(self, tmp_path):
        """Server returning error in JSON-RPC gives actionable message."""
        output_file = tmp_path / "error.pptx"

        error_response = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"message": "Unknown verb: bad_verb"},
        }) + "\n"

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        # First call (initialize) succeeds, second (session_start) returns error
        init_ok = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n"
        mock_proc.stdout.readline = MagicMock(
            side_effect=[init_ok, init_ok, error_response]
        )

        with patch("services.fcp_export.subprocess.Popen", return_value=mock_proc):
            exporter = FCPExporter("fcp-slides")
            with pytest.raises(FCPExportError) as exc_info:
                await exporter.export(
                    mutations=[{"verb": "bad_verb", "args": {}}],
                    output_path=output_file,
                )

        msg = str(exc_info.value)
        assert "Unknown verb" in msg or "error" in msg.lower()


# ---------------------------------------------------------------------------
# Server registry completeness
# ---------------------------------------------------------------------------

class TestServerRegistry:
    def test_registry_has_fcp_sheets(self):
        assert "fcp-sheets" in _SERVER_REGISTRY

    def test_registry_has_fcp_slides(self):
        assert "fcp-slides" in _SERVER_REGISTRY

    def test_registry_has_ostk_fcp_drawio(self):
        assert "@ostk-ai/fcp-drawio" in _SERVER_REGISTRY

    def test_registry_has_ostk_fcp_pdf(self):
        assert "@ostk-ai/fcp-pdf" in _SERVER_REGISTRY

    def test_registry_has_ostk_fcp_slides(self):
        assert "@ostk-ai/fcp-slides" in _SERVER_REGISTRY

    def test_fcp_slides_uvx_transport(self):
        entry = _SERVER_REGISTRY["fcp-slides"]
        assert entry["transport"] == "uvx"
        assert entry["cmd"] == ["uvx", "fcp-slides"]
