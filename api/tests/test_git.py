"""Tests for GET /api/git/recent."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def _make_run(returncode: int, stdout: str = "", stderr: str = ""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


_ROOT_OK = _make_run(0, "/repo\n")
_LOG_LINES = (
    "abc1234|Add feature|Alice <a@b.com>|2026-05-19T10:00:00+00:00\n"
    "def5678|Fix bug|Bob|2026-05-18T09:00:00+00:00\n"
)


def _mock_run(cmd, **kwargs):
    if "rev-parse" in cmd:
        return _ROOT_OK
    return _make_run(0, _LOG_LINES)


class TestGitRecent:
    def test_happy_path_returns_n_commits(self):
        with patch("routers.git.subprocess.run", side_effect=_mock_run):
            r = client.get("/api/git/recent?limit=2")
        assert r.status_code == 200
        data = r.json()
        assert len(data["commits"]) == 2
        c = data["commits"][0]
        assert c["hash"] == "abc1234"
        assert c["subject"] == "Add feature"
        assert c["author"] == "Alice <a@b.com>"
        assert c["timestamp"] == "2026-05-19T10:00:00+00:00"

    def test_all_four_fields_present(self):
        with patch("routers.git.subprocess.run", side_effect=_mock_run):
            r = client.get("/api/git/recent")
        assert r.status_code == 200
        for c in r.json()["commits"]:
            assert set(c.keys()) == {"hash", "subject", "author", "timestamp"}

    def test_limit_zero_returns_empty(self):
        r = client.get("/api/git/recent?limit=0")
        assert r.status_code == 200
        assert r.json() == {"commits": []}

    def test_limit_above_max_rejected(self):
        r = client.get("/api/git/recent?limit=100")
        assert r.status_code == 422

    def test_limit_at_max_accepted(self):
        with patch("routers.git.subprocess.run", side_effect=_mock_run):
            r = client.get("/api/git/recent?limit=50")
        assert r.status_code == 200

    def test_not_a_git_repo_returns_500(self):
        def bad_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return _make_run(1, stderr="not a git repo")
            return _make_run(0, "")

        with patch("routers.git.subprocess.run", side_effect=bad_run):
            r = client.get("/api/git/recent")
        assert r.status_code == 500
        assert "git repository" in r.json()["detail"]

    def test_control_chars_in_subject_sanitized(self):
        evil_line = "abc1234|subject\x01with\x02ctrl|Author|2026-05-19T10:00:00+00:00\n"

        def evil_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return _ROOT_OK
            return _make_run(0, evil_line)

        with patch("routers.git.subprocess.run", side_effect=evil_run):
            r = client.get("/api/git/recent?limit=1")
        assert r.status_code == 200
        subject = r.json()["commits"][0]["subject"]
        assert "\x01" not in subject
        assert "\x02" not in subject
        assert "\\u0001" in subject
        assert "\\u0002" in subject

    def test_default_limit_is_5(self):
        call_args = []

        def capture_run(cmd, **kwargs):
            call_args.append(cmd)
            if "rev-parse" in cmd:
                return _ROOT_OK
            return _make_run(0, "")

        with patch("routers.git.subprocess.run", side_effect=capture_run):
            client.get("/api/git/recent")

        log_cmd = [c for c in call_args if "log" in c]
        assert any("-n5" in str(c) for c in log_cmd)
