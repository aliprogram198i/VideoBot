"""Compatibility patch for the current Yoinku API contract.

The downloader keeps its existing fallback pipeline. This module only replaces
bot.download_with_yoinku so audio requests use the documented Yoinku format
ID (a-mp3) instead of the obsolete a-320 value.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import urllib.parse
from urllib.error import HTTPError
from urllib.request import Request


def install(bot_module):
    """Install the Yoinku compatibility implementation into bot.py."""

    async def download_with_yoinku(
        url,
        temp_dir,
        is_audio=False,
        attempt_id=None,
        attempt_number=None,
    ):
        api_key = os.getenv("YOINKU_API_KEY")
        started_at = time.monotonic()
        diagnostics = {
            "attempt_id": attempt_id,
            "attempt_number": attempt_number,
            "status": "not_started",
            "duration_ms": None,
            "internal_attempts": 0,
            "exception_type": None,
            "error_message": None,
            "http_status": None,
            "response_type": None,
            "bytes_downloaded": None,
        }

        if not api_key:
            diagnostics.update({
                "status": "not_configured",
                "exception_type": "MissingAPIKey",
                "error_message": "YOINKU_API_KEY is not configured.",
                "duration_ms": int((time.monotonic() - started_at) * 1000),
            })
            return None, diagnostics

        limit = (
            bot_module.MAX_AUDIO_DOWNLOAD_BYTES
            if is_audio
            else bot_module.MAX_VIDEO_DOWNLOAD_BYTES
        )

        if shutil.disk_usage(temp_dir).free < min(
            limit,
            bot_module.MIN_FREE_SPACE_BYTES,
        ):
            diagnostics.update({
                "status": "insufficient_space",
                "exception_type": "InsufficientFreeSpace",
                "error_message": "Insufficient free space for Yoinku download.",
                "duration_ms": int((time.monotonic() - started_at) * 1000),
            })
            return None, diagnostics

        # Yoinku currently documents a-mp3 for audio. The old a-320 ID
        # returns 404 and caused the Smart Search -> Audio path to fail.
        format_id = "a-mp3" if is_audio else "v-720"
        extension = ".mp3" if is_audio else ".mp4"
        api_url = (
            "https://yoinku.com/api/v1/download?"
            + urllib.parse.urlencode({"url": url, "format": format_id})
        )
        output_file = os.path.join(temp_dir, "yoinku_download" + extension)
        fetch_deadline = time.monotonic() + bot_module.YOINKU_DOWNLOAD_TIMEOUT

        def fetch():
            request = Request(
                api_url,
                headers={
                    "x-api-key": api_key,
                    "Accept": "application/json",
                    "User-Agent": "VideoBot/1.0",
                },
            )
            remaining_time = fetch_deadline - time.monotonic()
            if remaining_time <= 0:
                raise TimeoutError("Yoinku download deadline exceeded")

            with bot_module.safe_urlopen(
                request,
                timeout=min(30, max(1, remaining_time)),
                max_bytes=bot_module.MAX_YOINKU_RESPONSE_BYTES,
                expected_content_types={"application/json"},
            ) as response:
                diagnostics["http_status"] = getattr(response, "status", None)
                diagnostics["response_type"] = response.headers.get("Content-Type")
                payload = bot_module.read_limited(
                    response,
                    bot_module.MAX_YOINKU_RESPONSE_BYTES,
                )

            data = json.loads(payload.decode("utf-8"))
            direct_url = (
                data.get("url")
                if isinstance(data, dict) and data.get("ok")
                else None
            )
            if not direct_url:
                error = data.get("error") if isinstance(data, dict) else None
                raise ValueError(
                    f"Yoinku returned no download URL: {error or 'unknown error'}"
                )

            bot_module.validate_public_http_url(direct_url)
            request = Request(direct_url, headers={"User-Agent": "VideoBot/1.0"})

            remaining_time = fetch_deadline - time.monotonic()
            if remaining_time <= 0:
                raise TimeoutError("Yoinku file download deadline exceeded")

            try:
                with bot_module.safe_urlopen(
                    request,
                    timeout=min(60, max(1, remaining_time)),
                    max_bytes=limit,
                ) as response, open(output_file, "wb") as output:
                    diagnostics["http_status"] = getattr(response, "status", None)
                    diagnostics["response_type"] = response.headers.get("Content-Type")
                    total = 0
                    while True:
                        remaining_time = fetch_deadline - time.monotonic()
                        if remaining_time <= 0:
                            raise TimeoutError("Yoinku file download deadline exceeded")
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > limit:
                            raise ValueError("Yoinku file exceeds configured size limit")
                        output.write(chunk)
                    diagnostics["bytes_downloaded"] = total
            except Exception:
                try:
                    os.remove(output_file)
                except FileNotFoundError:
                    pass
                raise

            if not os.path.isfile(output_file) or os.path.getsize(output_file) == 0:
                raise ValueError("Yoinku returned an empty file")
            return output_file

        for attempt in range(3):
            diagnostics["internal_attempts"] = attempt + 1
            try:
                result = await asyncio.to_thread(fetch)
                diagnostics.update({
                    "status": "success",
                    "duration_ms": int((time.monotonic() - started_at) * 1000),
                    "format_id": format_id,
                })
                return result, diagnostics
            except HTTPError as exc:
                diagnostics["http_status"] = getattr(exc, "code", None)
                diagnostics["exception_type"] = type(exc).__name__
                retry_after = None
                try:
                    retry_after = exc.headers.get("Retry-After")
                except Exception:
                    pass
                diagnostics["retry_after"] = retry_after
                diagnostics["error_message"] = bot_module.sanitize_error_for_storage(str(exc))
                if getattr(exc, "code", None) == 429:
                    break
                if attempt < 2 and time.monotonic() < fetch_deadline:
                    await asyncio.sleep(min(2 ** attempt, fetch_deadline - time.monotonic()))
            except (OSError, TimeoutError) as exc:
                diagnostics["exception_type"] = type(exc).__name__
                diagnostics["error_message"] = bot_module.sanitize_error_for_storage(str(exc))
                if attempt < 2 and time.monotonic() < fetch_deadline:
                    await asyncio.sleep(min(2 ** attempt, fetch_deadline - time.monotonic()))
            except Exception as exc:
                diagnostics["exception_type"] = type(exc).__name__
                diagnostics["error_message"] = bot_module.sanitize_error_for_storage(str(exc))
                break

        diagnostics.update({
            "status": "failed",
            "duration_ms": int((time.monotonic() - started_at) * 1000),
            "format_id": format_id,
        })
        return None, diagnostics

    bot_module.download_with_yoinku = download_with_yoinku
    print("🔧 Yoinku compatibility: audio format fixed (a-mp3)", flush=True)
