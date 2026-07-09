
import pytest
import os
import json
from services.text_bridge import is_trusted_sender
from services.settings_store import settings_store


def test_hardcoded_identifier_not_trusted():
    # no identifier is hardcoded — trust comes only from configured contacts
    assert is_trusted_sender("some_handle") is False
    assert is_trusted_sender("Some.Handle@example.com") is False
    assert is_trusted_sender("some_handle_phone") is False

    # Unknown identifiers are not trusted by default
    assert is_trusted_sender("someone_else") is False
    assert is_trusted_sender("+15551234567") is False


def test_configured_contact_trust():
    # Simulate enabling and adding a trusted contact
    original = settings_store.get("text_bridge", {})
    try:
        settings_store.update({
            "text_bridge": {
                "enabled": True,
                "trusted_contacts": ["+15551234567"]
            }
        })
        
        assert is_trusted_sender("+15551234567") is True
        assert is_trusted_sender("someone_else") is False
    finally:
        # Restore
        settings_store.update({"text_bridge": original})
