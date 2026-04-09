"""Tests for the Gmail reply service and endpoints.

All Google API calls and Anthropic calls are mocked so these tests run
without real credentials or network access. The mocking style matches
api/tests/test_gmail.py and api/tests/test_chat_providers.py.
"""

from __future__ import annotations

import base64
import json
from email import message_from_bytes
from email.policy import default as default_policy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import gmail_reply as gmail_reply_service


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


SELF_EMAIL = "tori@example.com"


def _fake_original(
    *,
    subject: str = "hello",
    from_addr: str = "Alice <alice@example.com>",
    to: str = "tori@example.com, bob@example.com",
    cc: str = "carol@example.com",
    message_id: str = "<orig-abc@mail.example.com>",
    references: str = "",
    reply_to: str = "",
    body_text: str = "Hey, quick question about the project.",
    mime_type: str = "text/plain",
) -> dict:
    """Build a fake Gmail messages.get response."""
    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": from_addr},
        {"name": "To", "value": to},
        {"name": "Cc", "value": cc},
        {"name": "Message-ID", "value": message_id},
    ]
    if references:
        headers.append({"name": "References", "value": references})
    if reply_to:
        headers.append({"name": "Reply-To", "value": reply_to})

    encoded_body = base64.urlsafe_b64encode(body_text.encode("utf-8")).decode("ascii")
    payload = {
        "mimeType": mime_type,
        "headers": headers,
        "body": {"data": encoded_body},
    }
    return {
        "id": "orig-1",
        "threadId": "thread-1",
        "snippet": body_text[:100],
        "payload": payload,
    }


def _build_mock_service(*, original: dict, sent_id: str = "sent-1"):
    """Build a MagicMock Google service object.

    Returns (service, send_spy) where send_spy lets tests inspect what
    messages().send() was called with.
    """
    service = MagicMock()

    # get
    get_exec = MagicMock()
    get_exec.execute = MagicMock(return_value=original)
    service.users.return_value.messages.return_value.get.return_value = get_exec

    # send
    send_exec = MagicMock()
    send_exec.execute = MagicMock(return_value={"id": sent_id, "threadId": original.get("threadId", "thread-1")})
    send_call = service.users.return_value.messages.return_value.send
    send_call.return_value = send_exec

    return service, send_call


def _decode_sent_mime(send_call) -> tuple[dict, "object"]:
    """Return (send_kwargs, parsed_email_message) from a send() spy.

    ``send_kwargs`` is the kwargs dict passed to ``.send(...)``.
    ``parsed_email_message`` is an email.message.Message built from the
    base64url-decoded raw bytes.
    """
    # The first positional/keyword argument is what the service was
    # called with. We passed keyword-only: .send(userId=..., body=...).
    assert send_call.call_count == 1
    _, kwargs = send_call.call_args
    body = kwargs["body"]
    raw_b64 = body["raw"]
    raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("ascii") + b"==")
    parsed = message_from_bytes(raw_bytes, policy=default_policy)
    return kwargs, parsed


# ---------------------------------------------------------------------------
# 1. send_reply threads correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reply_threads_correctly():
    """Outgoing MIME must carry In-Reply-To, References, Re: Subject, To,
    and the Gmail send call must pass threadId so Gmail stitches it."""
    original = _fake_original(
        subject="hello",
        from_addr="Alice <alice@example.com>",
        to="tori@example.com",
        cc="",
        message_id="<orig-abc@mail.example.com>",
        references="<root-1@mail.example.com> <root-2@mail.example.com>",
    )
    service, send_call = _build_mock_service(original=original)

    with (
        patch("services.gmail_reply.has_send_scope", return_value=True),
        patch("services.gmail_reply._build_gmail_service", return_value=service),
        patch("services.gmail_reply.get_email", return_value=SELF_EMAIL),
    ):
        result = await gmail_reply_service.send_reply(
            thread_id="thread-1",
            in_reply_to_message_id="orig-1",
            body="Sure, sounds good.",
            reply_all=False,
        )

    assert result["ok"] is True
    assert result["message_id"] == "sent-1"

    kwargs, parsed = _decode_sent_mime(send_call)
    assert kwargs["userId"] == "me"
    assert kwargs["body"]["threadId"] == "thread-1"

    assert parsed["Subject"] == "Re: hello"
    assert parsed["In-Reply-To"] == "<orig-abc@mail.example.com>"
    refs = parsed["References"]
    assert "<root-1@mail.example.com>" in refs
    assert "<root-2@mail.example.com>" in refs
    assert "<orig-abc@mail.example.com>" in refs

    assert "alice@example.com" in (parsed["To"] or "")
    # Not reply_all: no cc field.
    assert not parsed["Cc"]
    # Body is plain text.
    assert parsed.get_content().strip() == "Sure, sounds good."


