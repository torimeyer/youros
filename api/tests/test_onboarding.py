from unittest.mock import MagicMock, patch

import pytest


# --- POST /api/onboarding/intent ---

@pytest.mark.asyncio
async def test_intent_writing_returns_writing_starter_pack(client):
    """Writing intent returns writer agent templates."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "writing"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-writer-blog-post" in ids
    assert "builtin-writer-proofreader" in ids
    # Default-selected items are correctly flagged
    defaults = [item for item in pack if item["default_selected"]]
    assert len(defaults) > 0
    # Each item has the required fields
    for item in pack:
        assert item["kind"] == "agent"
        assert item["name"]
        assert item["description"]


@pytest.mark.asyncio
async def test_intent_coding_returns_coding_starter_pack(client):
    """Coding intent returns engineering agent templates."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "coding"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    ids = [item["id"] for item in pack]
    assert "builtin-builder" in ids
    assert "builtin-diagnose" in ids
    assert "builtin-eng-write-tests" in ids
    # Builder and Diagnose should be default-selected
    by_id = {item["id"]: item for item in pack}
    assert by_id["builtin-builder"]["default_selected"] is True
    assert by_id["builtin-diagnose"]["default_selected"] is True


@pytest.mark.asyncio
async def test_intent_work_role_includes_role_in_request(client):
    """work_role intent with a role string still returns a valid pack."""
    resp = await client.post("/api/onboarding/intent", json={
        "intent": "work_role",
        "role": "Product manager",
    })
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-pm-prd" in ids


@pytest.mark.asyncio
async def test_intent_sales_returns_sales_starter_pack(client):
    """Sales intent returns sales agent templates, not PM templates."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "sales"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-sales-prospect-research" in ids
    assert "builtin-sales-cold-outreach" in ids
    assert "builtin-sales-call-prep" in ids
    by_id = {item["id"]: item for item in pack}
    assert by_id["builtin-sales-prospect-research"]["default_selected"] is True
    assert by_id["builtin-sales-cold-outreach"]["default_selected"] is True
    assert "builtin-pm-prd" not in ids, "Sales intent must not include PM templates"


@pytest.mark.asyncio
async def test_intent_general_returns_universal_starter_pack(client):
    """General intent returns universal templates, not PM templates."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "general"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-builder" in ids
    assert "builtin-research" in ids
    assert "builtin-brainstorm" in ids
    assert "builtin-explain-plain" in ids
    by_id = {item["id"]: item for item in pack}
    assert by_id["builtin-builder"]["default_selected"] is True
    assert "builtin-pm-prd" not in ids, "General intent must not include PM templates"


@pytest.mark.asyncio
async def test_intent_invalid_returns_422(client):
    """An unrecognised intent value should be rejected by Pydantic validation."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "gaming"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_intent_marketing_returns_campaign_brief(client):
    """Marketing intent returns Campaign Brief as the primary agent."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "marketing"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-marketing-campaign-brief" in ids
    by_id = {item["id"]: item for item in pack}
    assert by_id["builtin-marketing-campaign-brief"]["default_selected"] is True


@pytest.mark.asyncio
async def test_intent_founder_returns_investor_update(client):
    """Founder intent returns Investor Update as the primary agent."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "founder"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-founder-investor-update" in ids
    by_id = {item["id"]: item for item in pack}
    assert by_id["builtin-founder-investor-update"]["default_selected"] is True


@pytest.mark.asyncio
async def test_intent_support_returns_customer_reply(client):
    """Support intent returns Customer Reply as the primary agent."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "support"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-support-customer-reply" in ids
    by_id = {item["id"]: item for item in pack}
    assert by_id["builtin-support-customer-reply"]["default_selected"] is True


@pytest.mark.asyncio
async def test_intent_designer_returns_design_critique(client):
    """Designer intent returns Design Critique as the primary agent."""
    resp = await client.post("/api/onboarding/intent", json={"intent": "designer"})
    assert resp.status_code == 200
    pack = resp.json()["starter_pack"]
    assert len(pack) > 0
    ids = [item["id"] for item in pack]
    assert "builtin-designer-design-critique" in ids
    by_id = {item["id"]: item for item in pack}
    assert by_id["builtin-designer-design-critique"]["default_selected"] is True


