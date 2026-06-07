"""Web Push notification service.

Manages VAPID keys and push subscriptions stored in ~/.youros/ so they
survive app restarts and git pulls without touching the repo.

Uses pywebpush when available, falls back to the cryptography package
for VAPID key generation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

MYOS_DIR = Path.home() / ".youros"
VAPID_KEYS_FILE = MYOS_DIR / "vapid_keys.json"
SUBSCRIPTIONS_FILE = MYOS_DIR / "push_subscriptions.json"

logger = logging.getLogger(__name__)


class PushService:
    """Manages VAPID keys and push subscriptions for Web Push."""

    def _ensure_dir(self) -> None:
        MYOS_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # VAPID key management
    # ------------------------------------------------------------------

    def get_vapid_keys(self) -> dict:
        """Return the VAPID key pair, generating on first call."""
        self._ensure_dir()
        if VAPID_KEYS_FILE.exists():
            try:
                keys = json.loads(VAPID_KEYS_FILE.read_text())
                if keys.get("public") and keys.get("private"):
                    return keys
            except (json.JSONDecodeError, OSError):
                pass
        return self._generate_vapid_keys()

    def get_public_key(self) -> str:
        """Return just the public VAPID key for the browser."""
        return self.get_vapid_keys()["public"]

    def _generate_vapid_keys(self) -> dict:
        """Generate a new VAPID key pair and persist it."""
        try:
            from py_vapid import Vapid

            vapid = Vapid()
            vapid.generate_keys()
            raw_private = vapid.private_pem()
            raw_public = vapid.public_key
            # py_vapid stores keys in PEM, we need the URL-safe base64
            # applicationServerKey for the browser. Decode the public key
            # from the Vapid instance directly.
            import base64
            pub_raw = vapid.public_key.public_bytes(
                encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.X962,
                format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"]).PublicFormat.UncompressedPoint,
            )
            public_b64 = base64.urlsafe_b64encode(pub_raw).rstrip(b"=").decode()
            private_b64 = base64.urlsafe_b64encode(
                vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
            ).rstrip(b"=").decode()
            keys = {"public": public_b64, "private": private_b64}
        except ImportError:
            # Fall back to cryptography package directly
            keys = self._generate_vapid_keys_crypto()

        self._ensure_dir()
        VAPID_KEYS_FILE.write_text(json.dumps(keys, indent=2))
        return keys

    def _generate_vapid_keys_crypto(self) -> dict:
        """Generate VAPID keys using the cryptography package."""
        import base64
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization

        private_key = ec.generate_private_key(ec.SECP256R1())

        # Public key in uncompressed point format (65 bytes)
        pub_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
        public_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()

        # Private key as raw 32-byte integer
        priv_numbers = private_key.private_numbers()
        priv_bytes = priv_numbers.private_value.to_bytes(32, "big")
        private_b64 = base64.urlsafe_b64encode(priv_bytes).rstrip(b"=").decode()

        return {"public": public_b64, "private": private_b64}

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def _load_subscriptions(self) -> list[dict]:
        self._ensure_dir()
        if not SUBSCRIPTIONS_FILE.exists():
            return []
        try:
            data = json.loads(SUBSCRIPTIONS_FILE.read_text())
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save_subscriptions(self, subs: list[dict]) -> None:
        self._ensure_dir()
        SUBSCRIPTIONS_FILE.write_text(json.dumps(subs, indent=2))

    def add_subscription(self, subscription: dict) -> None:
        """Save a push subscription, deduplicating by endpoint."""
        subs = self._load_subscriptions()
        endpoint = subscription.get("endpoint", "")
        # Replace existing subscription with same endpoint
        subs = [s for s in subs if s.get("endpoint") != endpoint]
        subs.append(subscription)
        self._save_subscriptions(subs)

    def remove_subscription(self, endpoint: str) -> bool:
        """Remove a subscription by endpoint. Returns True if found."""
        subs = self._load_subscriptions()
        original = len(subs)
        subs = [s for s in subs if s.get("endpoint") != endpoint]
        if len(subs) < original:
            self._save_subscriptions(subs)
            return True
        return False

    def list_subscriptions(self) -> list[dict]:
        """Return all stored subscriptions."""
        return self._load_subscriptions()

    # ------------------------------------------------------------------
    # Sending push notifications
    # ------------------------------------------------------------------

    def send_to_all(
        self,
        title: str,
        body: str,
        url: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> int:
        """Send a push notification to all subscriptions.

        Returns the number of successfully delivered pushes.
        Silently removes subscriptions that return 404/410 (unsubscribed).
        """
        subs = self._load_subscriptions()
        if not subs:
            return 0

        keys = self.get_vapid_keys()
        payload = json.dumps({
            "title": title,
            "body": body,
            "url": url or "/",
            "tag": tag or "myos",
        })

        delivered = 0
        stale_endpoints: list[str] = []

        try:
            from pywebpush import webpush, WebPushException
        except ImportError:
            logger.warning(
                "pywebpush not installed, cannot send push notifications. "
                "Install with: pip install pywebpush"
            )
            return 0

        for sub in subs:
            try:
                webpush(
                    subscription_info=sub,
                    data=payload,
                    vapid_private_key=keys["private"],
                    vapid_claims={"sub": "mailto:myos@localhost"},
                )
                delivered += 1
            except WebPushException as e:
                # 404 or 410 means the subscription is gone
                if hasattr(e, "response") and e.response is not None:
                    status = getattr(e.response, "status_code", 0)
                    if status in (404, 410):
                        stale_endpoints.append(sub.get("endpoint", ""))
                        continue
                logger.debug("Push failed for %s: %s", sub.get("endpoint", "?"), e)
            except Exception as e:
                logger.debug("Push failed for %s: %s", sub.get("endpoint", "?"), e)

        # Clean up stale subscriptions
        if stale_endpoints:
            subs = [s for s in subs if s.get("endpoint") not in stale_endpoints]
            self._save_subscriptions(subs)

        return delivered


push_service = PushService()
