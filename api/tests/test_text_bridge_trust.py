
import pytest
import os
import json
from services.text_bridge import is_trusted_sender
from services.settings_store import settings_store

def test_vmeyer_trust():
    # 1. Identifier containing 'vmeyer' (case-insensitive) should be trusted
    assert is_trusted_sender("vmeyer") is True
    assert is_trusted_sender("Tori.VMeyer@example.com") is True
    assert is_trusted_sender("vmeyer_phone") is True
    
    # 2. Other identifiers should NOT be trusted by default
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
