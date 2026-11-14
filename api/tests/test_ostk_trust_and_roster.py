"""Tests for non-blocking HUMANFILE trust signing and ostk roster seat verdicts (->3115)."""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import provider_detection as _pd


@pytest.fixture(autouse=True)
def _reset_caches():
    _pd._reset_provider_cache()
    if hasattr(_pd, "_reset_ostk_roster_cache"):
        _pd._reset_ostk_roster_cache()
    yield
    _pd._reset_provider_cache()
    if hasattr(_pd, "_reset_ostk_roster_cache"):
        _pd._reset_ostk_roster_cache()


# ---------------------------------------------------------------------------
# parse_ostk_roster() unit tests
# ---------------------------------------------------------------------------


def test_parse_ostk_roster_all_verdict_categories():
    raw = (
        "seat             pid    health   age      session    role     serving  lineage          wake   ctx%  attested  verdict\n"
        "claude-seat-1    10421  active   45s      sess-a1    builder  ->3115   root->claude     live   18%   45s       VALID[attested]\n"
        "gemini-seat-2    10899  active   12s      sess-g2    auditor  -        root->gemini     poll   7%    12s       VALID\n"
        "rogue-worker     22001  active   3s       sess-fake  -        -        lineage-unknown  poll?  0%    -         IMPOSTOR[session-contradiction]\n"
        "myos-api-p59549  59549  active   12s      -          -        -        lineage-unknown  poll?  0%    12s       UNVERIFIABLE-YET[proc-dir-pending]\n"
        "orphan-seat      -      crashed  182d05h  -          -        -        lineage-unknown  poll?  0%    -         IMPOSTOR\n"
    )

    parsed = _pd.parse_ostk_roster(raw)

    assert parsed["available"] is True
    assert parsed["counts"] == {
        "total": 5,
        "valid": 2,
        "impostor": 2,
        "unverifiable": 1,
    }
    seats = parsed["seats"]
    assert len(seats) == 5

    assert seats[0] == {
        "seat": "claude-seat-1",
        "pid": 10421,
        "health": "active",
        "age": "45s",
        "session": "sess-a1",
        "role": "builder",
        "serving": "->3115",
        "lineage": "root->claude",
        "wake": "live",
        "ctx_pct": "18%",
        "attested": "45s",
        "verdict": "VALID[attested]",
        "verdict_category": "valid",
    }
    assert seats[1]["verdict"] == "VALID"
    assert seats[1]["verdict_category"] == "valid"
    assert seats[1]["serving"] is None

    assert seats[2]["seat"] == "rogue-worker"
    assert seats[2]["pid"] == 22001
    assert seats[2]["role"] is None
    assert seats[2]["serving"] is None
    assert seats[2]["verdict"] == "IMPOSTOR[session-contradiction]"
    assert seats[2]["verdict_category"] == "impostor"

    assert seats[3]["seat"] == "myos-api-p59549"
    assert seats[3]["pid"] == 59549
    assert seats[3]["session"] is None
    assert seats[3]["role"] is None
    assert seats[3]["serving"] is None
    assert seats[3]["verdict"] == "UNVERIFIABLE-YET[proc-dir-pending]"
    assert seats[3]["verdict_category"] == "unverifiable"

    assert seats[4]["seat"] == "orphan-seat"
    assert seats[4]["pid"] is None
    assert seats[4]["verdict"] == "IMPOSTOR"
    assert seats[4]["verdict_category"] == "impostor"


def test_parse_ostk_roster_header_only_or_empty():
    header_only = (
        "seat             pid    health  age  session  role  serving  lineage          wake   ctx%  attested  verdict\n"
    )
    assert _pd.parse_ostk_roster(header_only) == {
        "available": True,
        "seats": [],
        "counts": {"total": 0, "valid": 0, "impostor": 0, "unverifiable": 0},
    }
    assert _pd.parse_ostk_roster("") == {
        "available": True,
        "seats": [],
        "counts": {"total": 0, "valid": 0, "impostor": 0, "unverifiable": 0},
    }


# ---------------------------------------------------------------------------
# get_ostk_roster() async + cache + loop-safety tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ostk_roster_parses_and_caches_with_ttl(tmp_path):
    raw = (
        "seat             pid    health  age  session  role  serving  lineage          wake   ctx%  attested  verdict\n"
        "claude-alpha     41234  active  9s   s-alpha  dev   ->3115   root->claude     live   12%   9s        VALID[attested]\n"
    )
    calls = 0

    def fake_run(cmd, **kwargs):
        nonlocal calls
        calls += 1
        assert cmd == ["ostk", "roster"]
        assert kwargs.get("cwd") == str(tmp_path)
        assert kwargs.get("stdin") == asyncio.subprocess.DEVNULL
        res = MagicMock()
        res.returncode = 0
        res.stdout = raw
        res.stderr = ""
        return res

    with patch("services.provider_detection.subprocess.run", side_effect=fake_run):
        first = await _pd.get_ostk_roster(cwd=tmp_path)
        second = await _pd.get_ostk_roster(cwd=tmp_path)

    assert calls == 1
    assert first["available"] is True
    assert first["counts"]["valid"] == 1
    assert second == first

    _pd._reset_ostk_roster_cache()
    with patch("services.provider_detection.subprocess.run", side_effect=fake_run):
        await _pd.get_ostk_roster(cwd=tmp_path)
    assert calls == 2


