"""Single admission contract for resolver-produced local media.

All resolver paths that return a local file must pass through this module before
the file is allowed to continue toward Telegram delivery.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

from downloader.instagram_identity import parse_instagram_post_url


def _inside(path: str, root: str | None) -> bool:
    if not root:
        return True
    try:
        candidate = Path(path).resolve()
        base = Path(root).resolve()
        return candidate == base or base in candidate.parents
    except OSError:
        return False


def validate_media_artifact(media_file: str, *, media_type: str) -> tuple[bool, dict]:
    """Validate the local artifact as actual audio/video before delivery.

    This is deliberately deterministic and fail-closed.  File existence/size
    is not sufficient: ffprobe must be able to parse the container and expose
    the expected media stream type.
    """
    result = {"valid": False, "reason": "media_not_validated"}
    try:
        path = Path(media_file).resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            result["reason"] = "missing_or_empty_media"
            return False, result
    except OSError:
        result["reason"] = "media_stat_failed"
        return False, result

    expected = "audio" if str(media_type).lower() == "audio" else "video"
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=codec_type",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        result["reason"] = "ffprobe_unavailable_or_failed"
        result["exception_type"] = type(exc).__name__
        return False, result

    if completed.returncode != 0:
        result["reason"] = "ffprobe_rejected_media"
        return False, result

    stream_types = {
        line.strip().lower()
        for line in (completed.stdout or "").splitlines()
        if line.strip()
    }
    if expected not in stream_types:
        result["reason"] = "expected_media_stream_missing"
        result["expected_stream"] = expected
        result["stream_types"] = sorted(stream_types)
        return False, result

    result.update({
        "valid": True,
        "reason": "media_container_and_stream_verified",
        "stream_types": sorted(stream_types),
    })
    return True, result


def admit_media_artifact(
    source_url: str,
    media_file: str | None,
    *,
    temp_dir: str | None = None,
    resolver: str | None = None,
    diagnostics: dict | None = None,
    media_type: str = "video",
) -> tuple[str | None, dict]:
    """Universal acceptance gate for every resolver-produced local artifact."""
    admitted, admission = admit_local_media(
        source_url,
        media_file,
        temp_dir=temp_dir,
        resolver=resolver,
        diagnostics=diagnostics,
    )
    if not admitted:
        return None, admission

    valid, validation = validate_media_artifact(admitted, media_type=media_type)
    if not valid:
        admission.update({
            "admitted": False,
            "reason": validation.get("reason", "media_validation_failed"),
            "media_validation": validation,
        })
        return None, admission

    admission["media_validation"] = validation
    admission["admitted"] = True
    admission["reason"] = "source_identity_file_and_media_verified"
    return admitted, admission


def admit_local_media(
    source_url: str,
    media_file: str | None,
    *,
    temp_dir: str | None = None,
    resolver: str | None = None,
    diagnostics: dict | None = None,
) -> tuple[str | None, dict]:
    """Fail-closed admission for direct resolver artifacts."""
    details = dict(diagnostics or {})
    result = {
        "admitted": False,
        "resolver": resolver or details.get("resolver") or "unknown",
        "reason": "not_admitted",
    }

    if not isinstance(source_url, str) or not source_url:
        result["reason"] = "missing_source_url"
        return None, result

    if not isinstance(media_file, str) or not media_file:
        result["reason"] = "missing_media_file"
        return None, result

    try:
        path = Path(media_file).resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            result["reason"] = "missing_or_empty_media"
            return None, result
    except OSError:
        result["reason"] = "media_stat_failed"
        return None, result

    if not _inside(str(path), temp_dir):
        result["reason"] = "media_outside_temp_dir"
        return None, result

    resolver_name = result["resolver"]
    parsed_instagram = parse_instagram_post_url(source_url)
    if parsed_instagram is not None:
        if resolver_name not in {
            "instagram_relay_html",
            "instagram_graphql",
            "cobalt_instagram",
        }:
            result["reason"] = "instagram_untrusted_resolver"
            return None, result
        proof = details.get("identity_proof")
        if (
            details.get("source_identity_verified") is not True
            or not isinstance(proof, dict)
            or proof.get("type") != "instagram_shortcode"
            or proof.get("key") != parsed_instagram.key
        ):
            result["reason"] = "instagram_identity_proof_missing_or_mismatch"
            return None, result

        result["source_identity"] = parsed_instagram.key
        result["identity_proof"] = proof

    result.update({"admitted": True, "reason": "source_identity_and_file_verified"})
    return str(path), result