# ---------------------------------------------------------------------------
# 2. reply_all=True keeps everyone except the current user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reply_reply_all_includes_original_recipients():
    original = _fake_original(
        subject="team sync",
        from_addr="Alice <alice@example.com>",
        to="tori@example.com, bob@example.com, dave@example.com",
        cc="carol@example.com, erin@example.com",
        message_id="<orig-xyz@mail.example.com>",
    )
    service, send_call = _build_mock_service(original=original)

    with (
        patch("services.gmail_reply.has_send_scope", return_value=True),
        patch("services.gmail_reply._build_gmail_service", return_value=service),
        patch("services.gmail_reply.get_email", return_value=SELF_EMAIL),
    ):
        result = await gmail_reply_service.send_reply(
            thread_id="thread-1",
            in_reply_to_message_id="orig-1",
            body="Here is my update.",
            reply_all=True,
        )

    assert result["ok"] is True
    _, parsed = _decode_sent_mime(send_call)

    to_field = parsed["To"] or ""
    cc_field = parsed["Cc"] or ""

    # Original sender is in To.
    assert "alice@example.com" in to_field
    # Original To recipients (except self) are in To.
    assert "bob@example.com" in to_field
    assert "dave@example.com" in to_field
    # Original Cc recipients are still in Cc.
    assert "carol@example.com" in cc_field
    assert "erin@example.com" in cc_field
    # Current user is never in either list.
    assert "tori@example.com" not in to_field
    assert "tori@example.com" not in cc_field


# ---------------------------------------------------------------------------
# 3. reply_all=False sends only to the original sender
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reply_reply_all_false_only_sender():
    original = _fake_original(
        subject="team sync",
        from_addr="Alice <alice@example.com>",
        to="tori@example.com, bob@example.com, dave@example.com",
        cc="carol@example.com, erin@example.com",
        message_id="<orig-xyz@mail.example.com>",
    )
    service, send_call = _build_mock_service(original=original)

    with (
        patch("services.gmail_reply.has_send_scope", return_value=True),
        patch("services.gmail_reply._build_gmail_service", return_value=service),
        patch("services.gmail_reply.get_email", return_value=SELF_EMAIL),
    ):
        result = await gmail_reply_service.send_reply(
            thread_id="thread-1",
            in_reply_to_message_id="orig-1",
            body="Just to you.",
            reply_all=False,
        )

    assert result["ok"] is True
    _, parsed = _decode_sent_mime(send_call)

    to_field = parsed["To"] or ""
    assert "alice@example.com" in to_field
    assert "bob@example.com" not in to_field
    assert "dave@example.com" not in to_field
    # No Cc on a non reply-all.
    assert not parsed["Cc"]


# ---------------------------------------------------------------------------
# 4. No send scope returns needs_reauth and does NOT call Gmail send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reply_no_send_scope_returns_needs_reauth():
    service = MagicMock()
    send_call = service.users.return_value.messages.return_value.send

    with (
        patch("services.gmail_reply.has_send_scope", return_value=False),
        patch("services.gmail_reply._build_gmail_service", return_value=service),
        patch("services.gmail_reply.get_email", return_value=SELF_EMAIL),
    ):
        result = await gmail_reply_service.send_reply(
            thread_id="thread-1",
            in_reply_to_message_id="orig-1",
            body="whatever",
            reply_all=False,
        )

    assert result["ok"] is False
    assert result["needs_reauth"] is True
    # messages().send() must never have been called.
    assert send_call.call_count == 0


# ---------------------------------------------------------------------------
# 5. Do not double-prefix "Re: Re: ..."
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reply_strips_redundant_re_prefix():
    original = _fake_original(
        subject="Re: hello",
        from_addr="Alice <alice@example.com>",
        to="tori@example.com",
        cc="",
    )
    service, send_call = _build_mock_service(original=original)

    with (
        patch("services.gmail_reply.has_send_scope", return_value=True),
        patch("services.gmail_reply._build_gmail_service", return_value=service),
        patch("services.gmail_reply.get_email", return_value=SELF_EMAIL),
    ):
        result = await gmail_reply_service.send_reply(
            thread_id="thread-1",
            in_reply_to_message_id="orig-1",
            body="sure",
            reply_all=False,
        )

    assert result["ok"] is True
    _, parsed = _decode_sent_mime(send_call)
    assert parsed["Subject"] == "Re: hello"
    assert parsed["Subject"] != "Re: Re: hello"


# ---------------------------------------------------------------------------
# 6. draft_reply sends the right prompt to Anthropic
# ---------------------------------------------------------------------------


def _make_claude_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def _make_mock_claude_client(text: str):
    create = AsyncMock(return_value=_make_claude_response(text))
    messages = MagicMock()
    messages.create = create
    client = MagicMock()
    client.messages = messages
    return client, create


