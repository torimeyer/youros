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
        # unstarted task -> ready (3c7f9e53: only started tasks trigger in-progress)
        assert OstkService.compute_spec_status(
            "spec", ["1"], {"1": "open"}
        ) == "ready"
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

    (tmp_path / "docs" / "spec").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)

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

    (tmp_path / "docs" / "spec").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))
    monkeypatch.setattr("config.PROJECT_ROOT", tmp_path)

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


# ---------------------------------------------------------------------------
# Integration test: GET /api/specs must honour _spec_claims (FR-012 / →1662)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_specs_active_claim_overrides_ready_status(client, monkeypatch):
    """RED (→1662): GET /api/specs must show 'in-progress' when a claim is active.

    list_docs() calls compute_spec_status without claims, so a spec with
    an active terminal-agent claim but no tasks returns 'ready'. After the
    fix, list_specs re-applies compute_spec_status with _spec_claims and
    the status becomes 'in-progress'.
    """
    from services import ostk as ostk_module
    from routers import specs as specs_router
    from unittest.mock import AsyncMock

    spec_path = "docs/spec/claim-override-test.md"

    # Stub list_docs to return a promoted spec with no tasks and status
    # already computed as 'ready' (which is what list_docs returns today,
    # because it ignores _spec_claims when calling compute_spec_status).
    fake_doc = {
        "path": spec_path,
        "title": "Claim Override Test",
        "status": "ready",
        "task_ids": [],
        "task_summary": {"total": 0, "open": 0, "closed": 0},
        "acceptance_criteria": [],
        "promoted_at": "2026-05-01T00:00:00+00:00",
        "created_at": "2026-05-01T00:00:00+00:00",
        "updated_at_ms": 0,
        "stage": "spec",
        "husk": False,
        "missing_files": [],
        "open_linked_needles": [],
        "is_user_local": False,
    }
    monkeypatch.setattr(
        ostk_module.ostk, "list_docs", AsyncMock(return_value=[fake_doc])
    )

    # Inject an active claim for this spec into the router's registry
    specs_router._spec_claims[spec_path] = [
        {
            "agent": "gemini-terminal",
            "source": "agent",
            "started_at": "2026-05-23T10:00:00+00:00",
            "task_ids": [],
        }
    ]

    try:
        resp = await client.get("/api/specs")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        matching = [d for d in data["docs"] if d.get("path") == spec_path]
        assert matching, (
            f"Spec {spec_path!r} not found in response; "
            f"paths={[d.get('path') for d in data['docs']]}"
        )
        doc = matching[0]
        assert doc["status"] == "in-progress", (
            f"Spec with active claim must show 'in-progress', got {doc['status']!r}. "
            "list_specs must re-apply compute_spec_status with _spec_claims."
        )
    finally:
        specs_router._spec_claims.pop(spec_path, None)


# ---------------------------------------------------------------------------
# →2148: spec status must reflect shipped work, not stale open tasks
# ---------------------------------------------------------------------------

class TestComputeSpecStatusShippedWork:
    """When all acceptance criteria are checked, the spec is complete even if
    the linked tasks were never formally closed (stale open tasks)."""

    def setup_method(self):
        from services.ostk import OstkService
        self.compute = OstkService.compute_spec_status

    def _stale_task_statuses(self, ids):
        """All listed tasks remain in 'open' state — never closed."""
        return {tid: "open" for tid in ids}

    def test_building_all_acs_checked_stale_open_tasks_is_complete(self):
        """Executive Summary scenario: base_status='building', 20 open tasks,
        all ACs checked. Must show 'complete', not 'in-progress'."""
        task_ids = [str(i) for i in range(1, 21)]
        statuses = self._stale_task_statuses(task_ids)
        result = self.compute(
            "building", task_ids, statuses, ac_all_met=True, claims=[]
        )
        assert result == "complete", (
            f"Spec with all ACs checked and stale open tasks must be 'complete', got {result!r}. "
            "Stale open tasks must not override explicit AC verification."
        )

    def test_spec_all_acs_checked_open_tasks_is_complete(self):
        """Same scenario with base_status='spec' (promoted, not yet building)."""
        task_ids = ["t1", "t2", "t3"]
        statuses = self._stale_task_statuses(task_ids)
        result = self.compute(
            "spec", task_ids, statuses, ac_all_met=True, claims=[]
        )
        assert result == "complete", (
            f"Promoted spec with all ACs checked must be 'complete', got {result!r}."
        )

    def test_building_all_acs_checked_but_active_claim_stays_in_progress(self):
        """If an agent is still actively working (active claim), do not prematurely
        declare complete even when all ACs are checked."""
        task_ids = ["t1", "t2"]
        statuses = self._stale_task_statuses(task_ids)
        claim = {
            "agent": "build-agent",
            "task_ids": ["t1"],  # t1 is open → claim is active
        }
        result = self.compute(
            "building", task_ids, statuses, ac_all_met=True, claims=[claim]
        )
        assert result == "in-progress", (
            f"Spec with active claim must stay 'in-progress' even when all ACs checked, got {result!r}."
        )

    def test_building_acs_not_all_checked_open_tasks_stays_in_progress(self):
        """No regression: if ACs are NOT all checked and tasks are open, still in-progress."""
        task_ids = ["t1", "t2"]
        statuses = self._stale_task_statuses(task_ids)
        result = self.compute(
            "building", task_ids, statuses, ac_all_met=False, claims=[]
        )
        assert result == "in-progress", (
            f"Spec with unchecked ACs and open tasks must be 'in-progress', got {result!r}."
        )

    def test_ac_all_met_false_does_not_flip_to_complete_for_spec(self):
        """No regression: partial ACs + open tasks → not complete."""
        task_ids = ["t1", "t2"]
        statuses = self._stale_task_statuses(task_ids)
        result = self.compute(
            "spec", task_ids, statuses, ac_all_met=False, claims=[]
        )
        assert result != "complete", (
            f"Spec with unchecked ACs must not be 'complete', got {result!r}."
        )
