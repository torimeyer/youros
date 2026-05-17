"""Tests for extended compute_spec_status with claims support (→1422).

Phase 1: Backend plumbing for spec-claims registry.

RED tests — these fail until _spec_claims, /claim endpoint, and
compute_spec_status claims param are implemented.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Unit tests: compute_spec_status with claims list
# ---------------------------------------------------------------------------

class TestComputeSpecStatusWithClaims:
    """compute_spec_status must incorporate active claims into its rules."""

    def setup_method(self):
        from services.ostk import OstkService
        self.compute = OstkService.compute_spec_status

    def test_no_tasks_no_claims_is_ready(self):
        """Baseline: no tasks, no claims -> ready (unchanged behaviour)."""
        status = self.compute("spec", [], {}, claims=[])
        assert status == "ready"

    def test_no_tasks_with_active_claim_is_in_progress(self):
        """RED 1: a claim with no tasks flips status to in-progress.

        This is the core scenario: terminal agent has claimed the spec
        before any tasks exist.  The UI must show Building not Ready.
        """
        claim = {
            "agent": "gemini-terminal",
            "source": "agent",
            "started_at": "2026-05-16T10:00:00+00:00",
            "task_ids": [],
        }
        status = self.compute("spec", [], {}, claims=[claim])
        assert status == "in-progress", (
            "A spec with an active claim but no tasks must be in-progress, "
            f"got {status!r}"
        )

    def test_active_claim_with_open_tasks_is_in_progress(self):
        """Claim + open tasks -> in-progress (both conditions hold)."""
        claim = {
            "agent": "gemini-terminal",
            "source": "agent",
            "started_at": "2026-05-16T10:00:00+00:00",
            "task_ids": ["100", "101"],
        }
        status = self.compute(
            "spec",
            ["100", "101"],
            {"100": "open", "101": "open"},
            claims=[claim],
        )
        assert status == "in-progress"

    def test_all_tasks_closed_but_active_claim_still_in_progress(self):
        """RED 2 (part a): claim with task_ids not all closed keeps in-progress.

        If the claim lists task 99 as its task_id but that task is still
        open, the claim is still active and status stays in-progress.
        """
        claim = {
            "agent": "gemini-terminal",
            "source": "agent",
            "started_at": "2026-05-16T10:00:00+00:00",
            "task_ids": ["99"],
        }
        status = self.compute(
            "spec",
            ["99"],
            {"99": "open"},
            claims=[claim],
        )
        assert status == "in-progress"

    def test_all_tasks_closed_claim_auto_released_is_complete(self):
        """RED 2 (part b): when all task_ids in a claim are closed, the
        claim auto-releases and status flips to complete.

        compute_spec_status must detect that the claim's task_ids are all
        closed, treat the claim as inactive, and return 'complete'.
        """
        claim = {
            "agent": "gemini-terminal",
            "source": "agent",
            "started_at": "2026-05-16T10:00:00+00:00",
            "task_ids": ["200", "201"],
        }
        status = self.compute(
            "spec",
            ["200", "201"],
            {"200": "closed", "201": "closed"},
            claims=[claim],
        )
        assert status == "complete", (
            "When all claim task_ids are closed, the claim must auto-release "
            f"and status must be complete, got {status!r}"
        )

    def test_empty_task_ids_in_claim_auto_releases(self):
        """A claim with task_ids=[] auto-releases when all tasks are closed.

        A claim created before decomposition completes has task_ids=[].
        Once the spec has tasks and they all close, status -> complete.
        """
        claim = {
            "agent": "passive",
            "source": "passive",
            "started_at": "2026-05-16T10:00:00+00:00",
            "task_ids": [],
        }
        # Spec has tasks, all closed, but claim has empty task_ids.
        # The spec-level task_ids show all closed -> complete (claim
        # with no task_ids has nothing to keep active).
        status = self.compute(
            "spec",
            ["300"],
            {"300": "closed"},
            claims=[claim],
        )
        assert status == "complete", (
            f"Claim with empty task_ids must not block complete, got {status!r}"
        )

    def test_draft_and_plan_unchanged_by_claims(self):
        """Claims never override draft/plan status."""
        claim = {"agent": "x", "source": "agent",
                 "started_at": "2026-05-16T10:00:00+00:00", "task_ids": []}
        assert self.compute("draft", [], {}, claims=[claim]) == "draft"
        assert self.compute("plan", [], {}, claims=[claim]) == "plan"

    def test_claims_none_default_backward_compat(self):
        """claims=None (default) leaves all existing status logic unchanged."""
        from services.ostk import OstkService

        # ready
        assert OstkService.compute_spec_status("spec", [], {}) == "ready"
        # in-progress
        assert OstkService.compute_spec_status(
            "spec", ["1"], {"1": "open"}
        ) == "in-progress"
        # complete
        assert OstkService.compute_spec_status(
            "spec", ["1"], {"1": "closed"}
        ) == "complete"


# ---------------------------------------------------------------------------
# Integration tests: POST /api/specs/{path}/claim endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_claim_flips_ready_spec_to_in_progress(
    client, tmp_path, monkeypatch
):
    """RED 1 (integration): POST /claim on a ready spec (no tasks) returns
    200 and a subsequent GET /tasks shows claims with the agent's name.
    The returned task_ids list may be empty for a spec not yet decomposed.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_file = tmp_path / "docs" / "spec" / "my-claim-spec.md"
    spec_file.write_text(
        "---\ntitle: My Claim Spec\nstatus: spec\n---\n\n- [ ] Do the thing\n"
    )

    # Stub spec_tasks: returns empty (no tasks yet)
    from unittest.mock import AsyncMock
    monkeypatch.setattr(ostk_module.ostk, "spec_tasks", AsyncMock(return_value=[]))
    # Stub doc_decompose: no-op (nothing to decompose in this test)
    monkeypatch.setattr(ostk_module.ostk, "doc_decompose", AsyncMock(return_value={}))

    # Clear any prior claims for this spec
    specs_router._spec_claims.pop("docs/spec/my-claim-spec.md", None)

    resp = await client.post(
        "/api/specs/docs/spec/my-claim-spec.md/claim",
        json={"agent": "gemini-terminal", "source": "agent"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "task_ids" in data, f"Response missing task_ids: {data}"

    # Claims must now appear in GET /tasks
    tasks_resp = await client.get("/api/specs/docs/spec/my-claim-spec.md/tasks")
    assert tasks_resp.status_code == 200
    tasks_data = tasks_resp.json()
    assert "claims" in tasks_data, f"GET /tasks response missing claims key: {tasks_data}"
    claims = tasks_data["claims"]
    assert len(claims) >= 1, f"Expected at least 1 claim, got: {claims}"
    assert claims[0]["agent"] == "gemini-terminal"
    assert claims[0]["source"] == "agent"

    # Cleanup
    specs_router._spec_claims.pop("docs/spec/my-claim-spec.md", None)


@pytest.mark.asyncio
async def test_build_endpoint_records_source_build_claim(
    client, tmp_path, monkeypatch
):
    """RED 3: Build endpoint records a source=build claim in _spec_claims.

    After clicking Build, _spec_claims must have an entry with source='build'
    for the spec so the status reflects in-progress immediately even before
    any agent closes its task.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router
    from routers import agents as agents_router
    from unittest.mock import AsyncMock

    (tmp_path / "docs" / "spec").mkdir(parents=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))

    spec_file = tmp_path / "docs" / "spec" / "build-claim-spec.md"
    spec_file.write_text(
        "---\ntitle: Build Claim Spec\nstatus: spec\n---\n\n- [ ] Task A\n"
    )

    # Stub spec_build to return one builder config
    fake_configs = {
        "agents": [
            {
                "name": "build-claim-spec-builder-1",
                "prompt": "Do the thing",
                "task_id": "501",
                "task_title": "Task A",
            }
        ]
    }
    monkeypatch.setattr(
        ostk_module.ostk, "spec_build", AsyncMock(return_value=fake_configs)
    )

    # Stub spawn_agent so no real subprocess runs
    async def _fake_spawn(body, **kwargs):
        return {"name": body.name, "status": "running"}

    monkeypatch.setattr(agents_router, "spawn_agent", _fake_spawn)

    # Clear prior claims
    specs_router._spec_claims.pop("docs/spec/build-claim-spec.md", None)
    specs_router._task_assignments.pop("501", None)

    resp = await client.post("/api/specs/docs/spec/build-claim-spec.md/build")
    assert resp.status_code == 200, f"Expected 200: {resp.text}"

    # The _spec_claims dict must now have a source=build entry
    claims = specs_router._spec_claims.get("docs/spec/build-claim-spec.md", [])
    assert claims, (
        "_spec_claims must have an entry after Build, got none. "
        "Build endpoint must record a source=build claim."
    )
    build_claim = next((c for c in claims if c.get("source") == "build"), None)
    assert build_claim is not None, (
        f"No source=build claim found in {claims}"
    )
    assert "started_at" in build_claim
    assert "task_ids" in build_claim

    # Cleanup
    specs_router._spec_claims.pop("docs/spec/build-claim-spec.md", None)
    specs_router._task_assignments.pop("501", None)
