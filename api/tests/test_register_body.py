"""Regression tests for POST /api/agents/register response body.

Context: on 2026-04-20 a subagent reported "empty body" from the register
endpoint. Diagnosis showed the endpoint actually returns a full JSON body
(status, result, mailbox_instruction, mailbox_check_interval_seconds).
The "empty body" symptom was an artifact of the caller's curl timeout on
the unrelated GET /api/agents endpoint, plus a 400 from the probe being
misread as an empty 2xx.

These tests pin the response shape so any future change that strips the
body, swaps to StreamingResponse without flush, or otherwise drops bytes
fails loudly instead of leaving subagents invisible in the Agents page.
"""
import pytest


@pytest.mark.asyncio
async def test_register_endpoint_returns_body_with_required_keys(client, monkeypatch):
    """A minimal register POST returns a non-empty JSON body with the
    full mailbox contract the caller needs to self-bootstrap polling.
    """
    from routers import agents as agents_router
    monkeypatch.setattr(agents_router, "_save_agent_state", lambda: None)

    payload = {
        "name": "test_register_endpoint_returns_body_probe",
        "prompt": "regression probe for register endpoint body",
    }
    resp = await client.post("/api/agents/register", json=payload)

    assert resp.status_code == 200, resp.text
    # The core empty-body regression guard. If a future change (e.g. a
    # StreamingResponse without flush, a middleware that strips
    # response.body, a background task that raises after sending
    # headers) drops the body, content-length will be 0 and this
    # assert catches it.
    assert int(resp.headers.get("content-length", "0")) > 0, (
        "register response had empty body; subagents would be invisible"
    )
    body = resp.json()
    assert body, "register response body decoded to falsy JSON"
    # Required keys the caller relies on to self-register and start
    # polling the mailbox. Missing any of these breaks the subagent
    # bootstrap path documented in agent_mailbox_instruction().
    for key in (
        "result",
        "source",
        "status",
        "mailbox_instruction",
        "mailbox_check_interval_seconds",
    ):
        assert key in body, f"register body missing {key!r}: {body}"
    # Sanity: the mailbox instruction should mention the agent by name
    # so subagents copy-paste the right curl, not a stale one.
    assert "test_register_endpoint_returns_body_probe" in body["mailbox_instruction"]
    assert body["status"] == "running"
    assert body["source"] == "claude-code"


@pytest.mark.asyncio
async def test_register_endpoint_returns_body_when_only_name_supplied(client, monkeypatch):
    """Even the absolute minimal payload (just ``name``) returns a
    non-empty body. Protects against a future validator tightening
    that accidentally 500s instead of 400ing.
    """
    from routers import agents as agents_router
    monkeypatch.setattr(agents_router, "_save_agent_state", lambda: None)

    resp = await client.post(
        "/api/agents/register",
        json={"name": "test_register_endpoint_returns_body_bare"},
    )

    # On this branch the register handler accepts a bare name. Whichever
    # status the endpoint chooses, the body must not be empty.
    assert int(resp.headers.get("content-length", "0")) > 0, (
        f"register returned status={resp.status_code} with empty body"
    )
    # Must be valid JSON, not a silent 200 with b"" or a truncated
    # chunk from a broken StreamingResponse.
    body = resp.json()
    assert isinstance(body, dict) and body, (
        f"register returned empty/non-dict JSON: {body!r}"
    )
