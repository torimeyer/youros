"""Gmail reply service.

Send plain-text replies in existing Gmail threads, and ask Claude to draft
a plain-text reply for the current message. Kept in a dedicated module so
it does not collide with the read-side caching logic in services.gmail.

All user-facing error text here is plain language and never contains
em-dashes, per the project writing-style rules.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from pathlib import Path
from typing import Any, Optional

import anthropic

from services.google_auth import TOKEN_PATH, get_credentials, get_email
from services.settings_store import settings_store


GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"

# Same Claude model the chat and idea breakdown pathways use.
_DRAFT_MODEL = "claude-sonnet-4-20250514"

# Cap the drafting prompt so a giant original email cannot blow the context.
_MAX_ORIGINAL_CHARS = 6000


# ---------------------------------------------------------------------------
# Scope helpers
# ---------------------------------------------------------------------------


def has_send_scope() -> bool:
    """Return True when the stored Google token grants Gmail send access.

    The token file contains a ``scope`` field (a space-separated string of
    the scopes Google actually granted). If the file is missing or the
    field is missing we assume no send access so the frontend shows a
    reconnect CTA. Never raises.
    """
    if not TOKEN_PATH.exists():
        return False
    try:
        tokens = json.loads(TOKEN_PATH.read_text())
    except Exception:
        return False
    scope_str = tokens.get("scope", "") or ""
    return GMAIL_SEND_SCOPE in scope_str


# ---------------------------------------------------------------------------
# Gmail client
# ---------------------------------------------------------------------------


def _build_gmail_service():
    """Build an authenticated Gmail API service object.

    Kept local to this module so it does not collide with edits the perf
    agent is making in services.gmail.
    """
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise RuntimeError(
            "Google API client is not available on this server."
        ) from exc

    tokens = get_credentials()
    creds = Credentials(
        token=tokens.get("access_token"),
        refresh_token=tokens.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=None,
        client_secret=None,
        scopes=(tokens.get("scope") or "").split() or None,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Header parsing helpers
# ---------------------------------------------------------------------------


def _headers_map(message: dict) -> dict[str, str]:
    """Return a lowercased-key dict of the message's top-level headers."""
    headers = message.get("payload", {}).get("headers", []) or []
    out: dict[str, str] = {}
    for h in headers:
        name = (h.get("name") or "").lower()
        if name and name not in out:
            out[name] = h.get("value", "") or ""
    return out


def _ensure_re_prefix(subject: str) -> str:
    """Prefix the subject with ``Re: `` unless it already starts with one."""
    clean = (subject or "").strip()
    if not clean:
        return "Re:"
    # Treat Re:, RE:, re:, Re :, and nested patterns as already prefixed.
    if re.match(r"^\s*re\s*:\s*", clean, flags=re.IGNORECASE):
        return clean
    return f"Re: {clean}"


def _normalize_addr(addr: str) -> str:
    """Lowercase the email portion of an address for comparison."""
    _, email = parseaddr(addr or "")
    return (email or "").strip().lower()


def _split_addr_list(value: str) -> list[str]:
    """Split a header value into a list of full ``Name <email>`` strings."""
    if not value:
        return []
    pairs = getaddresses([value])
    out: list[str] = []
    for name, email in pairs:
        if not email:
            continue
        if name:
            out.append(f"{name} <{email}>")
        else:
            out.append(email)
    return out


def _pick_recipients(
    *,
    headers: dict[str, str],
    self_email: str,
    reply_all: bool,
) -> tuple[list[str], list[str]]:
    """Return (to_list, cc_list) for the outgoing reply.

    - ``reply_all=False``: reply only to the original sender.
    - ``reply_all=True``: To = original sender plus everyone on the
      original To, Cc = original Cc, with the current user removed from
      both.

    The original sender is always present in ``to_list`` exactly once.
    """
    reply_to_value = headers.get("reply-to") or headers.get("from", "")
    _, sender_email = parseaddr(reply_to_value)
    sender_full = reply_to_value if sender_email else ""

    if not reply_all:
        return ([sender_full] if sender_full else []), []

    self_norm = _normalize_addr(self_email)
    sender_norm = _normalize_addr(sender_full)

    to_list: list[str] = [sender_full] if sender_full else []
    seen = {self_norm, sender_norm} if sender_norm else {self_norm}

    for entry in _split_addr_list(headers.get("to", "")):
        norm = _normalize_addr(entry)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        to_list.append(entry)

    cc_list: list[str] = []
    for entry in _split_addr_list(headers.get("cc", "")):
        norm = _normalize_addr(entry)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        cc_list.append(entry)

    return to_list, cc_list


