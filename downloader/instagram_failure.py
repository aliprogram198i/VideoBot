"""Deterministic user-facing classification for Instagram download failures.

This module only classifies diagnostics already collected by AliBot. It does
not perform network requests, authentication, or access-control bypasses.
"""

from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return str(value)
    return str(value)


def classify_instagram_failure(
    *,
    primary_stderr: str = "",
    primary_stdout: str = "",
    smart_diagnostics: dict[str, Any] | None = None,
    cobalt_diagnostics: dict[str, Any] | None = None,
) -> str:
    """Return a stable category from existing Instagram failure diagnostics."""
    smart = smart_diagnostics or {}
    cobalt = cobalt_diagnostics or {}

    text = " ".join(
        [
            _text(primary_stderr),
            _text(primary_stdout),
            _text(smart.get("error_message")),
            _text(smart.get("stderr_tail")),
            _text(smart.get("stdout_tail")),
            _text(cobalt.get("error_code")),
            _text(cobalt.get("error_context")),
            _text(cobalt.get("reason")),
            _text(cobalt.get("error_message")),
        ]
    ).lower()

    if any(
        marker in text
        for marker in (
            "this content isn't available to everyone",
            "can't be seen by certain audiences",
            "certain audiences",
            "error.api.fetch.empty",
        )
    ):
        return "audience_restricted"

    if any(
        marker in text
        for marker in (
            "login required",
            "login to instagram",
            "log in to instagram",
            "please log in",
            "requires login",
        )
    ):
        return "login_required"

    if any(
        marker in text
        for marker in (
            "rate-limit reached",
            "rate limit",
            "too many requests",
            "429",
        )
    ):
        return "rate_limited"

    if any(
        marker in text
        for marker in (
            "private account",
            "private post",
            "this account is private",
        )
    ):
        return "private"

    if any(
        marker in text
        for marker in (
            "not found",
            "doesn't exist",
            "does not exist",
            "content is unavailable",
            "content isn't available",
            "requested content is not available",
            "page isn't available",
        )
    ):
        return "unavailable"

    if smart.get("exception_type") or cobalt.get("exception_type"):
        return "extractor_error"

    return "unknown"


_MESSAGES = {
    "ar": {
        "audience_restricted": (
            "⚠️ تعذر الوصول إلى هذا المنشور من جلسة Instagram العامة حالياً.\n\n"
            "قد يكون المحتوى مقيداً بجمهور أو عمر معيّن، أو يتطلب الوصول من حساب Instagram.\n"
            "لم يتم استخدام حساب أو بيانات تسجيل دخول لتجاوز هذا القيد."
        ),
        "login_required": (
            "🔐 هذا المحتوى يتطلب تسجيل الدخول إلى Instagram حالياً.\n\n"
            "لا يمكن لـ AliBot الوصول إليه من الجلسة العامة."
        ),
        "rate_limited": (
            "⏳ Instagram يفرض حالياً تقييداً على طلبات الوصول.\n\n"
            "يرجى المحاولة لاحقاً."
        ),
        "private": (
            "🔒 هذا المحتوى تابع لحساب خاص أو غير متاح للجلسة العامة."
        ),
        "unavailable": (
            "❌ هذا المنشور غير متاح حالياً على Instagram، أو تم حذفه/تقييده."
        ),
        "extractor_error": (
            "⚠️ تعذر استخراج الوسائط من Instagram حالياً.\n\n"
            "يرجى المحاولة مرة أخرى لاحقاً."
        ),
        "unknown": (
            "❌ تعذر تحميل هذا الرابط.\n\n"
            "قد يكون الرابط غير متاح حالياً، أو أن المنصة تحتاج إلى تسجيل دخول، "
            "أو أن الفيديو غير مدعوم."
        ),
    },
    "en": {
        "audience_restricted": (
            "⚠️ Instagram did not make this post available to the public session.\n\n"
            "It may be restricted by audience or age, or require an Instagram account.\n"
            "AliBot did not use account credentials to bypass this restriction."
        ),
        "login_required": (
            "🔐 Instagram currently requires a login to access this content.\n\n"
            "AliBot cannot access it from the public session."
        ),
        "rate_limited": (
            "⏳ Instagram is currently rate-limiting access.\n\n"
            "Please try again later."
        ),
        "private": "🔒 This content belongs to a private account or is not public.",
        "unavailable": "❌ This Instagram post is currently unavailable or removed.",
        "extractor_error": (
            "⚠️ Instagram media could not be extracted right now.\n\n"
            "Please try again later."
        ),
        "unknown": (
            "❌ This link could not be downloaded.\n\n"
            "The content may be unavailable, require login, or be unsupported."
        ),
    },
    "tr": {
        "audience_restricted": (
            "⚠️ Instagram bu gönderiyi herkese açık oturuma sunmadı.\n\n"
            "İçerik yaş veya kitle kısıtlamasına sahip olabilir ya da bir Instagram hesabı gerektirebilir."
        ),
        "login_required": (
            "🔐 Instagram bu içeriğe erişmek için giriş yapılmasını gerektiriyor."
        ),
        "rate_limited": "⏳ Instagram erişimi geçici olarak sınırlandırıyor. Lütfen daha sonra tekrar deneyin.",
        "private": "🔒 Bu içerik gizli bir hesaba ait veya herkese açık değil.",
        "unavailable": "❌ Bu Instagram gönderisi şu anda kullanılamıyor.",
        "extractor_error": "⚠️ Instagram medyası şu anda çıkarılamadı. Lütfen daha sonra tekrar deneyin.",
        "unknown": "❌ Bu bağlantı indirilemedi. İçerik kullanılamıyor, giriş gerektiriyor veya desteklenmiyor olabilir.",
    },
    "de": {
        "audience_restricted": (
            "⚠️ Instagram stellt diesen Beitrag der öffentlichen Sitzung derzeit nicht zur Verfügung.\n\n"
            "Der Inhalt kann nach Zielgruppe oder Alter eingeschränkt sein oder ein Instagram-Konto erfordern."
        ),
        "login_required": "🔐 Instagram erfordert derzeit eine Anmeldung für diesen Inhalt.",
        "rate_limited": "⏳ Instagram begrenzt den Zugriff derzeit. Bitte später erneut versuchen.",
        "private": "🔒 Dieser Inhalt gehört zu einem privaten Konto oder ist nicht öffentlich.",
        "unavailable": "❌ Dieser Instagram-Beitrag ist derzeit nicht verfügbar.",
        "extractor_error": "⚠️ Instagram-Medien konnten derzeit nicht extrahiert werden. Bitte später erneut versuchen.",
        "unknown": "❌ Dieser Link konnte nicht heruntergeladen werden. Der Inhalt ist möglicherweise nicht verfügbar, erfordert eine Anmeldung oder wird nicht unterstützt.",
    },
}


def instagram_failure_message(
    language: str,
    *,
    primary_stderr: str = "",
    primary_stdout: str = "",
    smart_diagnostics: dict[str, Any] | None = None,
    cobalt_diagnostics: dict[str, Any] | None = None,
) -> str:
    category = classify_instagram_failure(
        primary_stderr=primary_stderr,
        primary_stdout=primary_stdout,
        smart_diagnostics=smart_diagnostics,
        cobalt_diagnostics=cobalt_diagnostics,
    )
    messages = _MESSAGES.get(language, _MESSAGES["en"])
    return messages.get(category, messages["unknown"])
