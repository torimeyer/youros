"""Org signing key service.

Manages Ed25519 keypairs for signing catalog items. Private key never
leaves disk. All operations are synchronous (file I/O only).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from services.youros_paths import youros_home

_MYOS_DIR = youros_home()


def _org_key_dir(org_id: str) -> Path:
    d = _MYOS_DIR / "orgs" / org_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _priv_path(org_id: str) -> Path:
    return _org_key_dir(org_id) / "signing.key"


def _pub_path(org_id: str) -> Path:
    return _org_key_dir(org_id) / "signing.pub"


def _meta_path(org_id: str) -> Path:
    return _org_key_dir(org_id) / "signing.meta.json"


def get_status(org_id: str) -> dict:
    """Return {exists, fingerprint, generated_at}. Never touches private key."""
    pub = _pub_path(org_id)
    if not pub.exists():
        return {"exists": False, "fingerprint": None, "generated_at": None}

    pub_bytes = pub.read_bytes()
    fingerprint = hashlib.sha256(pub_bytes).hexdigest()
    generated_at = None
    meta = _meta_path(org_id)
    if meta.exists():
        try:
            generated_at = json.loads(meta.read_text()).get("generated_at")
        except Exception:
            pass
    return {"exists": True, "fingerprint": fingerprint, "generated_at": generated_at}


def generate(org_id: str) -> dict:
    """Generate a new Ed25519 keypair. Overwrites any existing key. Returns status."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat, PrivateFormat, NoEncryption,
    )

    private_key = Ed25519PrivateKey.generate()
    pub_key = private_key.public_key()

    priv_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_bytes = pub_key.public_bytes(Encoding.Raw, PublicFormat.Raw)

    _priv_path(org_id).write_bytes(priv_bytes)
    _pub_path(org_id).write_bytes(pub_bytes)

    fingerprint = hashlib.sha256(pub_bytes).hexdigest()
    generated_at = datetime.now(timezone.utc).isoformat()
    _meta_path(org_id).write_text(json.dumps({"generated_at": generated_at}))

    return {"fingerprint": fingerprint, "generated_at": generated_at}


def revoke(org_id: str) -> None:
    """Delete all key files for the org."""
    for p in (_priv_path(org_id), _pub_path(org_id), _meta_path(org_id)):
        if p.exists():
            p.unlink()


def sign_content(content: str, org_id: str) -> Optional[str]:
    """Sign content hash with the org private key. Returns hex signature or None."""
    priv = _priv_path(org_id)
    if not priv.exists():
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

        raw = priv.read_bytes()
        private_key = Ed25519PrivateKey.from_private_bytes(raw)
        content_hash = hashlib.sha256(content.encode()).digest()
        sig = private_key.sign(content_hash)
        return sig.hex()
    except Exception:
        return None


def verify_content(content: str, signature_hex: str, org_id: str) -> Optional[bool]:
    """Verify signature against content hash. Returns True/False or None if no key."""
    pub = _pub_path(org_id)
    if not pub.exists():
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature

        pub_bytes = pub.read_bytes()
        public_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
        content_hash = hashlib.sha256(content.encode()).digest()
        sig_bytes = bytes.fromhex(signature_hex)
        public_key.verify(sig_bytes, content_hash)
        return True
    except Exception:
        return False