def _build_references(headers: dict[str, str]) -> str:
    """Build the outgoing References header value.

    Combines the existing References header with the original Message-ID
    so any mail client can thread the reply, not just Gmail.
    """
    prior = (headers.get("references") or "").strip()
    msg_id = (headers.get("message-id") or "").strip()
    if prior and msg_id:
        return f"{prior} {msg_id}"
    return prior or msg_id


def _build_mime(
    *,
    to_list: list[str],
    cc_list: list[str],
    from_addr: str,
    subject: str,
    in_reply_to: str,
    references: str,
    body: str,
) -> EmailMessage:
    """Construct the outgoing plain-text reply as an EmailMessage."""
    msg = EmailMessage()
    if to_list:
        msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    if from_addr:
        msg["From"] = from_addr
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body or "")
    return msg


def _encode_raw(msg: EmailMessage) -> str:
    """Return the base64url-encoded raw bytes Gmail expects."""
    return base64.urlsafe_b64encode(bytes(msg)).decode("ascii")


# ---------------------------------------------------------------------------
# Original message body extraction (used for the drafting prompt)
# ---------------------------------------------------------------------------


def _decode_body_data(data: str) -> str:
    """Decode a Gmail body data field (base64url) to a text string."""
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data.encode("ascii") + b"==").decode(
            "utf-8", errors="replace"
        )
    except Exception:
        return ""


def _extract_text_body(payload: dict) -> str:
    """Walk a Gmail payload and return the plain-text body.

    Prefers ``text/plain`` parts. Falls back to stripping tags from an
    ``text/html`` part if plain text is not available. Never raises.
    """
    if not payload:
        return ""

    mime_type = payload.get("mimeType", "") or ""

    body = payload.get("body") or {}
    data = body.get("data") or ""

    if mime_type == "text/plain" and data:
        return _decode_body_data(data).strip()

    parts = payload.get("parts") or []

    # Breadth-first search: prefer plain text at any depth.
    queue: list[dict] = list(parts)
    html_fallback = ""

    while queue:
        part = queue.pop(0)
        part_type = (part.get("mimeType") or "").lower()
        part_body = part.get("body") or {}
        part_data = part_body.get("data") or ""

        if part_type == "text/plain" and part_data:
            return _decode_body_data(part_data).strip()

        if part_type == "text/html" and part_data and not html_fallback:
            html_fallback = _decode_body_data(part_data)

        sub = part.get("parts") or []
        if sub:
            queue.extend(sub)

    # Top-level HTML fallback if the root is html.
    if mime_type == "text/html" and data and not html_fallback:
        html_fallback = _decode_body_data(data)

    if html_fallback:
        # Very light tag stripper. Good enough to seed a draft.
        text = re.sub(r"<[^>]+>", " ", html_fallback)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    return ""


# ---------------------------------------------------------------------------
# send_reply
# ---------------------------------------------------------------------------


def _run_in_executor(fn):
    """Run a sync function in the default executor."""
    return asyncio.get_event_loop().run_in_executor(None, fn)


def _fetch_message_sync(service, message_id: str) -> dict:
    return (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )


def _send_message_sync(service, raw: str, thread_id: str) -> dict:
    return (
        service.users()
        .messages()
        .send(
            userId="me",
            body={"raw": raw, "threadId": thread_id},
        )
        .execute()
    )


def _is_scope_error(exc: BaseException) -> bool:
    """Return True when the exception looks like a missing-scope error."""
    text = str(exc).lower()
    if "insufficientpermissions" in text or "insufficient authentication scopes" in text:
        return True
    # googleapiclient HttpError exposes resp.status.
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    if status in (401, 403):
        if "insufficient" in text or "scope" in text or "gmail.send" in text:
            return True
    return False


