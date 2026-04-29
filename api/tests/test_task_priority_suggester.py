"""Tests for task_priority_suggester and the task_labeling wrappers.

Each scoring rule is tested independently per the →859 AC.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.task_priority_suggester import suggest_priority, decay_priority, days_since
from services.task_labeling import get_priority_suggestion, get_decayed_priority


# ---------------------------------------------------------------------------
# suggest_priority: urgency signal
# ---------------------------------------------------------------------------

class TestUrgencySignal:
    def test_urgent_keyword_gives_p1(self):
        r = suggest_priority("Fix urgent login bug")
        assert r["priority"] == "P1"
        assert r["confidence"] >= 0.8
        assert "urgency" in r["reason"].lower() or "urgent" in r["reason"].lower()

    def test_asap_keyword_gives_p1(self):
        r = suggest_priority("Deploy the new build asap")
        assert r["priority"] == "P1"
        assert r["confidence"] >= 0.8

    def test_critical_keyword_gives_p1(self):
        r = suggest_priority("Critical security patch needed")
        assert r["priority"] == "P1"

    def test_blocker_keyword_gives_p1(self):
        r = suggest_priority("API is a blocker for the demo")
        assert r["priority"] == "P1"

    def test_outage_keyword_gives_p1(self):
        r = suggest_priority("Production outage in payment service")
        assert r["priority"] == "P1"

    def test_security_keyword_gives_p1(self):
        r = suggest_priority("Security vulnerability in auth flow")
        assert r["priority"] == "P1"

    def test_keyword_in_description_counts(self):
        r = suggest_priority("Fix the login page", "This is urgent, users are blocked")
        assert r["priority"] == "P1"

    def test_case_insensitive(self):
        r = suggest_priority("URGENT: deploy hotfix")
        assert r["priority"] == "P1"


# ---------------------------------------------------------------------------
# suggest_priority: deadline signal
# ---------------------------------------------------------------------------

class TestDeadlineSignal:
    def test_by_friday_gives_p1(self):
        r = suggest_priority("Ship the report by Friday")
        assert r["priority"] == "P1"
        assert r["confidence"] >= 0.7

    def test_before_demo_gives_p1(self):
        r = suggest_priority("Fix pagination before the demo")
        assert r["priority"] == "P1"

    def test_before_release_gives_p1(self):
        r = suggest_priority("Update changelog before release")
        assert r["priority"] == "P1"

    def test_due_today_gives_p1(self):
        r = suggest_priority("Invoice due today")
        assert r["priority"] == "P1"

    def test_end_of_week_gives_p1(self):
        r = suggest_priority("Finish review end of week")
        assert r["priority"] == "P1"

    def test_reason_mentions_deadline(self):
        r = suggest_priority("Finish by Monday")
        assert "deadline" in r["reason"].lower() or "by" in r["reason"].lower()


# ---------------------------------------------------------------------------
# suggest_priority: low-urgency signal
# ---------------------------------------------------------------------------

class TestLowUrgencySignal:
    def test_research_gives_p3(self):
        r = suggest_priority("Research new authentication libraries")
        assert r["priority"] == "P3"
        assert r["confidence"] >= 0.6

    def test_explore_gives_p3(self):
        r = suggest_priority("Explore dark mode options")
        assert r["priority"] == "P3"

    def test_someday_gives_p3(self):
        r = suggest_priority("Someday migrate to Postgres")
        assert r["priority"] == "P3"

    def test_nice_to_have_gives_p3(self):
        r = suggest_priority("Nice-to-have: add tooltips")
        assert r["priority"] == "P3"

    def test_reason_mentions_exploratory(self):
        r = suggest_priority("Investigate caching options")
        assert "exploratory" in r["reason"].lower() or "signal" in r["reason"].lower()


# ---------------------------------------------------------------------------
# suggest_priority: no signal defaults to P2
# ---------------------------------------------------------------------------

class TestDefaultPriority:
    def test_no_signal_gives_p2(self):
        r = suggest_priority("Update the team wiki page")
        assert r["priority"] == "P2"

    def test_low_confidence_on_default(self):
        r = suggest_priority("Refactor the sidebar component")
        assert r["confidence"] < 0.6

    def test_empty_description_ok(self):
        r = suggest_priority("Add unit tests")
        assert r["priority"] in {"P1", "P2", "P3"}


# ---------------------------------------------------------------------------
# decay_priority: stale-decay rule
# ---------------------------------------------------------------------------

class TestDecayPriority:
    def test_p1_decays_to_p2_after_window(self):
        assert decay_priority("P1", days_stale=14, decay_days=14) == "P2"

    def test_p1_not_decayed_before_window(self):
        assert decay_priority("P1", days_stale=13, decay_days=14) == "P1"

    def test_p2_decays_to_p3_after_double_window(self):
        assert decay_priority("P2", days_stale=28, decay_days=14) == "P3"

    def test_p2_not_decayed_before_double_window(self):
        assert decay_priority("P2", days_stale=27, decay_days=14) == "P2"

    def test_p0_never_decayed(self):
        assert decay_priority("P0", days_stale=999, decay_days=1) == "P0"

    def test_p3_never_decayed(self):
        assert decay_priority("P3", days_stale=999, decay_days=1) == "P3"

    def test_custom_decay_window(self):
        assert decay_priority("P1", days_stale=7, decay_days=7) == "P2"
        assert decay_priority("P1", days_stale=6, decay_days=7) == "P1"


# ---------------------------------------------------------------------------
# days_since helper
# ---------------------------------------------------------------------------

class TestDaysSince:
    def test_recent_timestamp_gives_zero(self):
        now = datetime.now(tz=timezone.utc).isoformat()
        assert days_since(now) == 0

    def test_old_timestamp_gives_positive_days(self):
        old = (datetime.now(tz=timezone.utc) - timedelta(days=10)).isoformat()
        assert days_since(old) == 10

    def test_invalid_timestamp_gives_zero(self):
        assert days_since("not-a-date") == 0

    def test_z_suffix_handled(self):
        ts = "2020-01-01T00:00:00Z"
        result = days_since(ts)
        assert result > 0


# ---------------------------------------------------------------------------
# task_labeling wrappers (no regression on existing flow)
# ---------------------------------------------------------------------------

class TestTaskLabelingWrappers:
    def test_get_priority_suggestion_delegates(self):
        r = get_priority_suggestion("Fix urgent crash")
        assert r["priority"] == "P1"
        assert "confidence" in r
        assert "reason" in r

    def test_get_decayed_priority_recent_task_unchanged(self):
        now = datetime.now(tz=timezone.utc).isoformat()
        assert get_decayed_priority("P1", now, decay_days=14) == "P1"

    def test_get_decayed_priority_old_p1_demoted(self):
        old = (datetime.now(tz=timezone.utc) - timedelta(days=20)).isoformat()
        assert get_decayed_priority("P1", old, decay_days=14) == "P2"

    def test_schedule_auto_labels_still_importable(self):
        from services.task_labeling import schedule_auto_labels
        assert callable(schedule_auto_labels)