@pytest.mark.asyncio
async def test_intent_all_packs_resolve_to_non_empty_list(client):
    """Every intent in the mapping resolves to at least one valid template."""
    all_intents = [
        "writing", "personal", "coding", "research", "work_role",
        "sales", "general", "marketing", "founder", "support", "designer",
    ]
    for intent in all_intents:
        resp = await client.post("/api/onboarding/intent", json={"intent": intent})
        assert resp.status_code == 200, f"intent={intent} returned {resp.status_code}"
        pack = resp.json()["starter_pack"]
        assert len(pack) > 0, f"intent={intent} returned empty starter_pack"


@pytest.mark.asyncio
async def test_first_runs_writing_returns_three_hints(client):
    resp = await client.get("/api/onboarding/first-runs?intent=writing")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["hints"]) == 3
    for h in data["hints"]:
        assert "label" in h
        assert "seed" in h
        assert h["kind"] in ("chat", "task")


@pytest.mark.asyncio
async def test_first_runs_all_intents_return_three_hints(client):
    for intent in ["writing", "personal", "coding", "research", "work_role", "sales", "general"]:
        resp = await client.get(f"/api/onboarding/first-runs?intent={intent}")
        assert resp.status_code == 200
        hints = resp.json()["hints"]
        assert len(hints) == 3, f"{intent} returned {len(hints)} hints"


@pytest.mark.asyncio
async def test_first_runs_unknown_intent_defaults_to_writing(client):
    resp = await client.get("/api/onboarding/first-runs?intent=unknown_xyz")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["hints"]) == 3


@pytest.mark.asyncio
async def test_first_runs_default_intent_is_writing(client):
    resp = await client.get("/api/onboarding/first-runs")
    assert resp.status_code == 200
    hints = resp.json()["hints"]
    labels = [h["label"] for h in hints]
    assert any("blog" in lbl.lower() or "draft" in lbl.lower() or "headline" in lbl.lower() for lbl in labels)


@pytest.mark.asyncio
async def test_enable_myos_hooks_success(client):
    """enable-myos-hooks runs myos-track.sh and returns enabled:True on success."""
    with patch("routers.onboarding.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        resp = await client.post("/api/onboarding/enable-myos-hooks")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["method"] == "track"


@pytest.mark.asyncio
async def test_enable_myos_hooks_scope_everywhere(client):
    """scope=everywhere passes --global to myos-track.sh."""
    with patch("routers.onboarding.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        resp = await client.post("/api/onboarding/enable-myos-hooks", json={"scope": "everywhere"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["method"] == "everywhere"
    args = mock_run.call_args[0][0]
    assert "--global" in args


@pytest.mark.asyncio
async def test_enable_myos_hooks_scope_repo_with_path(client):
    """scope=repo passes the provided path to myos-track.sh."""
    with patch("routers.onboarding.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        resp = await client.post(
            "/api/onboarding/enable-myos-hooks",
            json={"scope": "repo", "path": "/home/user/myproject"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["method"] == "repo"
    args = mock_run.call_args[0][0]
    assert "/home/user/myproject" in args


@pytest.mark.asyncio
async def test_enable_myos_hooks_scope_repo_missing_path(client):
    """scope=repo without path returns enabled:False."""
    resp = await client.post("/api/onboarding/enable-myos-hooks", json={"scope": "repo"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert "path" in data["error"]


@pytest.mark.asyncio
async def test_enable_myos_hooks_scope_myos_only(client):
    """scope=myos-only returns success without calling myos-track.sh."""
    with patch("routers.onboarding.subprocess.run") as mock_run:
        resp = await client.post("/api/onboarding/enable-myos-hooks", json={"scope": "myos-only"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["method"] == "myos-only"
    mock_run.assert_not_called()

