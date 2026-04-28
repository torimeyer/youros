"""Tests for org signing key endpoints and signing service."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import signing as svc


# ---------------------------------------------------------------------------
# Service-level tests
# ---------------------------------------------------------------------------


@pytest.fixture
def key_dir(tmp_path):
    with patch.object(svc, "_MYOS_DIR", tmp_path):
        yield tmp_path


class TestSigningService:
    def test_status_no_key(self, key_dir):
        result = svc.get_status("test-org")
        assert result == {"exists": False, "fingerprint": None, "generated_at": None}

    def test_generate_creates_files(self, key_dir):
        svc.generate("test-org")
        assert (key_dir / "orgs" / "test-org" / "signing.key").exists()
        assert (key_dir / "orgs" / "test-org" / "signing.pub").exists()
        assert (key_dir / "orgs" / "test-org" / "signing.meta.json").exists()

    def test_generate_returns_fingerprint(self, key_dir):
        result = svc.generate("test-org")
        assert "fingerprint" in result
        assert len(result["fingerprint"]) == 64  # SHA-256 hex
        assert "generated_at" in result

    def test_status_exists_after_generate(self, key_dir):
        svc.generate("test-org")
        status = svc.get_status("test-org")
        assert status["exists"] is True
        assert status["fingerprint"] is not None
        assert status["generated_at"] is not None

    def test_private_key_never_in_status(self, key_dir):
        svc.generate("test-org")
        status = svc.get_status("test-org")
        assert "private" not in str(status).lower()
        for v in status.values():
            if isinstance(v, (bytes, str)) and len(str(v)) > 100:
                pytest.fail("Status response may contain private key material")

    def test_revoke_deletes_files(self, key_dir):
        svc.generate("test-org")
        svc.revoke("test-org")
        assert not (key_dir / "orgs" / "test-org" / "signing.key").exists()
        assert not (key_dir / "orgs" / "test-org" / "signing.pub").exists()

    def test_status_not_exists_after_revoke(self, key_dir):
        svc.generate("test-org")
        svc.revoke("test-org")
        status = svc.get_status("test-org")
        assert status["exists"] is False

    def test_sign_returns_hex(self, key_dir):
        svc.generate("test-org")
        sig = svc.sign_content("hello world", "test-org")
        assert sig is not None
        assert all(c in "0123456789abcdef" for c in sig)

    def test_sign_no_key_returns_none(self, key_dir):
        sig = svc.sign_content("hello world", "no-key-org")
        assert sig is None

    def test_verify_valid_signature(self, key_dir):
        svc.generate("test-org")
        content = "catalog item content"
        sig = svc.sign_content(content, "test-org")
        assert sig is not None
        assert svc.verify_content(content, sig, "test-org") is True

    def test_verify_invalid_signature(self, key_dir):
        svc.generate("test-org")
        sig = svc.sign_content("original", "test-org")
        assert sig is not None
        assert svc.verify_content("tampered", sig, "test-org") is False

    def test_verify_no_key_returns_none(self, key_dir):
        result = svc.verify_content("content", "a" * 128, "no-key-org")
        assert result is None


# ---------------------------------------------------------------------------
# Router-level tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_endpoint_no_key(client, tmp_path):
    with patch.object(svc, "_MYOS_DIR", tmp_path):
        with patch("services.enterprise_store.get_org", return_value=None):
            resp = await client.get("/api/org/signing-key")
    assert resp.status_code == 200
    data = resp.json()
    assert data["exists"] is False
    assert data["fingerprint"] is None


@pytest.mark.asyncio
async def test_generate_endpoint_success(client, tmp_path):
    with patch.object(svc, "_MYOS_DIR", tmp_path):
        with patch("services.enterprise_store.get_org", return_value=None):
            with patch("services.auth.get_current_user", return_value=None):
                resp = await client.post("/api/org/signing-key/generate")
    assert resp.status_code == 200
    data = resp.json()
    assert "fingerprint" in data
    assert len(data["fingerprint"]) == 64


@pytest.mark.asyncio
async def test_generate_endpoint_admin_guard(client, tmp_path):
    with patch.object(svc, "_MYOS_DIR", tmp_path):
        with patch("services.enterprise_store.get_org", return_value=None):
            with patch("services.auth.get_current_user", return_value={"role": "member", "email": "m@test.com"}):
                resp = await client.post("/api/org/signing-key/generate")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_status_endpoint_after_generate(client, tmp_path):
    with patch.object(svc, "_MYOS_DIR", tmp_path):
        with patch("services.enterprise_store.get_org", return_value=None):
            with patch("services.auth.get_current_user", return_value=None):
                await client.post("/api/org/signing-key/generate")
            resp = await client.get("/api/org/signing-key")
    assert resp.status_code == 200
    assert resp.json()["exists"] is True


@pytest.mark.asyncio
async def test_revoke_endpoint_success(client, tmp_path):
    with patch.object(svc, "_MYOS_DIR", tmp_path):
        with patch("services.enterprise_store.get_org", return_value=None):
            with patch("services.auth.get_current_user", return_value=None):
                await client.post("/api/org/signing-key/generate")
                resp = await client.post("/api/org/signing-key/revoke")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "warning" in data


@pytest.mark.asyncio
async def test_revoke_endpoint_admin_guard(client, tmp_path):
    with patch.object(svc, "_MYOS_DIR", tmp_path):
        with patch("services.enterprise_store.get_org", return_value=None):
            with patch("services.auth.get_current_user", return_value={"role": "member", "email": "m@test.com"}):
                resp = await client.post("/api/org/signing-key/revoke")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_status_not_exists_after_revoke(client, tmp_path):
    with patch.object(svc, "_MYOS_DIR", tmp_path):
        with patch("services.enterprise_store.get_org", return_value=None):
            with patch("services.auth.get_current_user", return_value=None):
                await client.post("/api/org/signing-key/generate")
                await client.post("/api/org/signing-key/revoke")
            resp = await client.get("/api/org/signing-key")
    assert resp.status_code == 200
    assert resp.json()["exists"] is False
