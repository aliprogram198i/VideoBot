import re
from pathlib import Path

path = Path('bot.py')
text = path.read_text(encoding='utf-8')

updates = {
    'ar': {
        'video_done': '''        "video_done":
            "╭━━━━━━━━━━━━━━━━━━━━╮\\n"
            "       🎬 تم تحميل الفيديو بنجاح!\\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\\n\\n"
            "👤 مرحباً {username} 🤍\\n\\n"
            "🎚 الجودة: {quality}\\n"
            "📥 الحالة: جاهز ✓\\n\\n"
            "━━━━━━━━━━━━━━━━━━━━\\n"
            "🚀 استمتع بالفيديو!\\n"
            "🔗 أرسل رابطاً آخر لبدء تحميل جديد.",\n''',
        'audio_done': '''        "audio_done":
            "╭━━━━━━━━━━━━━━━━━━━━╮\\n"
            "       🎵 تم تحميل الصوت بنجاح!\\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\\n\\n"
            "👤 مرحباً {username} 🤍\\n\\n"
            "🎚 الجودة: {quality}\\n"
            "📥 الحالة: جاهز ✓\\n\\n"
            "━━━━━━━━━━━━━━━━━━━━\\n"
            "🎧 استمتع بالصوت!\\n"
            "🔗 أرسل رابطاً آخر لبدء تحميل جديد.",\n'''
    },
    'en': {
        'video_done': '''        "video_done":
            "╭━━━━━━━━━━━━━━━━━━━━╮\\n"
            "       🎬 Video downloaded successfully!\\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\\n\\n"
            "👤 Welcome {username} 🤍\\n\\n"
            "🎚 Quality: {quality}\\n"
            "📥 Status: Ready ✓\\n\\n"
            "━━━━━━━━━━━━━━━━━━━━\\n"
            "🚀 Enjoy your video!\\n"
            "🔗 Send another link to start a new download.",\n''',
        'audio_done': '''        "audio_done":
            "╭━━━━━━━━━━━━━━━━━━━━╮\\n"
            "       🎵 Audio downloaded successfully!\\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\\n\\n"
            "👤 Welcome {username} 🤍\\n\\n"
            "🎚 Quality: {quality}\\n"
            "📥 Status: Ready ✓\\n\\n"
            "━━━━━━━━━━━━━━━━━━━━\\n"
            "🎧 Enjoy your audio!\\n"
            "🔗 Send another link to start a new download.",\n'''
    },
    'tr': {
        'video_done': '''        "video_done":
            "╭━━━━━━━━━━━━━━━━━━━━╮\\n"
            "       🎬 Video başarıyla indirildi!\\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\\n\\n"
            "👤 Hoş geldin {username} 🤍\\n\\n"
            "🎚 Kalite: {quality}\\n"
            "📥 Durum: Hazır ✓\\n\\n"
            "━━━━━━━━━━━━━━━━━━━━\\n"
            "🚀 Videonuzun keyfini çıkarın!\\n"
            "🔗 Yeni bir indirme için başka bir bağlantı gönderin.",\n''',
        'audio_done': '''        "audio_done":
            "╭━━━━━━━━━━━━━━━━━━━━╮\\n"
            "       🎵 Ses başarıyla indirildi!\\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\\n\\n"
            "👤 Hoş geldin {username} 🤍\\n\\n"
            "🎚 Kalite: {quality}\\n"
            "📥 Durum: Hazır ✓\\n\\n"
            "━━━━━━━━━━━━━━━━━━━━\\n"
            "🎧 Sesinizin keyfini çıkarın!\\n"
            "🔗 Yeni bir indirme için başka bir bağlantı gönderin.",\n'''
    },
    'de': {
        'video_done': '''        "video_done":
            "╭━━━━━━━━━━━━━━━━━━━━╮\\n"
            "       🎬 Video erfolgreich heruntergeladen!\\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\\n\\n"
            "👤 Willkommen {username} 🤍\\n\\n"
            "🎚 Qualität: {quality}\\n"
            "📥 Status: Bereit ✓\\n\\n"
            "━━━━━━━━━━━━━━━━━━━━\\n"
            "🚀 Viel Spaß mit Ihrem Video!\\n"
            "🔗 Senden Sie einen weiteren Link für einen neuen Download.",\n''',
        'audio_done': '''        "audio_done":
            "╭━━━━━━━━━━━━━━━━━━━━╮\\n"
            "       🎵 Audio erfolgreich heruntergeladen!\\n"
            "╰━━━━━━━━━━━━━━━━━━━━╯\\n\\n"
            "👤 Willkommen {username} 🤍\\n\\n"
            "🎚 Qualität: {quality}\\n"
            "📥 Status: Bereit ✓\\n\\n"
            "━━━━━━━━━━━━━━━━━━━━\\n"
            "🎧 Viel Spaß mit Ihrem Audio!\\n"
            "🔗 Senden Sie einen weiteren Link für einen neuen Download.",\n'''
    }
}

for lang, keys in updates.items():
    start = text.find(f'    "{lang}": {{')
    if start < 0:
        raise RuntimeError(f'Missing language block: {lang}')
    end = text.find('\n\n    "', start + 8)
    if end < 0:
        end = len(text)
    block = text[start:end]
    for key, replacement in keys.items():
        pattern = rf'        "{re.escape(key)}":\n(?:            .*\n)+?'
        match = re.search(pattern, block)
        if not match:
            raise RuntimeError(f'Missing {lang}.{key}')
        block = block[:match.start()] + replacement + block[match.end():]
    text = text[:start] + block + text[end:]

marker = '# ALIBOT_SUCCESS_MESSAGES_V1'
if marker not in text:
    anchor = '# ============================================================\n# قاعدة البيانات'
    if anchor not in text:
        raise RuntimeError('Database anchor not found')
    text = text.replace(anchor, marker + '\n' + anchor, 1)

path.write_text(text, encoding='utf-8')
print('Success messages updated.')
