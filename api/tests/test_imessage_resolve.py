"""Tests for the iMessage contact resolution feature."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# _names_match unit tests
# ---------------------------------------------------------------------------


def test_names_match_exact():
    from services.imessage import _names_match
    assert _names_match("mom", "Mom") is True


def test_names_match_prefix():
    from services.imessage import _names_match
    assert _names_match("lil", "Lil Oatmeal") is True


def test_names_match_substring():
    from services.imessage import _names_match
    assert _names_match("oatmeal", "Lil Oatmeal") is True


def test_names_match_all_words_in_name():
    from services.imessage import _names_match
    assert _names_match("lil oatmeal", "Lil Oatmeal") is True


def test_names_match_empty_query():
    from services.imessage import _names_match
    assert _names_match("", "Lil Oatmeal") is False


def test_names_match_empty_name():
    from services.imessage import _names_match
    assert _names_match("lil", "") is False


def test_names_match_no_match():
    from services.imessage import _names_match
    assert _names_match("xavier", "Lil Oatmeal") is False


# ---------------------------------------------------------------------------
# resolve_contact_phrase_sync unit tests
# ---------------------------------------------------------------------------


def test_resolve_finds_match_in_contacts_cache():
    from services import imessage

    fake_cache = {"+15550001234": "Lil Oatmeal"}
    with patch.object(imessage, "_load_contacts_cache", return_value=fake_cache), \
         patch.object(imessage, "get_conversations_sync", return_value=[]):
        result = imessage.resolve_contact_phrase_sync("lil oatmeal goodnight")

    assert result["identifier"] == "+15550001234"
    assert result["display_name"] == "Lil Oatmeal"
    assert result["message_text"] == "goodnight"


def test_resolve_finds_match_in_conversations():
    from services import imessage

    fake_convs = [
        {"identifier": "+15550009999", "display_name": "Mom", "service": "iMessage"},
    ]
    with patch.object(imessage, "_load_contacts_cache", return_value={}), \
         patch.object(imessage, "get_conversations_sync", return_value=fake_convs):
        result = imessage.resolve_contact_phrase_sync("mom I will be late")

    assert result["identifier"] == "+15550009999"
    assert result["display_name"] == "Mom"
    assert result["message_text"] == "I will be late"


def test_resolve_multi_word_name():
    from services import imessage

    fake_convs = [
        {"identifier": "+15550007777", "display_name": "Big Oatmeal", "service": "iMessage"},
    ]
    with patch.object(imessage, "_load_contacts_cache", return_value={}), \
         patch.object(imessage, "get_conversations_sync", return_value=fake_convs):
        result = imessage.resolve_contact_phrase_sync("big oatmeal good morning")

    assert result["display_name"] == "Big Oatmeal"
    assert result["message_text"] == "good morning"


def test_resolve_raises_when_no_contact_found():
    from services import imessage

    with patch.object(imessage, "_load_contacts_cache", return_value={}), \
         patch.object(imessage, "get_conversations_sync", return_value=[]):
        with pytest.raises(ValueError, match="No contact found"):
            imessage.resolve_contact_phrase_sync("zzznobody hello there")


def test_resolve_raises_for_too_short_phrase():
    from services import imessage

    with patch.object(imessage, "_load_contacts_cache", return_value={}), \
         patch.object(imessage, "get_conversations_sync", return_value=[]):
        with pytest.raises(ValueError):
            imessage.resolve_contact_phrase_sync("mom")