@pytest.mark.asyncio
async def test_draft_reply_calls_anthropic_with_original_text():
    original_body = "Can you send the Q2 numbers by Friday?"
    original = _fake_original(
        subject="Q2 numbers",
        from_addr="Alice <alice@example.com>",
        to="tori@example.com",
        cc="",
        body_text=original_body,
    )
    service, _ = _build_mock_service(original=original)

    client, create = _make_mock_claude_client("Yes, I'll send them Thursday.")

    with (
        patch("services.gmail_reply._build_gmail_service", return_value=service),
        patch("services.gmail_reply.get_email", return_value=SELF_EMAIL),
        patch("services.gmail_reply.settings_store.get", return_value="Tori"),
        patch(
            "services.gmail_reply._load_retry_helpers",
            return_value=(AsyncMock(return_value="sk-test"), None),
        ),
        patch("services.gmail_reply.anthropic.AsyncAnthropic", return_value=client),
    ):
        result = await gmail_reply_service.draft_reply(
            thread_id="thread-1",
            in_reply_to_message_id="orig-1",
        )

    assert result["ok"] is True
    assert create.call_count == 1
    call_kwargs = create.call_args.kwargs
    system = call_kwargs["system"]
    user = call_kwargs["messages"][0]["content"]

    # System prompt mentions plain text and forbids em-dashes.
    assert "plain text" in system.lower()
    assert "em-dash" in system.lower() or "em dash" in system.lower() or "em-dashes" in system.lower()
    # User name makes it into the system prompt.
    assert "Tori" in system
    # User message contains the original body text so Claude can respond to it.
    assert original_body in user


# ---------------------------------------------------------------------------
# 7. draft_reply returns the model's text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_reply_returns_text():
    original = _fake_original(body_text="Are we still on for tomorrow?")
    service, _ = _build_mock_service(original=original)

    expected = "Yes, still on. See you at 10."
    client, _ = _make_mock_claude_client(expected)

    with (
        patch("services.gmail_reply._build_gmail_service", return_value=service),
        patch("services.gmail_reply.get_email", return_value=SELF_EMAIL),
        patch("services.gmail_reply.settings_store.get", return_value="Tori"),
        patch(
            "services.gmail_reply._load_retry_helpers",
            return_value=(AsyncMock(return_value="sk-test"), None),
        ),
        patch("services.gmail_reply.anthropic.AsyncAnthropic", return_value=client),
    ):
        result = await gmail_reply_service.draft_reply(
            thread_id="thread-1",
            in_reply_to_message_id="orig-1",
        )

    assert result["ok"] is True
    assert result["draft"] == expected


# ---------------------------------------------------------------------------
# 8. send_capability reports has_send_scope based on stored scopes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_capability_reports_scope_state(tmp_path):
    token_path = tmp_path / "google_token.json"

    # Case 1: scope present
    token_path.write_text(
        json.dumps({
            "access_token": "ya29.test",
            "scope": (
                "https://www.googleapis.com/auth/gmail.readonly "
                "https://www.googleapis.com/auth/gmail.send"
            ),
        })
    )
    with patch("services.gmail_reply.TOKEN_PATH", token_path):
        assert gmail_reply_service.has_send_scope() is True

    # Case 2: scope missing
    token_path.write_text(
        json.dumps({
            "access_token": "ya29.test",
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
        })
    )
    with patch("services.gmail_reply.TOKEN_PATH", token_path):
        assert gmail_reply_service.has_send_scope() is False

    # Case 3: token file missing
    token_path.unlink()
    with patch("services.gmail_reply.TOKEN_PATH", token_path):
        assert gmail_reply_service.has_send_scope() is False


