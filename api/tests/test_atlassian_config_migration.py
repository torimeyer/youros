"""Tests for Atlassian config migration: {site} → {jira_site, confluence_site}."""
import pytest
from services.atlassian import _migrate_atlassian_config


def test_migrate_old_site_key_fills_both():
    old = {"site": "example.atlassian.net", "email": "user@example.com"}
    result = _migrate_atlassian_config(old)
    assert result["jira_site"] == "example.atlassian.net"
    assert result["confluence_site"] == "example.atlassian.net"
    assert result["email"] == "user@example.com"


def test_migrate_is_idempotent_on_already_migrated():
    already = {"jira_site": "jira.example.com", "confluence_site": "conf.example.com", "email": "a@b.com"}
    result = _migrate_atlassian_config(already)
    assert result["jira_site"] == "jira.example.com"
    assert result["confluence_site"] == "conf.example.com"


def test_migrate_does_not_overwrite_explicit_split():
    cfg = {"site": "old.atlassian.net", "jira_site": "jira.example.com", "confluence_site": "conf.example.com"}
    result = _migrate_atlassian_config(cfg)
    assert result["jira_site"] == "jira.example.com"
    assert result["confluence_site"] == "conf.example.com"


def test_migrate_preserves_other_fields():
    old = {"site": "example.atlassian.net", "email": "u@e.com", "cloud_id": "abc123", "auth_method": "oauth"}
    result = _migrate_atlassian_config(old)
    assert result["cloud_id"] == "abc123"
    assert result["auth_method"] == "oauth"


def test_migrate_empty_config_is_noop():
    result = _migrate_atlassian_config({})
    assert result == {}
