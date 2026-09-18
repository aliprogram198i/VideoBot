"""Optional Gemini service isolated from the download critical path."""

from __future__ import annotations

import asyncio
import logging
import os

LOG = logging.getLogger(__name__)
_TIMEOUT_SECONDS = 30.0


def _load_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception as exc:
        LOG.warning("Gemini initialization failed: %s", type(exc).__name__)
        return None


async def generate(prompt: str) -> str:
    """Generate an optional admin/analytics response.

    This function is intentionally lazy and bounded. Importing AliBot never
    imports or initializes Gemini, and download code does not depend on it.
    """
    client = _load_client()
    if client is None:
        raise RuntimeError("Gemini AI is not configured")

    def _generate() -> str:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
        )
        return response.text or ""

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_generate),
            timeout=_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        LOG.warning("Gemini request timed out after %ss", _TIMEOUT_SECONDS)
        raise
