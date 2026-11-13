"""Tests for blue-green zero-downtime backend reload (task ->3110).

Covers:
1. reload_agent_state_from_disk() merging disk heartbeats/registrations without
   clobbering live in-memory active_agents process handles.
2. POST /api/internal/reload-state strict loopback enforcement (403 on remote
   clients or spoofed X-Forwarded-For, 200 on loopback).
3. Continuous HTTP bombardment through a dynamic active_backend_port proxy
   during scripts/reload-backend-zero-downtime.sh proving 0 non-200 responses
   across a full primary restart + state sync across workers.
4. Abort-safety when standby fails healthcheck (primary untouched, 0 downtime).
5. Cold-start fallback when primary is initially down.
6. scripts/restart-backend.sh delegation to zero-downtime reload vs --hard.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from main import app
from routers import agents as agents_router


REPO_ROOT = Path(__file__).resolve().parents[2]
RELOAD_SCRIPT = REPO_ROOT / "scripts" / "reload-backend-zero-downtime.sh"
RESTART_SCRIPT = REPO_ROOT / "scripts" / "restart-backend.sh"


def _pick_free_port(preferred: int | None = None) -> int:
    """Bind port on 127.0.0.1 briefly to reserve a free ephemeral port."""
    if preferred is not None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _write_worker_server_script(script_path: Path) -> None:
    """Write a standalone fast HTTP server used for hermetic reload tests."""
    script_path.write_text(
        textwrap.dedent(
            """\
            import json
            import os
            import signal
            import sys
            import time
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            from pathlib import Path

            ROLE = sys.argv[1]
            PORT = int(sys.argv[2])
            STATE_FILE = Path(sys.argv[3])
            BOOT_DELAY = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
            FAIL_HEALTH = (sys.argv[5] == "fail") if len(sys.argv) > 5 else False

            time.sleep(BOOT_DELAY)

            def load_disk():
                if STATE_FILE.exists():
                    try:
                        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
                    except Exception:
                        return {}
                return {}

            IN_MEMORY = {
                "items": dict(load_disk().get("items", {})),
                "reloads": 0,
            }

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, fmt, *args):
                    return

                def _send_json(self, code, payload):
                    raw = json.dumps(payload).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)

                def do_GET(self):
                    if self.path == "/api/health":
                        if FAIL_HEALTH:
                            self._send_json(503, {"status": "broken", "role": ROLE})
                        else:
                            self._send_json(
                                200,
                                {
                                    "status": "ok",
                                    "role": ROLE,
                                    "pid": os.getpid(),
                                    "reloads": IN_MEMORY["reloads"],
                                    "items": IN_MEMORY["items"],
                                },
                            )
                        return
                    self._send_json(404, {"detail": "not found"})

                def do_POST(self):
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    body_bytes = self.rfile.read(length) if length > 0 else b"{}"
                    if self.path == "/api/internal/reload-state":
                        disk = load_disk()
                        IN_MEMORY["items"].update(disk.get("items", {}))
                        IN_MEMORY["reloads"] += 1
                        marker_log = STATE_FILE.parent / "reload_calls.log"
                        with marker_log.open("a", encoding="utf-8") as fh:
                            fh.write(f"{ROLE}:{PORT}\\n")
                        self._send_json(
                            200,
                            {
                                "status": "ok",
                                "agents_loaded": len(IN_MEMORY["items"]),
                                "role": ROLE,
                            },
                        )
                        return
                    if self.path == "/api/write-item":
                        data = json.loads(body_bytes.decode("utf-8") or "{}")
                        k = data["key"]
                        v = data["val"]
                        IN_MEMORY["items"][k] = v
                        disk = load_disk()
                        items = dict(disk.get("items", {}))
                        items[k] = v
                        tmp = STATE_FILE.with_suffix(".tmp")
                        tmp.write_text(json.dumps({"items": items}), encoding="utf-8")
                        tmp.replace(STATE_FILE)
                        self._send_json(200, {"ok": True, "role": ROLE})
                        return
                    self._send_json(404, {"detail": "not found"})

            httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
            signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
            httpd.serve_forever()
            """
        ),
        encoding="utf-8",
    )


class _DynamicPortProxy:
    """Hermetic HTTP reverse proxy reading active_backend_port with short TTL."""

    def __init__(self, active_port_file: Path, default_port: int, ttl_s: float = 0.05):
        self.active_port_file = active_port_file
        self.default_port = default_port
        self.ttl_s = ttl_s
        self._cached_port = default_port
        self._cached_at = 0.0
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    def resolve_port(self) -> int:
        now = time.monotonic()
        with self._lock:
            if now - self._cached_at < self.ttl_s:
                return self._cached_port
            port = self.default_port
            try:
                raw = self.active_port_file.read_text(encoding="utf-8").strip()
                if raw.isdigit():
                    parsed = int(raw)
                    if 1024 <= parsed <= 65535:
                        port = parsed
            except OSError:
                pass
            self._cached_port = port
            self._cached_at = now
            return port

    def start(self) -> int:
        outer = self

        class ProxyHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def _proxy(self):
                target_port = outer.resolve_port()
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(length) if length > 0 else None
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", target_port, timeout=2.0)
                    conn.request(
                        self.command,
                        self.path,
                        body=body,
                        headers={"Content-Type": self.headers.get("Content-Type", "application/json")},
                    )
                    resp = conn.getresponse()
                    resp_body = resp.read()
                    conn.close()
                    self.send_response(resp.status)
                    self.send_header("Content-Type", resp.getheader("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(resp_body)))
                    self.end_headers()
                    self.wfile.write(resp_body)
                except Exception as exc:
                    err_bytes = json.dumps({"proxy_error": str(exc), "target_port": target_port}).encode("utf-8")
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err_bytes)))
                    self.end_headers()
                    self.wfile.write(err_bytes)

            def do_GET(self):
                self._proxy()

            def do_POST(self):
                self._proxy()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def _wait_http_200(port: int, timeout_s: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1.0)
            conn.request("GET", "/api/health")
            resp = conn.getresponse()
            raw = resp.read()
            conn.close()
            if resp.status == 200:
                return json.loads(raw.decode("utf-8"))
        except Exception:
            pass
        time.sleep(0.05)
    raise AssertionError(f"Server on port {port} did not reach HTTP 200 within {timeout_s}s")


class _DummyProc:
    def __init__(self, returncode=None):
        self.returncode = returncode


def test_reload_agent_state_from_disk_merges_without_clobbering_active_procs(tmp_path):
    state_file = tmp_path / "agent_state.json"
    deleted_file = tmp_path / "deleted_agents.json"
    deleted_file.write_text(json.dumps(["deleted-ghost"]), encoding="utf-8")

    state_file.write_text(
        json.dumps(
            {
                "agent-existing": {
                    "status": "running",
                    "current_step": "updated-on-outgoing-worker",
                    "last_heartbeat_at": "2026-11-13T22:40:00+00:00",
                },
                "agent-new-cutover": {
                    "status": "running",
                    "task": "registered during boot window",
                    "last_heartbeat_at": "2026-11-13T22:40:05+00:00",
                },
                "agent-live-proc": {
                    "status": "completed",
                    "pid": 99999,
                    "current_step": "disk-step",
                    "last_heartbeat_at": "2026-11-13T22:40:02+00:00",
                },
                "deleted-ghost": {
                    "status": "running",
                },
            }
        ),
        encoding="utf-8",
    )

    live_proc = _DummyProc(returncode=None)
    orig_meta = dict(agents_router.agent_metadata)
    orig_active = dict(agents_router.active_agents)
    try:
        agents_router.agent_metadata.clear()
        agents_router.active_agents.clear()
        agents_router.agent_metadata["agent-existing"] = {
            "status": "running",
            "current_step": "old-step",
            "last_heartbeat_at": "2026-11-13T22:35:00+00:00",
        }
        agents_router.agent_metadata["agent-live-proc"] = {
            "status": "running",
            "pid": 42424,
            "current_step": "initial-step",
            "last_heartbeat_at": "2026-11-13T22:35:00+00:00",
        }
        agents_router.active_agents["agent-live-proc"] = live_proc

        with (
            patch.object(agents_router, "AGENT_STATE_PATH", state_file),
            patch.object(agents_router, "DELETED_AGENTS_PATH", deleted_file),
        ):
            loaded = agents_router.reload_agent_state_from_disk()

        assert loaded == 3
        assert agents_router.agent_metadata["agent-existing"]["current_step"] == "updated-on-outgoing-worker"
        assert "agent-new-cutover" in agents_router.agent_metadata
        assert "deleted-ghost" not in agents_router.agent_metadata
        assert agents_router.active_agents["agent-live-proc"] is live_proc
        assert agents_router.agent_metadata["agent-live-proc"]["pid"] == 42424
        assert agents_router.agent_metadata["agent-live-proc"]["status"] == "running"
    finally:
        agents_router.agent_metadata.clear()
        agents_router.agent_metadata.update(orig_meta)
        agents_router.active_agents.clear()
        agents_router.active_agents.update(orig_active)


@pytest.mark.asyncio
async def test_internal_reload_state_endpoint_enforces_loopback_and_reloads(tmp_path, monkeypatch):
    state_file = tmp_path / "agent_state.json"
    deleted_file = tmp_path / "deleted_agents.json"
    deleted_file.write_text("[]", encoding="utf-8")
    state_file.write_text(
        json.dumps(
            {
                "cutover-agent-1": {
                    "status": "running",
                    "current_step": "synced-via-endpoint",
                    "last_heartbeat_at": "2026-11-13T22:41:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )

    orig_meta = dict(agents_router.agent_metadata)
    try:
        agents_router.agent_metadata.clear()
        monkeypatch.setattr(agents_router, "AGENT_STATE_PATH", state_file)
        monkeypatch.setattr(agents_router, "DELETED_AGENTS_PATH", deleted_file)

        loopback_transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 54321))
        async with httpx.AsyncClient(transport=loopback_transport, base_url="http://127.0.0.1:8000") as c:
            res = await c.post("/api/internal/reload-state")
            assert res.status_code == 200
            assert res.json() == {"status": "ok", "agents_loaded": 1}
            assert agents_router.agent_metadata["cutover-agent-1"]["current_step"] == "synced-via-endpoint"

            spoofed = await c.post(
                "/api/internal/reload-state",
                headers={"X-Forwarded-For": "203.0.113.99"},
            )
            assert spoofed.status_code == 403

        remote_transport = httpx.ASGITransport(app=app, client=("198.51.100.77", 54321))
        async with httpx.AsyncClient(transport=remote_transport, base_url="http://127.0.0.1:8000") as rc:
            monkeypatch.delenv("ALLOW_NON_LOOPBACK", raising=False)
            r_blocked = await rc.post("/api/internal/reload-state")
            assert r_blocked.status_code == 403

            monkeypatch.setenv("ALLOW_NON_LOOPBACK", "1")
            r_still_blocked = await rc.post("/api/internal/reload-state")
            assert r_still_blocked.status_code == 403
    finally:
        agents_router.agent_metadata.clear()
        agents_router.agent_metadata.update(orig_meta)


def test_zero_downtime_reload_under_continuous_http_bombardment(tmp_path):
    assert RELOAD_SCRIPT.is_file(), f"Missing {RELOAD_SCRIPT}"
    assert os.access(RELOAD_SCRIPT, os.X_OK), f"{RELOAD_SCRIPT} must be executable"

    youros_home = tmp_path / "youros_home"
    youros_home.mkdir()
    state_file = youros_home / "shared_state.json"
    state_file.write_text(json.dumps({"items": {"boot": "v1"}}), encoding="utf-8")
    active_port_file = youros_home / "active_backend_port"
    lock_file = tmp_path / "reload.lock"
    worker_script = tmp_path / "worker_server.py"
    _write_worker_server_script(worker_script)

    primary_port = _pick_free_port(18911)
    standby_port = _pick_free_port(18912)
    while standby_port == primary_port:
        standby_port = _pick_free_port()

    active_port_file.write_text(f"{primary_port}\n", encoding="utf-8")

    primary_v1 = subprocess.Popen(
        [sys.executable, str(worker_script), "primary-v1", str(primary_port), str(state_file), "0"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    primary_v2_pid_file = tmp_path / "primary_v2.pid"
    proxy = _DynamicPortProxy(active_port_file, default_port=primary_port, ttl_s=0.05)

    stop_bombard = threading.Event()
    roles_seen: list[str] = []
    failures: list[str] = []
    write_counter = 0

    try:
        _wait_http_200(primary_port)
        proxy_port = proxy.start()

        def bombard_worker():
            nonlocal write_counter
            idx = 0
            while not stop_bombard.is_set():
                idx += 1
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=2.0)
                    if idx % 5 == 0:
                        write_counter += 1
                        payload = json.dumps({"key": f"req-{write_counter}", "val": "live"}).encode("utf-8")
                        conn.request(
                            "POST",
                            "/api/write-item",
                            body=payload,
                            headers={"Content-Type": "application/json"},
                        )
                    else:
                        conn.request("GET", "/api/health")
                    resp = conn.getresponse()
                    raw = resp.read().decode("utf-8", errors="replace")
                    conn.close()
                    if resp.status != 200:
                        failures.append(f"HTTP {resp.status}: {raw}")
                    else:
                        parsed = json.loads(raw)
                        if "role" in parsed:
                            roles_seen.append(parsed["role"])
                except Exception as exc:
                    failures.append(f"Exception: {exc}")
                time.sleep(0.015)

        bombard_thread = threading.Thread(target=bombard_worker, daemon=True)
        bombard_thread.start()
        time.sleep(0.12)

        restart_helper = tmp_path / "restart_primary.sh"
        restart_helper.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                set -e
                kill -9 {primary_v1.pid} 2>/dev/null || true
                sleep 0.45
                "{sys.executable}" "{worker_script}" primary-v2 "{primary_port}" "{state_file}" 0.1 > /dev/null 2>&1 < /dev/null &
                echo $! > "{primary_v2_pid_file}"
                """
            ),
            encoding="utf-8",
        )
        restart_helper.chmod(0o755)

        env = os.environ.copy()
        env.update(
            {
                "YOUROS_HOME": str(youros_home),
                "PRIMARY_PORT": str(primary_port),
                "STANDBY_PORT": str(standby_port),
                "ACTIVE_PORT_FILE": str(active_port_file),
                "RELOAD_LOCK_FILE": str(lock_file),
                "SCHEME": "http",
                "READINESS_TIMEOUT": "10",
                "DRAIN_SECONDS": "0.35",
                "STANDBY_START_CMD": f'"{sys.executable}" "{worker_script}" standby "{standby_port}" "{state_file}" 0.2',
                "PRIMARY_RESTART_CMD": f'bash "{restart_helper}"',
            }
        )

        proc = subprocess.run(
            ["bash", str(RELOAD_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )
        time.sleep(0.15)
        stop_bombard.set()
        bombard_thread.join(timeout=3.0)

        assert proc.returncode == 0, f"Reload failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        assert failures == [], f"Expected 0 failed requests during blue-green reload, got: {failures[:10]}"
        assert len(roles_seen) >= 40
        assert "primary-v1" in roles_seen
        assert "standby" in roles_seen, f"Traffic never routed through standby: {set(roles_seen)}"
        assert "primary-v2" in roles_seen, f"Traffic never cut back to primary-v2: {set(roles_seen)}"
        assert active_port_file.read_text(encoding="utf-8").strip() == str(primary_port)

        # Verify state written while on standby was hydrated into primary-v2 via /api/internal/reload-state
        final_health = _wait_http_200(primary_port)
        assert final_health["role"] == "primary-v2"
        assert final_health["reloads"] >= 1
        for i in range(1, write_counter + 1):
            assert final_health["items"].get(f"req-{i}") == "live"

        reload_log = (youros_home / "reload_calls.log").read_text(encoding="utf-8").splitlines()
        assert f"standby:{standby_port}" in reload_log
        assert f"primary-v2:{primary_port}" in reload_log

        # Standby port must be torn down cleanly after drain
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            assert s.connect_ex(("127.0.0.1", standby_port)) != 0
    finally:
        stop_bombard.set()
        proxy.close()
        primary_v1.kill()
        if primary_v2_pid_file.exists():
            try:
                os.kill(int(primary_v2_pid_file.read_text().strip()), signal.SIGKILL)
            except Exception:
                pass


def test_zero_downtime_reload_aborts_safely_when_standby_fails_healthcheck(tmp_path):
    assert RELOAD_SCRIPT.is_file(), f"Missing {RELOAD_SCRIPT}"
    youros_home = tmp_path / "youros_home"
    youros_home.mkdir()
    state_file = youros_home / "shared_state.json"
    state_file.write_text("{}", encoding="utf-8")
    active_port_file = youros_home / "active_backend_port"
    lock_file = tmp_path / "reload.lock"
    worker_script = tmp_path / "worker_server.py"
    _write_worker_server_script(worker_script)

    primary_port = _pick_free_port(18921)
    standby_port = _pick_free_port(18922)
    while standby_port == primary_port:
        standby_port = _pick_free_port()

    active_port_file.write_text(f"{primary_port}\n", encoding="utf-8")

    primary_v1 = subprocess.Popen(
        [sys.executable, str(worker_script), "primary-v1", str(primary_port), str(state_file), "0"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proxy = _DynamicPortProxy(active_port_file, default_port=primary_port, ttl_s=0.05)
    stop_bombard = threading.Event()
    failures: list[str] = []

    try:
        _wait_http_200(primary_port)
        proxy_port = proxy.start()

        def bombard_worker():
            while not stop_bombard.is_set():
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=1.5)
                    conn.request("GET", "/api/health")
                    resp = conn.getresponse()
                    resp.read()
                    conn.close()
                    if resp.status != 200:
                        failures.append(f"HTTP {resp.status}")
                except Exception as exc:
                    failures.append(str(exc))
                time.sleep(0.02)

        t = threading.Thread(target=bombard_worker, daemon=True)
        t.start()

        primary_touched_marker = tmp_path / "primary_was_touched"
        env = os.environ.copy()
        env.update(
            {
                "YOUROS_HOME": str(youros_home),
                "PRIMARY_PORT": str(primary_port),
                "STANDBY_PORT": str(standby_port),
                "ACTIVE_PORT_FILE": str(active_port_file),
                "RELOAD_LOCK_FILE": str(lock_file),
                "SCHEME": "http",
                "READINESS_TIMEOUT": "1.2",
                "STANDBY_START_CMD": f'"{sys.executable}" "{worker_script}" broken-standby "{standby_port}" "{state_file}" 0 fail',
                "PRIMARY_RESTART_CMD": f'touch "{primary_touched_marker}"',
            }
        )

        proc = subprocess.run(
            ["bash", str(RELOAD_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        stop_bombard.set()
        t.join(timeout=2.0)

        assert proc.returncode == 1, f"Expected reload script to exit 1 when standby fails healthcheck, got {proc.returncode}"
        assert not primary_touched_marker.exists(), "Primary must NEVER be restarted when standby fails"
        assert primary_v1.poll() is None, "Primary v1 process must remain alive"
        assert failures == [], f"Expected 0 failed requests on broken standby abort, got: {failures}"
        assert active_port_file.read_text(encoding="utf-8").strip() == str(primary_port)
    finally:
        stop_bombard.set()
        proxy.close()
        primary_v1.kill()


def test_zero_downtime_reload_cold_starts_primary_directly_when_primary_is_down(tmp_path):
    youros_home = tmp_path / "youros_home"
    youros_home.mkdir()
    state_file = youros_home / "shared_state.json"
    state_file.write_text("{}", encoding="utf-8")
    active_port_file = youros_home / "active_backend_port"
    lock_file = tmp_path / "reload.lock"
    worker_script = tmp_path / "worker_server.py"
    _write_worker_server_script(worker_script)

    primary_port = _pick_free_port(18931)
    standby_port = _pick_free_port(18932)
    while standby_port == primary_port:
        standby_port = _pick_free_port()

    primary_pid_file = tmp_path / "cold_primary.pid"
    standby_marker = tmp_path / "standby_should_not_run"

    start_helper = tmp_path / "cold_start.sh"
    start_helper.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -e
            "{sys.executable}" "{worker_script}" cold-primary "{primary_port}" "{state_file}" 0.1 > /dev/null 2>&1 < /dev/null &
            echo $! > "{primary_pid_file}"
            """
        ),
        encoding="utf-8",
    )
    start_helper.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "YOUROS_HOME": str(youros_home),
            "PRIMARY_PORT": str(primary_port),
            "STANDBY_PORT": str(standby_port),
            "ACTIVE_PORT_FILE": str(active_port_file),
            "RELOAD_LOCK_FILE": str(lock_file),
            "SCHEME": "http",
            "READINESS_TIMEOUT": "8",
            "STANDBY_START_CMD": f'touch "{standby_marker}"',
            "PRIMARY_RESTART_CMD": f'bash "{start_helper}"',
        }
    )

    try:
        proc = subprocess.run(
            ["bash", str(RELOAD_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc.returncode == 0, f"Cold start failed:\n{proc.stdout}\n{proc.stderr}"
        assert not standby_marker.exists(), "Cold start should skip standby dance when primary is down"
        assert active_port_file.read_text(encoding="utf-8").strip() == str(primary_port)
        health = _wait_http_200(primary_port)
        assert health["role"] == "cold-primary"
    finally:
        if primary_pid_file.exists():
            try:
                os.kill(int(primary_pid_file.read_text().strip()), signal.SIGKILL)
            except Exception:
                pass


def test_restart_backend_delegates_to_zero_downtime_unless_hard_passed(tmp_path):
    fake_scripts = tmp_path / "scripts"
    fake_scripts.mkdir()
    (fake_scripts / "restart-backend.sh").write_text(
        RESTART_SCRIPT.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (fake_scripts / "restart-backend.sh").chmod(0o755)

    zd_marker = tmp_path / "zd_called"
    hard_marker = tmp_path / "dev_backend_called"

    (fake_scripts / "reload-backend-zero-downtime.sh").write_text(
        f"#!/usr/bin/env bash\ntouch '{zd_marker}'\nexit 0\n",
        encoding="utf-8",
    )
    (fake_scripts / "reload-backend-zero-downtime.sh").chmod(0o755)

    (fake_scripts / "dev-backend.sh").write_text(
        f"#!/usr/bin/env bash\ntouch '{hard_marker}'\nexit 0\n",
        encoding="utf-8",
    )
    (fake_scripts / "dev-backend.sh").chmod(0o755)

    port = _pick_free_port(18941)
    listener = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.35)
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        (fake_bin / "pkill").write_text(
            "#!/usr/bin/env bash\necho 'UNSCOPED_PKILL_FORBIDDEN' >&2\nexit 99\n",
            encoding="utf-8",
        )
        (fake_bin / "pkill").chmod(0o755)

        env = os.environ.copy()
        env["PORT"] = str(port)
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

        # Default invocation while port is listening -> delegates to zero-downtime reload
        r1 = subprocess.run(
            ["bash", str(fake_scripts / "restart-backend.sh")],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert r1.returncode == 0
        assert zd_marker.exists()
        assert not hard_marker.exists()

        zd_marker.unlink()

        # --hard invocation -> kills listener on port and execs dev-backend.sh
        r2 = subprocess.run(
            ["bash", str(fake_scripts / "restart-backend.sh"), "--hard"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert r2.returncode == 0
        assert not zd_marker.exists()
        assert hard_marker.exists()
    finally:
        listener.kill()
