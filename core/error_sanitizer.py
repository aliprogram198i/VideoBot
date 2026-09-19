"""Safe diagnostic sanitization helpers for AliBot."""

import json
import re

from downloader.url_security import redact_url


def sanitize_error_for_storage(value, max_length=4000):
    if value is None:
        return ""

    text = str(value)

    text = re.sub(
        r"(Authorization\s*:\s*Bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(Bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )

    secret_keys = (
        "BOT_TOKEN",
        "GEMINI_API_KEY",
        "YOINKU_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "API_KEY",
        "AUTHORIZATION",
        "X-API-KEY",
        "X_API_KEY",
    )

    for key in secret_keys:
        text = re.sub(
            rf"({re.escape(key)}\s*[=:]\s*)[^\s,;]+",
            r"\1[REDACTED]",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            rf"({re.escape(key)}\s+)[^\s,;]+",
            r"\1[REDACTED]",
            text,
            flags=re.IGNORECASE,
        )

    text = re.sub(
        r"(Authorization\s*:\s*)[^\s,;]+",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r'("(?:api[_-]?key|token|access[_-]?token|secret|authorization)"\s*:\s*")[^"]*(")',
        r"\1[REDACTED]\2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"((?:[?&]|\b)(?:api[_-]?key|token|access[_-]?token|secret|key)\s*=)[^&\s]+",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )

    try:
        text = redact_url(text)
    except Exception:
        pass

    return text[:max_length]


def sanitize_error_value(value, max_length=4000):
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple)):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            value = str(value)
    else:
        value = str(value)
    return sanitize_error_for_storage(value, max_length=max_length)


def sanitize_error_details(details):
    if details is None:
        return {}

    if isinstance(details, dict):
        result = {}
        for key, value in details.items():
            safe_key = str(key)[:100]
            if isinstance(value, dict):
                result[safe_key] = sanitize_error_details(value)
            elif isinstance(value, (list, tuple)):
                result[safe_key] = [
                    sanitize_error_details(item)
                    if isinstance(item, dict)
                    else sanitize_error_value(item, 2000)
                    for item in value[:50]
                ]
            else:
                result[safe_key] = sanitize_error_value(value, 4000)
        return result

    if isinstance(details, (list, tuple)):
        return [
            sanitize_error_details(item)
            if isinstance(item, dict)
            else sanitize_error_value(item, 2000)
            for item in details[:50]
        ]

    return sanitize_error_value(details, 4000)


def details_to_json(details):
    if not details:
        return None
    try:
        safe_details = sanitize_error_details(details)
        return json.dumps(
            safe_details,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )[:12000]
    except Exception:
        return None
