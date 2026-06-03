"""Tests for the Executive Summary backend (portfolio_health + portfolio router).

All hermetic: no real file I/O beyond tmp_path/monkeypatch, no network. The
atlassian client is mocked the way test_atlassian_sync.py mocks it.
"""
from __future__ import annotations

import pytest

from services import portfolio_health


def test_unconfigured_when_no_config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_health, "PORTFOLIO_CONFIG_PATH", tmp_path / "portfolio.json")
    cfg = portfolio_health.load_portfolio_config()
    assert cfg == {}
    assert portfolio_health.is_configured(cfg) is False