def _friendly_send_error(exc: BaseException) -> str:
    """Return a plain-language error message for send failures."""
    text = str(exc)
    # Keep it simple. No raw JSON, no em-dashes, no stack frames.
    if not text:
        return "Gmail could not send the reply right now."
    # Truncate very long errors.
    if len(text) > 200:
        text = text[:200] + "..."
    # Replace any stray em-dashes just in case a downstream error message
    # contains one.
    text = text.replace("\u2014", ", ").replace("--", ", ")
    return f"Gmail could not send the reply. {text}"


async def send_reply(
    *,
    thread_id: str,
    in_reply_to_message_id: str,
    body: str,
    reply_all: bool = False,
) -> dict:
    """Send a plain-text reply in an existing Gmail thread.

    Returns ``{'ok': True, 'message_id': <id>}`` on success or
    ``{'ok': False, 'error': <plain language>, 'needs_reauth': bool}`` on
    failure. Never raises for expected scope or API errors.
    """
    if not has_send_scope():
        return {
            "ok": False,
            "needs_reauth": True,
            "error": (
                "Gmail send access has not been granted yet. "
                "Reconnect your Google account to enable sending replies."
            ),
        }

    try:
        service = _build_gmail_service()
    except Exception as exc:
        return {
            "ok": False,
            "needs_reauth": False,
            "error": _friendly_send_error(exc),
        }

    # Fetch the original message so we can copy its threading headers.
    try:
        original = await _run_in_executor(
            lambda: _fetch_message_sync(service, in_reply_to_message_id)
        )
    except Exception as exc:
        if _is_scope_error(exc):
            return {
                "ok": False,
                "needs_reauth": True,
                "error": (
                    "Gmail send access has not been granted yet. "
                    "Reconnect your Google account to enable sending replies."
                ),
            }
        return {
            "ok": False,
            "needs_reauth": False,
            "error": _friendly_send_error(exc),
        }

    headers = _headers_map(original)
    self_email = get_email() or ""

    to_list, cc_list = _pick_recipients(
        headers=headers,
        self_email=self_email,
        reply_all=reply_all,
    )

    subject = _ensure_re_prefix(headers.get("subject", ""))
    in_reply_to = (headers.get("message-id") or "").strip()
    references = _build_references(headers)

    mime_msg = _build_mime(
        to_list=to_list,
        cc_list=cc_list,
        from_addr=self_email,
        subject=subject,
        in_reply_to=in_reply_to,
        references=references,
        body=body or "",
    )
    raw = _encode_raw(mime_msg)

    try:
        sent = await _run_in_executor(
            lambda: _send_message_sync(service, raw, thread_id)
        )
    except Exception as exc:
        if _is_scope_error(exc):
            return {
                "ok": False,
                "needs_reauth": True,
                "error": (
                    "Gmail send access has not been granted yet. "
                    "Reconnect your Google account to enable sending replies."
                ),
            }
        return {
            "ok": False,
            "needs_reauth": False,
            "error": _friendly_send_error(exc),
        }

    # Invalidate the read-side inbox cache so the outgoing reply (and any
    # other new mail) appears on the next fetch without waiting for the
    # 60 s TTL to expire. Lazy import avoids a circular dependency at
    # module load time.
    try:
        from services import gmail as gmail_service

        gmail_service.invalidate_full_inbox_cache()
    except Exception:
        # Cache invalidation is best-effort. A failure here must never
        # turn a successful send into a reported failure.
        pass

    return {"ok": True, "message_id": sent.get("id", "")}


# ---------------------------------------------------------------------------
# draft_reply
# ---------------------------------------------------------------------------


def _load_retry_helpers():
    """Import the Anthropic retry helper and api-key resolver lazily.

    Importing chat_providers at module import time would pull in the
    FastAPI WebSocket stack for every consumer of this module. Lazy
    imports keep the reply service cheap to load and avoid any circular
    import risk with routers that depend on both modules.
    """
    from services.chat_providers import _resolve_api_key  # type: ignore

    try:
        from services.chat_providers import _anthropic_retry_call  # type: ignore
    except ImportError:
        _anthropic_retry_call = None  # type: ignore

    return _resolve_api_key, _anthropic_retry_call


