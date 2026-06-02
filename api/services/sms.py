"""Text-message (SMS) sending.

There is no SMS provider wired into yourOS today. Rather than pretend a
text went out, this module is an honest stub: it exposes the same
``send_sms`` shape a real provider would, reports that SMS is not set up,
and never silently claims success.

When someone is ready to turn texts on for real, the only change needed is
to fill in ``_send_via_provider`` with a hosted SMS provider (for example
Twilio): read the provider credentials from ``~/.myos`` the same private
way other secrets are stored, call the provider's send API, and return
``{"ok": True, ...}``. The reminders code already falls back to a working
channel and keeps an in-app record when this returns ``ok: False``, so
nothing is lost while SMS is unconfigured.

TODO(sms-provider): wire a real hosted SMS provider here. Until then
``is_configured`` returns False and ``send_sms`` reports the channel is
not set up so reminders fall back gracefully.
"""
from __future__ import annotations

import re
from pathlib import Path

# Where a future provider's credentials would live, kept out of the repo
# like every other secret. Presence of this file is what flips SMS on.
SMS_CONFIG_PATH = Path.home() / ".myos" / "sms_provider.json"


def is_configured() -> bool:
    """Return True only when a real SMS provider is set up.

    No provider is wired yet, so this is False unless someone has dropped
    real provider credentials at ``SMS_CONFIG_PATH``. We do not treat a
    saved phone number alone as "configured": a number with no provider
    still cannot send a text.
    """
    return SMS_CONFIG_PATH.exists()


def is_valid_phone_number(value: str) -> bool:
    """Loose check that a string looks like a phone number we could text.

    Accepts an optional leading +, then 7 to 15 digits (E.164 range),
    ignoring spaces, dashes, dots, and parentheses. This is a sanity
    check before saving, not full validation.
    """
    if not value:
        return False
    cleaned = re.sub(r"[\s\-\.\(\)]", "", value.strip())
    return bool(re.fullmatch(r"\+?\d{7,15}", cleaned))


async def _send_via_provider(to_number: str, body: str) -> dict:
    """Hook for a real provider. Not implemented yet.

    Replace this with a hosted SMS provider call when texts go live.
    """
    raise NotImplementedError("No SMS provider is wired yet.")


async def send_sms(to_number: str, body: str) -> dict:
    """Attempt to send a text. Returns a result dict, never raises.

    While no provider is configured this returns ``ok: False`` with a
    plain-language reason so the caller can fall back to a channel that
    works and tell the person their text could not be sent.
    """
    if not to_number:
        return {"ok": False, "error": "No phone number is saved for texts."}
    if not is_valid_phone_number(to_number):
        return {"ok": False, "error": "That phone number does not look right."}
    if not is_configured():
        return {
            "ok": False,
            "error": "Text messages are not set up yet on this yourOS.",
        }
    try:
        return await _send_via_provider(to_number, body)
    except NotImplementedError:
        return {
            "ok": False,
            "error": "Text messages are not set up yet on this yourOS.",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"The text could not be sent. {exc}"}


__all__ = ["SMS_CONFIG_PATH", "is_configured", "is_valid_phone_number", "send_sms"]