# ---------------------------------------------------------------------------
# 9. POST /api/gmail/reply integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_post_reply(client, tmp_path):
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_result = {"ok": True, "message_id": "sent-9"}

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch(
            "services.gmail_reply.send_reply",
            new=AsyncMock(return_value=fake_result),
        ),
    ):
        resp = await client.post(
            "/api/gmail/reply",
            json={
                "thread_id": "thread-1",
                "in_reply_to_message_id": "orig-1",
                "body": "Thanks, looks good.",
                "reply_all": False,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["message_id"] == "sent-9"


@pytest.mark.asyncio
async def test_endpoint_post_reply_not_authenticated(client, tmp_path):
    """Sanity: no token returns 401 without invoking the service."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.post(
            "/api/gmail/reply",
            json={
                "thread_id": "thread-1",
                "in_reply_to_message_id": "orig-1",
                "body": "whatever",
                "reply_all": False,
            },
        )

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 10. POST /api/gmail/draft_reply integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_post_draft_reply(client, tmp_path):
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_result = {"ok": True, "draft": "Sounds good, Friday works."}

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch(
            "services.gmail_reply.draft_reply",
            new=AsyncMock(return_value=fake_result),
        ),
    ):
        resp = await client.post(
            "/api/gmail/draft_reply",
            json={
                "thread_id": "thread-1",
                "in_reply_to_message_id": "orig-1",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["draft"] == "Sounds good, Friday works."


# ---------------------------------------------------------------------------
# 11. send_capability.reauth_url is the real Google consent URL, not the API path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_capability_reauth_url_is_google_oauth_url(client, tmp_path):
    """Regression: clicking Reconnect Gmail must send the browser to
    Google's consent screen, not to a JSON-returning API endpoint.

    Before the fix, /gmail/send_capability returned
    reauth_url = "/api/drive/auth/url/gmail" (the literal API path),
    so the frontend's <a href={reauth_url}> navigated directly to a
    JSON response and Tori saw raw JSON in her browser.
    """
    fake_google_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        "?client_id=209690833139-test.apps.googleusercontent.com"
        "&redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fapi%2Fdrive%2Fauth%2Fcallback"
        "&response_type=code"
        "&scope=openid+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.send"
        "&access_type=offline&prompt=consent&state=abc"
    )

    credentials_path = tmp_path / "google_credentials.json"
    credentials_path.write_text("{}")

    from routers import gmail_reply as gmail_reply_router

    async def fake_drive_auth_url_for_gmail():
        return {"url": fake_google_url}

    with (
        patch("services.gmail_reply.has_send_scope", return_value=False),
        patch.object(gmail_reply_router, "CREDENTIALS_PATH", credentials_path),
        patch(
            "routers.drive.drive_auth_url_for_gmail",
            new=fake_drive_auth_url_for_gmail,
        ),
    ):
        resp = await client.get("/api/gmail/send_capability")

    assert resp.status_code == 200
    data = resp.json()

    assert data["has_send_scope"] is False

    reauth_url = data["reauth_url"]
    assert isinstance(reauth_url, str) and reauth_url, (
        "reauth_url must be a non-empty string when the token cannot send"
    )

    # The CRITICAL assertion: the URL must point at Google, not at the
    # myOS API. Anything starting with /api would render raw JSON when
    # dropped into an <a href>.
    assert reauth_url.startswith("https://accounts.google.com/"), (
        f"reauth_url must be a Google consent URL, got: {reauth_url}"
    )
    assert "client_id=" in reauth_url
    assert "gmail.send" in reauth_url
    # Belt and suspenders: never leak the old API-path value.
    assert reauth_url != "/api/drive/auth/url/gmail"
    assert not reauth_url.startswith("/api/")


@pytest.mark.asyncio
async def test_send_capability_reauth_url_is_clickable_https(client, tmp_path):
    """Universal sanity check: reauth_url, when used as an href, must
    not take the user to a JSON page. Easiest way to prove that is to
    assert it starts with https:// rather than a relative API path."""
    fake_google_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?client_id=x&scope=gmail.send"
    )
    credentials_path = tmp_path / "google_credentials.json"
    credentials_path.write_text("{}")

    from routers import gmail_reply as gmail_reply_router

    async def fake_drive_auth_url_for_gmail():
        return {"url": fake_google_url}

    with (
        patch("services.gmail_reply.has_send_scope", return_value=False),
        patch.object(gmail_reply_router, "CREDENTIALS_PATH", credentials_path),
        patch(
            "routers.drive.drive_auth_url_for_gmail",
            new=fake_drive_auth_url_for_gmail,
        ),
    ):
        resp = await client.get("/api/gmail/send_capability")

    assert resp.status_code == 200
    data = resp.json()
    assert data["reauth_url"].startswith("https://")


@pytest.mark.asyncio
async def test_send_capability_has_scope_returns_null_reauth(client, tmp_path):
    """When the token already has gmail.send, reauth_url is null and we
    never invoke the OAuth URL builder."""
    credentials_path = tmp_path / "google_credentials.json"
    credentials_path.write_text("{}")

    from routers import gmail_reply as gmail_reply_router

    called = {"count": 0}

    async def fake_drive_auth_url_for_gmail():
        called["count"] += 1
        return {"url": "should-not-be-used"}

    with (
        patch("services.gmail_reply.has_send_scope", return_value=True),
        patch.object(gmail_reply_router, "CREDENTIALS_PATH", credentials_path),
        patch(
            "routers.drive.drive_auth_url_for_gmail",
            new=fake_drive_auth_url_for_gmail,
        ),
    ):
        resp = await client.get("/api/gmail/send_capability")

    assert resp.status_code == 200
    data = resp.json()
    assert data["has_send_scope"] is True
    assert data["reauth_url"] is None
    assert called["count"] == 0
