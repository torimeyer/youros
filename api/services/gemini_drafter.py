"""Gemini Enterprise drafter.

Thin wrapper around google.generativeai that uses google.auth.default()
credentials (Vertex AI / enterprise). Raises RuntimeError if credentials
are not available. No fallback to any other provider.
"""

from __future__ import annotations

import asyncio


async def draft(prompt: str, system: str = "") -> str:
    """Call Gemini Enterprise and return the response text.

    Raises RuntimeError("Gemini Enterprise not connected") if credentials
    are missing or invalid. Never falls back to Anthropic or any other model.
    """
    try:
        import google.auth
        import google.auth.transport.requests
        import google.generativeai as genai
    except ImportError as exc:
        raise RuntimeError("Gemini Enterprise not connected") from exc

    try:
        credentials, _project = google.auth.default(
            scopes=[
                "https://www.googleapis.com/auth/cloud-platform",
                "https://www.googleapis.com/auth/generative-language",
            ]
        )
        request = google.auth.transport.requests.Request()
        credentials.refresh(request)
    except Exception as exc:
        raise RuntimeError("Gemini Enterprise not connected") from exc

    genai.configure(credentials=credentials)

    model = genai.GenerativeModel(
        model_name="gemini-1.5-pro",
        system_instruction=system if system else None,
    )

    def _call() -> str:
        response = model.generate_content(prompt)
        return response.text or ""

    return await asyncio.get_event_loop().run_in_executor(None, _call)