@pytest.mark.asyncio
async def test_get_ostk_roster_unavailable_on_missing_binary_or_timeout(monkeypatch, tmp_path):
    with patch("services.provider_detection.subprocess.run", side_effect=FileNotFoundError("no ostk")):
        res_missing = await _pd.get_ostk_roster(cwd=tmp_path)

    assert res_missing == {
        "available": False,
        "seats": [],
        "counts": {"total": 0, "valid": 0, "impostor": 0, "unverifiable": 0},
    }

    _pd._reset_ostk_roster_cache()
    monkeypatch.setattr(_pd, "_OSTK_SUBPROCESS_TIMEOUT", 0.05)

    def slow_run(*_args, **_kwargs):
        time.sleep(0.3)
        res = MagicMock()
        res.returncode = 0
        res.stdout = ""
        res.stderr = ""
        return res

    with patch("services.provider_detection.subprocess.run", side_effect=slow_run):
        res_timeout = await _pd.get_ostk_roster(cwd=tmp_path)

    assert res_timeout["available"] is False
    assert res_timeout["seats"] == []


@pytest.mark.asyncio
async def test_get_ostk_roster_does_not_block_event_loop(tmp_path):
    def slow_roster(*_args, **_kwargs):
        time.sleep(0.25)
        res = MagicMock()
        res.returncode = 0
        res.stdout = (
            "seat  pid  health  age  session  role  serving  lineage  wake  ctx%  attested  verdict\n"
        )
        res.stderr = ""
        return res

    ticks = 0

    async def ticker():
        nonlocal ticks
        for _ in range(50):
            await asyncio.sleep(0.01)
            ticks += 1

    with patch("services.provider_detection.subprocess.run", side_effect=slow_roster):
        task = asyncio.create_task(ticker())
        await _pd.get_ostk_roster(cwd=tmp_path)
        task.cancel()

    assert ticks >= 10


# ---------------------------------------------------------------------------
# sign_ostk_humanfile() async + cache invalidation + TTY fallback tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_ostk_humanfile_success_invalidates_trust_cache_and_verifies(tmp_path):
    # Seed stale invalid trust and provider caches to verify sign_ostk_humanfile invalidates them.
    _pd._ostk_trust_cache_value = {
        "ostk_available": True,
        "humanfile_valid": False,
        "humanfile_warning": "stale warning",
    }
    _pd._ostk_trust_cache_ts = time.monotonic()
    _pd._providers_cache_value = {
        "claude_code": True,
        "humanfile_valid": False,
        "humanfile_warning": "stale warning",
    }
    _pd._providers_cache_ts = time.monotonic()

    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        res = MagicMock()
        if cmd == ["ostk", "trust", "sign", "--humanfile"]:
            assert kwargs.get("cwd") == str(tmp_path)
            assert kwargs.get("stdin") == asyncio.subprocess.DEVNULL
            assert kwargs.get("stdout") == subprocess.PIPE
            assert kwargs.get("stderr") == subprocess.PIPE
            res.returncode = 0
            res.stdout = "Signed HUMANFILE (Ed25519)\n"
            res.stderr = ""
            return res
        # Follow-up ostk boot verification inside detect_ostk_trust
        res.returncode = 0
        res.stdout = "HUMANFILE: verified (T0)\n"
        res.stderr = ""
        return res

    with patch("shutil.which", return_value="/usr/local/bin/ostk"):
        with patch("services.provider_detection.subprocess.run", side_effect=fake_run):
            result = await _pd.sign_ostk_humanfile(cwd=tmp_path)

    assert result["signed"] is True
    assert result["humanfile_valid"] is True
    assert result["requires_tty"] is False
    assert result["manual_command"] == "ostk trust sign --humanfile"
    assert result["trust"]["humanfile_valid"] is True
    assert _pd._providers_cache_value is None
    assert len(calls) == 2
    assert calls[0][0] == ["ostk", "trust", "sign", "--humanfile"]


