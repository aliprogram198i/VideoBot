"""Deterministic classification for Instagram download failures.

This module only classifies already-observed extractor diagnostics. It never
changes resolver routing, authentication, or source-identity policy.
"""

from __future__ import annotations


AUDIENCE_RESTRICTION_MARKERS = (
    "this content isn't available to everyone",
    "this content isn't available to everyone:",
    "can't be seen by certain audiences",
)

LOGIN_MARKERS = (
    "login required",
    "log in to continue",
    "login page",
    "redirected to the login page",
)

RATE_LIMIT_MARKERS = (
    "rate-limit reached",
    "rate limit reached",
    "too many requests",
)

PRIVATE_MARKERS = (
    "private",
    "this account is private",
)

DELETED_MARKERS = (
    "not found",
    "does not exist",
    "has been deleted",
    "removed",
)

EMPTY_MEDIA_MARKERS = (
    "empty media response",
    "error.api.fetch.empty",
)

EXTRACTOR_MARKERS = (
    "unsupported url",
    "unable to extract",
    "no video formats found",
    "no media found",
)


def classify_instagram_failure(
    *,
    stderr: str = "",
    stdout: str = "",
    cobalt_code: str | None = None,
) -> str:
    """Return a stable, user-facing failure category.

    The input is intentionally limited to diagnostics already produced by
    trusted resolvers. Matching is deterministic and fail-safe.
    """

    text = f"{stderr}
{stdout}".lower()

    if any(marker in text for marker in AUDIENCE_RESTRICTION_MARKERS):
        return "audience_restricted"

    if any(marker in text for marker in LOGIN_MARKERS):
        return "login_required"

    if any(marker in text for marker in RATE_LIMIT_MARKERS):
        return "rate_limited"

    if any(marker in text for marker in PRIVATE_MARKERS):
        return "private"

    if any(marker in text for marker in DELETED_MARKERS):
        return "unavailable"

    if cobalt_code == "error.api.fetch.empty":
        return "access_unavailable"

    if any(marker in text for marker in EMPTY_MEDIA_MARKERS):
        return "access_unavailable"

    if any(marker in text for marker in EXTRACTOR_MARKERS):
        return "extractor_error"

    return "unknown"