def _build_draft_prompt(
    *,
    user_name: str,
    original_text: str,
    original_sender: str,
    original_subject: str,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the drafting call."""
    name = (user_name or "the user").strip() or "the user"

    system_prompt = (
        f"You are myOS drafting a plain-text reply on behalf of {name}. "
        "Match the original sender's tone. Keep it short. "
        "No subject line, no greeting boilerplate beyond a natural opener. "
        "Never use em-dashes anywhere. "
        "Use plain text only, no markdown, no bullet points, no headings. "
        "Sign off with the sender's first name if a sign off feels natural."
    )

    trimmed = (original_text or "").strip()
    if len(trimmed) > _MAX_ORIGINAL_CHARS:
        trimmed = trimmed[:_MAX_ORIGINAL_CHARS] + "\n...(truncated)"

    user_prompt = (
        "Draft a reply to the email below.\n\n"
        f"From: {original_sender or 'unknown'}\n"
        f"Subject: {original_subject or '(no subject)'}\n"
        "----- original message -----\n"
        f"{trimmed}\n"
        "----- end original message -----\n\n"
        "Return only the reply body as plain text. No headers, no subject line, "
        "no markdown, no em-dashes."
    )

    return system_prompt, user_prompt


def _extract_draft_text(response: Any) -> str:
    """Pull the first text block out of an Anthropic response object."""
    content = getattr(response, "content", None) or []
    for block in content:
        if getattr(block, "type", "") == "text":
            text = getattr(block, "text", "") or ""
            if text:
                return text
    return ""


async def draft_reply(
    *,
    thread_id: str,
    in_reply_to_message_id: str,
) -> dict:
    """Use Anthropic to draft a plain-text reply to the given message.

    Returns ``{'ok': True, 'draft': <text>}`` on success or
    ``{'ok': False, 'error': <plain language>}`` otherwise.
    """
    # Fetch original message text.
    try:
        service = _build_gmail_service()
    except Exception as exc:
        return {"ok": False, "error": _friendly_send_error(exc)}

    try:
        original = await _run_in_executor(
            lambda: _fetch_message_sync(service, in_reply_to_message_id)
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": (
                "Could not load the original email to draft a reply. "
                "Try again in a moment."
            ),
        }

    headers = _headers_map(original)
    original_text = _extract_text_body(original.get("payload") or {})
    if not original_text:
        original_text = (original.get("snippet") or "").strip()

    original_sender = headers.get("from", "")
    original_subject = headers.get("subject", "")

    user_name = str(settings_store.get("user_name", "") or "").strip()
    if not user_name:
        # Fall back to the local-part of the connected email.
        self_email = get_email() or ""
        if self_email:
            user_name = self_email.split("@", 1)[0]

    system_prompt, user_prompt = _build_draft_prompt(
        user_name=user_name,
        original_text=original_text,
        original_sender=original_sender,
        original_subject=original_subject,
    )

    _resolve_api_key, _retry_call = _load_retry_helpers()

    api_key = await _resolve_api_key("anthropic_api_key")
    if not api_key:
        return {
            "ok": False,
            "error": (
                "No Anthropic API key found. "
                "Add one in Settings under AI Provider to use the drafting helper."
            ),
        }

    client = anthropic.AsyncAnthropic(api_key=api_key)

    async def _do_call() -> Any:
        return await client.messages.create(
            model=_DRAFT_MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

    try:
        if _retry_call is not None:
            response = await _retry_call(
                _do_call, op_name="gmail_reply.draft"
            )
        else:
            response = await _do_call()
    except anthropic.APIError as exc:
        return {
            "ok": False,
            "error": (
                "Claude could not draft a reply just now. "
                "Try again in a few seconds."
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": (
                "Something went wrong drafting the reply. "
                "Try again in a moment."
            ),
        }

    draft_text = _extract_draft_text(response)
    # Strip stray em-dashes just in case.
    draft_text = draft_text.replace("\u2014", ", ")

    if not draft_text:
        return {
            "ok": False,
            "error": (
                "Claude returned an empty draft. "
                "Try again, or write the reply yourself."
            ),
        }

    return {"ok": True, "draft": draft_text.strip()}