@pytest.mark.asyncio
async def test_sign_ostk_humanfile_passphrase_or_tty_required_returns_clean_fallback(tmp_path):
    def fake_run(cmd, **_kwargs):
        res = MagicMock()
        if cmd == ["ostk", "trust", "sign", "--humanfile"]:
            res.returncode = 1
            res.stdout = ""
            res.stderr = "Load key \"/Users/test/.ssh/id_ed25519\": incorrect passphrase supplied to decrypt private key\n"
            return res
        res.returncode = 0
        res.stdout = "[humanfile] advisory: HUMANFILE has been modified since it was signed (subject hash mismatch).\n"
        res.stderr = ""
        return res

    with patch("shutil.which", return_value="/usr/local/bin/ostk"):
        with patch("services.provider_detection.subprocess.run", side_effect=fake_run):
            result = await _pd.sign_ostk_humanfile(cwd=tmp_path)

    assert result["signed"] is False
    assert result["humanfile_valid"] is False
    assert result["requires_tty"] is True
    assert result["manual_command"] == "ostk trust sign --humanfile"
    assert "ostk trust sign --humanfile" in result["message"]
    assert "passphrase" in result["message"].lower()


@pytest.mark.asyncio
async def test_sign_ostk_humanfile_timeout_sets_requires_tty_without_blocking_loop(monkeypatch, tmp_path):
    monkeypatch.setattr(_pd, "_OSTK_SUBPROCESS_TIMEOUT", 0.05)

    def hanging_sign(cmd, **_kwargs):
        res = MagicMock()
        if cmd == ["ostk", "trust", "sign", "--humanfile"]:
            time.sleep(0.25)
            res.returncode = 0
            res.stdout = ""
            res.stderr = ""
            return res
        res.returncode = 0
        res.stdout = "HUMANFILE: signature invalid - refusing secret injection\n"
        res.stderr = ""
        return res

    ticks = 0

    async def ticker():
        nonlocal ticks
        for _ in range(30):
            await asyncio.sleep(0.01)
            ticks += 1

    with patch("shutil.which", return_value="/usr/local/bin/ostk"):
        with patch("services.provider_detection.subprocess.run", side_effect=hanging_sign):
            t = asyncio.create_task(ticker())
            result = await _pd.sign_ostk_humanfile(cwd=tmp_path)
            t.cancel()

    assert result["signed"] is False
    assert result["requires_tty"] is True
    assert result["manual_command"] == "ostk trust sign --humanfile"
    assert ticks >= 3


# ---------------------------------------------------------------------------
# FastAPI router endpoint tests (/api/secrets/trust-sign & /api/secrets/roster)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_secrets_trust_sign_endpoint_success(client):
    payload = {
        "signed": True,
        "humanfile_valid": True,
        "trust": {"ostk_available": True, "humanfile_valid": True, "humanfile_warning": None},
        "message": "HUMANFILE signed and verified.",
        "manual_command": "ostk trust sign --humanfile",
        "requires_tty": False,
    }
    with patch("services.provider_detection.sign_ostk_humanfile", new=AsyncMock(return_value=payload)):
        resp = await client.post("/api/secrets/trust-sign")

    assert resp.status_code == 200
    assert resp.json() == payload


@pytest.mark.asyncio
async def test_post_secrets_trust_sign_endpoint_passphrase_fallback_returns_200(client):
    payload = {
        "signed": False,
        "humanfile_valid": False,
        "trust": {
            "ostk_available": True,
            "humanfile_valid": False,
            "humanfile_warning": "HUMANFILE was modified since it was signed.",
        },
        "message": "Signing key requires an interactive passphrase. Run `ostk trust sign --humanfile` in your terminal.",
        "manual_command": "ostk trust sign --humanfile",
        "requires_tty": True,
    }
    with patch("services.provider_detection.sign_ostk_humanfile", new=AsyncMock(return_value=payload)):
        resp = await client.post("/api/secrets/trust-sign")

    assert resp.status_code == 200
    data = resp.json()
    assert data["signed"] is False
    assert data["requires_tty"] is True
    assert data["manual_command"] == "ostk trust sign --humanfile"


@pytest.mark.asyncio
async def test_get_secrets_roster_endpoint(client):
    roster_payload = {
        "available": True,
        "seats": [
            {
                "seat": "claude-seat-1",
                "pid": 10421,
                "health": "active",
                "age": "45s",
                "session": "sess-a1",
                "role": "builder",
                "serving": "->3115",
                "lineage": "root->claude",
                "wake": "live",
                "ctx_pct": "18%",
                "attested": "45s",
                "verdict": "VALID[attested]",
                "verdict_category": "valid",
            }
        ],
        "counts": {"total": 1, "valid": 1, "impostor": 0, "unverifiable": 0},
    }
    with patch("services.provider_detection.get_ostk_roster", new=AsyncMock(return_value=roster_payload)):
        resp = await client.get("/api/secrets/roster")

    assert resp.status_code == 200
    assert resp.json() == roster_payload
