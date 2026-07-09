"""Tests for scripts/youros-doctor.sh (→2572)

Covers:
- all-green path (backend mock + temp fixtures)
- red backend path (closed port)
- unparseable settings.json
"""
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "youros-doctor.sh"


def run_doctor(**env_overrides):
    env = {**os.environ, **env_overrides}
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )


class _MockBackendHandler(BaseHTTPRequestHandler):
    """Returns 200 for /api/status; connected:false for all OAuth endpoints."""

    def do_GET(self):
        if self.path == "/api/status":
            body = b'{"ok":true}'
        else:
            body = b'{"connected":false}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _make_mock_server():
    server = HTTPServer(("127.0.0.1", 0), _MockBackendHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port


def _make_fixtures(tmp_path: Path, valid_json: bool = True) -> tuple[Path, Path]:
    """Return (youros_dir, sock_path) with the minimum structure the doctor expects."""
    youros_dir = tmp_path / "youros"
    youros_dir.mkdir()
    (youros_dir / "specs").mkdir()
    (youros_dir / "drafts").mkdir()
    settings = '{"theme": "dark"}' if valid_json else "not valid json {{{{"
    (youros_dir / "settings.json").write_text(settings)
    sock_path = tmp_path / "ostk.sock"
    sock_path.touch()
    return youros_dir, sock_path


def test_all_green(tmp_path):
    server, port = _make_mock_server()
    try:
        youros_dir, sock_path = _make_fixtures(tmp_path)
        result = run_doctor(
            DOCTOR_BACKEND_URL=f"http://127.0.0.1:{port}",
            DOCTOR_FRONTEND_URL=f"http://127.0.0.1:{port}",
            DOCTOR_YOUROS_DIR=str(youros_dir),
            DOCTOR_SOCK_PATH=str(sock_path),
        )
    finally:
        server.shutdown()

    assert result.returncode == 0, (
        f"Expected exit 0 (all green), got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "All checks passed" in result.stdout


def test_backend_down_shows_red_with_fix(tmp_path):
    youros_dir, sock_path = _make_fixtures(tmp_path)
    result = run_doctor(
        DOCTOR_BACKEND_URL="http://127.0.0.1:19743",  # closed port
        DOCTOR_YOUROS_DIR=str(youros_dir),
        DOCTOR_SOCK_PATH=str(sock_path),
    )

    assert result.returncode == 1, "Expected exit 1 when backend is down"
    assert "Backend not reachable" in result.stdout
    assert "launchctl kickstart" in result.stdout


def test_invalid_settings_json_shows_red(tmp_path):
    server, port = _make_mock_server()
    try:
        youros_dir, sock_path = _make_fixtures(tmp_path, valid_json=False)
        result = run_doctor(
            DOCTOR_BACKEND_URL=f"http://127.0.0.1:{port}",
            DOCTOR_FRONTEND_URL=f"http://127.0.0.1:{port}",
            DOCTOR_YOUROS_DIR=str(youros_dir),
            DOCTOR_SOCK_PATH=str(sock_path),
        )
    finally:
        server.shutdown()

    assert result.returncode == 1
    assert "not valid JSON" in result.stdout