MESSAGES = {
    "ar": {
        "audience_restricted": (
            "⚠️ هذا المحتوى غير متاح حالياً للتنزيل من جلسة البوت.\n\n"
            "قد يكون مقيّداً حسب العمر أو الجمهور أو يتطلب تسجيل الدخول إلى Instagram.\n\n"
            "جرّب رابط Reel آخر متاح للعامة."
        ),
        "login_required": (
            "⚠️ Instagram يتطلب تسجيل الدخول للوصول إلى هذا المحتوى.\n\n"
            "لا يمكن لـ AliBot الوصول إليه من جلسة عامة حالياً.\n\n"
            "جرّب رابطاً عاماً آخر."
        ),
        "rate_limited": (
            "⏳ Instagram يحدّ مؤقتاً من الوصول إلى هذا المحتوى.\n\n"
            "يرجى المحاولة لاحقاً أو تجربة رابط آخر."
        ),
        "private": (
            "🔒 هذا المحتوى مرتبط بحساب خاص ولا يمكن الوصول إليه من جلسة عامة."
        ),
        "unavailable": (
            "❌ يبدو أن محتوى Instagram غير متاح حالياً أو لم يعد موجوداً.\n\n"
            "تحقق من الرابط ثم جرّب مرة أخرى."
        ),
        "access_unavailable": (
            "⚠️ تعذر الوصول إلى وسائط Instagram من جلسة البوت الحالية.\n\n"
            "جرّب رابطاً عاماً آخر أو أعد المحاولة لاحقاً."
        ),
        "extractor_error": (
            "⚠️ تعذر استخراج وسائط Instagram من هذا الرابط حالياً.\n\n"
            "جرّب رابطاً آخر أو أعد المحاولة لاحقاً."
        ),
        "unknown": (
            "❌ تعذر تحميل هذا الرابط حالياً.\n\n"
            "قد يكون المحتوى غير متاح، أو يحتاج إلى تسجيل دخول، أو حدث خطأ مؤقت."
        ),
    },
    "en": {
        "audience_restricted": (
            "⚠️ This content is not currently available to the bot's public session.\n\n"
            "It may be restricted by age or audience, or require an Instagram login.\n\n"
            "Try another publicly available Reel."
        ),
        "login_required": (
            "⚠️ Instagram requires login to access this content.\n\n"
            "AliBot cannot access it from a public session right now.\n\n"
            "Try another public link."
        ),
        "rate_limited": (
            "⏳ Instagram is temporarily limiting access to this content.\n\n"
            "Please try again later or use another link."
        ),
        "private": (
            "🔒 This content belongs to a private account and cannot be accessed from a public session."
        ),
        "unavailable": (
            "❌ This Instagram content appears to be unavailable or no longer exists.\n\n"
            "Check the link and try again."
        ),
        "access_unavailable": (
            "⚠️ Instagram media could not be accessed from the bot's current session.\n\n"
            "Try another public link or try again later."
        ),
        "extractor_error": (
            "⚠️ Instagram media could not be extracted from this link right now.\n\n"
            "Try another link or try again later."
        ),
        "unknown": (
            "❌ This link could not be downloaded right now.\n\n"
            "The content may be unavailable, require login, or be affected by a temporary error."
        ),
    },
    "tr": {
        "audience_restricted": (
            "⚠️ Bu içerik şu anda botun herkese açık oturumundan indirilemiyor.\n\n"
            "Yaş veya kitle kısıtlaması olabilir ya da Instagram girişi gerekebilir.\n\n"
            "Başka bir herkese açık Reel deneyin."
        ),
        "login_required": (
            "⚠️ Instagram bu içeriğe erişmek için giriş istiyor.\n\n"
            "AliBot şu anda herkese açık oturumdan erişemiyor.\n\n"
            "Başka bir herkese açık bağlantı deneyin."
        ),
        "rate_limited": (
            "⏳ Instagram bu içeriğe erişimi geçici olarak sınırlıyor.\n\n"
            "Daha sonra tekrar deneyin veya başka bir bağlantı kullanın."
        ),
        "private": "🔒 Bu içerik özel bir hesaba ait ve herkese açık oturumdan erişilemiyor.",
        "unavailable": "❌ Bu Instagram içeriği şu anda kullanılamıyor veya artık mevcut değil.\n\nBağlantıyı kontrol edip tekrar deneyin.",
        "access_unavailable": "⚠️ Instagram medyasına mevcut bot oturumundan erişilemedi.\n\nBaşka bir herkese açık bağlantı deneyin veya daha sonra tekrar deneyin.",
        "extractor_error": "⚠️ Bu Instagram bağlantısından medya şu anda çıkarılamadı.\n\nBaşka bir bağlantı deneyin veya daha sonra tekrar deneyin.",
        "unknown": "❌ Bu bağlantı şu anda indirilemedi.\n\nİçerik kullanılamıyor, giriş gerektiriyor veya geçici bir hata oluşmuş olabilir.",
    },
    "de": {
        "audience_restricted": (
            "⚠️ Dieser Inhalt ist derzeit für die öffentliche Bot-Sitzung nicht verfügbar.\n\n"
            "Er kann alters- oder zielgruppenbeschränkt sein oder eine Instagram-Anmeldung erfordern.\n\n"
            "Versuche ein anderes öffentlich verfügbares Reel."
        ),
        "login_required": (
            "⚠️ Instagram erfordert eine Anmeldung für diesen Inhalt.\n\n"
            "AliBot kann derzeit nicht über eine öffentliche Sitzung darauf zugreifen.\n\n"
            "Versuche einen anderen öffentlichen Link."
        ),
        "rate_limited": "⏳ Instagram begrenzt den Zugriff vorübergehend.\n\nVersuche es später erneut oder nutze einen anderen Link.",
        "private": "🔒 Dieser Inhalt gehört zu einem privaten Konto und ist über eine öffentliche Sitzung nicht zugänglich.",
        "unavailable": "❌ Dieser Instagram-Inhalt ist offenbar nicht verfügbar oder existiert nicht mehr.\n\nÜberprüfe den Link und versuche es erneut.",
        "access_unavailable": "⚠️ Auf die Instagram-Medien konnte über die aktuelle Bot-Sitzung nicht zugegriffen werden.\n\nVersuche einen anderen öffentlichen Link oder später erneut.",
        "extractor_error": "⚠️ Die Instagram-Medien konnten aus diesem Link derzeit nicht extrahiert werden.\n\nVersuche einen anderen Link oder später erneut.",
        "unknown": "❌ Dieser Link konnte derzeit nicht heruntergeladen werden.\n\nDer Inhalt ist möglicherweise nicht verfügbar, erfordert eine Anmeldung oder es liegt ein vorübergehender Fehler vor.",
    },
}


def instagram_failure_message(
    language: str,
    *,
    stderr: str = "",
    stdout: str = "",
    cobalt_code: str | None = None,
) -> str:
    """Build a localized message from the observed failure category."""

    category = classify_instagram_failure(
        stderr=stderr,
        stdout=stdout,
        cobalt_code=cobalt_code,
    )
    messages = MESSAGES.get(language, MESSAGES["ar"])
    return messages.get(category, messages["unknown"])
