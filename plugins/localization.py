"""Centralized user-facing localization for core AliBot UX layers.

Keep reusable UI text here when the same feature is exposed in multiple
handlers. Feature-specific labels may remain local when they are complete.
"""

LANGUAGES = ("ar", "en", "tr", "de")


def language(value: str | None) -> str:
    return value if value in LANGUAGES else "ar"


_MESSAGES = {
    "smart_search": {
        "ar": {
            "title": "🔎 <b>البحث الذكي</b>",
            "page": "📄 النتائج {start}–{end} من {total}  •  الصفحة {page}/{pages}",
            "previous": "⬅️ السابق", "more": "➡️ المزيد",
            "new": "🔎 بحث جديد", "cancel": "❌ إلغاء",
            "invalid": "❌ اكتب عبارة بحث بين حرفين و160 حرفًا.",
            "searching": "🔎 جاري البحث الذكي الاحترافي...\n\n⚙️ يتم تحليل وترتيب النتائج خوارزميًا.",
            "empty": "❌ لم أجد نتائج مناسبة. جرّب كلمات بحث مختلفة.",
            "expired": "❌ انتهت صلاحية نتائج البحث. أعد البحث من جديد.",
            "invalid_result": "❌ تعذر التحقق من نتيجة البحث.",
            "cancelled": "❌ تم إلغاء البحث الذكي.",
        },
        "en": {
            "title": "🔎 <b>Smart Search</b>",
            "page": "📄 Results {start}–{end} of {total}  •  Page {page}/{pages}",
            "previous": "⬅️ Previous", "more": "➡️ More",
            "new": "🔎 New search", "cancel": "❌ Cancel",
            "invalid": "❌ Enter a search phrase between 2 and 160 characters.",
            "searching": "🔎 Smart Search is working...\n\n⚙️ Results are being analyzed and ranked algorithmically.",
            "empty": "❌ No suitable results were found. Try different search terms.",
            "expired": "❌ The search results have expired. Start a new search.",
            "invalid_result": "❌ The selected search result could not be verified.",
            "cancelled": "❌ Smart Search cancelled.",
        },
        "tr": {
            "title": "🔎 <b>Akıllı Arama</b>",
            "page": "📄 {total} sonuçtan {start}–{end}  •  Sayfa {page}/{pages}",
            "previous": "⬅️ Önceki", "more": "➡️ Daha fazla",
            "new": "🔎 Yeni arama", "cancel": "❌ İptal",
            "invalid": "❌ 2 ile 160 karakter arasında bir arama ifadesi girin.",
            "searching": "🔎 Akıllı Arama çalışıyor...\n\n⚙️ Sonuçlar algoritmik olarak analiz edilip sıralanıyor.",
            "empty": "❌ Uygun sonuç bulunamadı. Farklı arama terimleri deneyin.",
            "expired": "❌ Arama sonuçlarının süresi doldu. Yeni bir arama başlatın.",
            "invalid_result": "❌ Seçilen arama sonucu doğrulanamadı.",
            "cancelled": "❌ Akıllı Arama iptal edildi.",
        },
        "de": {
            "title": "🔎 <b>Intelligente Suche</b>",
            "page": "📄 Ergebnisse {start}–{end} von {total}  •  Seite {page}/{pages}",
            "previous": "⬅️ Zurück", "more": "➡️ Mehr",
            "new": "🔎 Neue Suche", "cancel": "❌ Abbrechen",
            "invalid": "❌ Geben Sie einen Suchbegriff mit 2 bis 160 Zeichen ein.",
            "searching": "🔎 Intelligente Suche läuft...\n\n⚙️ Die Ergebnisse werden algorithmisch analysiert und sortiert.",
            "empty": "❌ Keine passenden Ergebnisse gefunden. Versuchen Sie andere Suchbegriffe.",
            "expired": "❌ Die Suchergebnisse sind abgelaufen. Starten Sie eine neue Suche.",
            "invalid_result": "❌ Das ausgewählte Suchergebnis konnte nicht überprüft werden.",
            "cancelled": "❌ Intelligente Suche abgebrochen.",
        },
    },
    "studio": {
        "ar": {
            "audio_formats": "🎧 صيغ الصوت", "image": "🖼️ صورة", "compress": "🗜️ ضغط",
            "trim15": "✂️ أول 15ث", "trim30": "✂️ أول 30ث", "custom_trim": "✂️ قص مخصص",
            "resize": "📐 المقاس", "preset": "📱 استخدام", "volume": "🔊 الصوت",
            "back": "🔙 رجوع", "mute": "🔇 كتم الصوت",
            "audio_prompt": "🎧 اختر صيغة الصوت:", "resize_prompt": "📐 اختر المقاس:",
            "preset_prompt": "📱 اختر الاستخدام:", "volume_prompt": "🔊 اختر مستوى الصوت:",
            "custom_prompt": "✂️ أرسل الفترة بهذا الشكل:\n00:10 - 00:40\n\nالحد الأقصى للقص المخصص: 5 دقائق.",
            "invalid_trim": "⚠️ الصيغة غير صحيحة. أرسل مثلًا: 00:10 - 00:40\nالمدة القصوى 5 دقائق.",
            "expired": "⚠️ انتهت صلاحية نسخة الاستوديو لهذا الفيديو.\nأعد تحميل الفيديو ثم استخدم أدوات الاستوديو.",
            "failed": "❌ تعذر تنفيذ العملية على هذا الفيديو.\nجرّب إعدادًا آخر أو فيديو أقصر.",
            "done_audio": "🎵 تم تجهيز الصوت بواسطة AliBot.", "done_photo": "🖼️ تم استخراج الصورة المصغرة من الفيديو.",
            "done_video": "🎬 تم تجهيز الفيديو بواسطة AliBot.",
            "mp3_status": "🎵 جاري استخراج الصوت بصيغة MP3...", "audio_status": "🎧 جاري تجهيز الصيغة الصوتية...",
            "thumb_status": "🖼️ جاري استخراج الصورة...", "trim_status": "✂️ جاري قص المقطع...",
            "custom_status": "✂️ جاري تنفيذ القص المخصص...", "compress_status": "🗜️ جاري ضغط الفيديو...",
            "resize_status": "📐 جاري تغيير المقاس...", "preset_status": "📱 جاري تجهيز الفيديو للاستخدام المحدد...",
            "volume_status": "🔊 جاري تعديل مستوى الصوت...",
        },
        "en": {
            "audio_formats": "🎧 Audio formats", "image": "🖼️ Image", "compress": "🗜️ Compress",
            "trim15": "✂️ First 15s", "trim30": "✂️ First 30s", "custom_trim": "✂️ Custom trim",
            "resize": "📐 Resize", "preset": "📱 Preset", "volume": "🔊 Volume",
            "back": "🔙 Back", "mute": "🔇 Mute",
            "audio_prompt": "🎧 Choose audio format:", "resize_prompt": "📐 Choose size:",
            "preset_prompt": "📱 Choose a preset:", "volume_prompt": "🔊 Choose volume:",
            "custom_prompt": "✂️ Send the range like this:\n00:10 - 00:40\n\nMaximum custom trim: 5 minutes.",
            "invalid_trim": "⚠️ Invalid format. Send for example: 00:10 - 00:40\nMaximum duration is 5 minutes.",
            "expired": "⚠️ The Studio copy for this video has expired.\nDownload the video again, then use Studio tools.",
            "failed": "❌ The operation could not be completed for this video.\nTry another setting or a shorter video.",
            "done_audio": "🎵 Audio prepared by AliBot.", "done_photo": "🖼️ Thumbnail extracted from the video.",
            "done_video": "🎬 Video prepared by AliBot.",
            "mp3_status": "🎵 Extracting audio as MP3...", "audio_status": "🎧 Preparing the audio format...",
            "thumb_status": "🖼️ Extracting image...", "trim_status": "✂️ Trimming the clip...",
            "custom_status": "✂️ Applying custom trim...", "compress_status": "🗜️ Compressing video...",
            "resize_status": "📐 Resizing video...", "preset_status": "📱 Preparing the selected preset...",
            "volume_status": "🔊 Adjusting volume...",
        },
        "tr": {
            "audio_formats": "🎧 Ses biçimleri", "image": "🖼️ Görsel", "compress": "🗜️ Sıkıştır",
            "trim15": "✂️ İlk 15 sn", "trim30": "✂️ İlk 30 sn", "custom_trim": "✂️ Özel kes",
            "resize": "📐 Boyut", "preset": "📱 Ön ayar", "volume": "🔊 Ses",
            "back": "🔙 Geri", "mute": "🔇 Sessiz",
            "audio_prompt": "🎧 Ses biçimini seçin:", "resize_prompt": "📐 Boyutu seçin:",
            "preset_prompt": "📱 Ön ayar seçin:", "volume_prompt": "🔊 Ses seviyesini seçin:",
            "custom_prompt": "✂️ Aralığı şu şekilde gönderin:\n00:10 - 00:40\n\nÖzel kesme sınırı: 5 dakika.",
            "invalid_trim": "⚠️ Biçim geçersiz. Örneğin: 00:10 - 00:40\nMaksimum süre 5 dakikadır.",
            "expired": "⚠️ Bu video için Studio kopyasının süresi doldu.\nVideoyu yeniden indirin ve Studio araçlarını kullanın.",
            "failed": "❌ Bu video üzerinde işlem tamamlanamadı.\nBaşka bir ayar veya daha kısa bir video deneyin.",
            "done_audio": "🎵 Ses AliBot tarafından hazırlandı.", "done_photo": "🖼️ Video küçük resmi çıkarıldı.",
            "done_video": "🎬 Video AliBot tarafından hazırlandı.",
            "mp3_status": "🎵 Ses MP3 olarak çıkarılıyor...", "audio_status": "🎧 Ses biçimi hazırlanıyor...",
            "thumb_status": "🖼️ Görsel çıkarılıyor...", "trim_status": "✂️ Klip kesiliyor...",
            "custom_status": "✂️ Özel kesme uygulanıyor...", "compress_status": "🗜️ Video sıkıştırılıyor...",
            "resize_status": "📐 Video yeniden boyutlandırılıyor...", "preset_status": "📱 Seçilen ön ayar hazırlanıyor...",
            "volume_status": "🔊 Ses seviyesi ayarlanıyor...",
        },
        "de": {
            "audio_formats": "🎧 Audioformate", "image": "🖼️ Bild", "compress": "🗜️ Komprimieren",
            "trim15": "✂️ Erste 15 Sek.", "trim30": "✂️ Erste 30 Sek.", "custom_trim": "✂️ Benutzerdefiniert",
            "resize": "📐 Größe", "preset": "📱 Preset", "volume": "🔊 Lautstärke",
            "back": "🔙 Zurück", "mute": "🔇 Stumm",
            "audio_prompt": "🎧 Audioformat auswählen:", "resize_prompt": "📐 Größe auswählen:",
            "preset_prompt": "📱 Preset auswählen:", "volume_prompt": "🔊 Lautstärke auswählen:",
            "custom_prompt": "✂️ Bereich so senden:\n00:10 - 00:40\n\nMaximale benutzerdefinierte Schnittdauer: 5 Minuten.",
            "invalid_trim": "⚠️ Ungültiges Format. Beispiel: 00:10 - 00:40\nMaximale Dauer: 5 Minuten.",
            "expired": "⚠️ Die Studio-Kopie dieses Videos ist abgelaufen.\nLaden Sie das Video erneut herunter und verwenden Sie danach Studio.",
            "failed": "❌ Die Verarbeitung dieses Videos ist fehlgeschlagen.\nVersuchen Sie eine andere Einstellung oder ein kürzeres Video.",
            "done_audio": "🎵 Audio wurde von AliBot vorbereitet.", "done_photo": "🖼️ Vorschaubild aus dem Video extrahiert.",
            "done_video": "🎬 Video wurde von AliBot vorbereitet.",
            "mp3_status": "🎵 Audio wird als MP3 extrahiert...", "audio_status": "🎧 Audioformat wird vorbereitet...",
            "thumb_status": "🖼️ Bild wird extrahiert...", "trim_status": "✂️ Clip wird geschnitten...",
            "custom_status": "✂️ Benutzerdefinierter Schnitt wird angewendet...", "compress_status": "🗜️ Video wird komprimiert...",
            "resize_status": "📐 Videogröße wird geändert...", "preset_status": "📱 Gewähltes Preset wird vorbereitet...",
            "volume_status": "🔊 Lautstärke wird angepasst...",
        },
    },
}


def t(section: str, key: str, lang: str | None = None, **kwargs) -> str:
    lang = language(lang)
    value = _MESSAGES[section][lang][key]
    return value.format(**kwargs) if kwargs else value
