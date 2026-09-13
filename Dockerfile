FROM python:3.12-slim

ARG DENO_VERSION=2.8.3

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --uid 10001 videobot \
    && curl -fsSL "https://dl.deno.land/release/v${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip" -o /tmp/deno.zip \
    && curl -fsSL "https://dl.deno.land/release/v${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip.sha256sum" -o /tmp/deno.sha256 \
    && expected="$(awk '{print $1}' /tmp/deno.sha256)" \
    && actual="$(sha256sum /tmp/deno.zip | awk '{print $1}')" \
    && test "$actual" = "$expected" \
    && unzip -q /tmp/deno.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/deno \
    && rm -f /tmp/deno.zip /tmp/deno.sha256 \
    && deno --version

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && mkdir -p /opt/yt-dlp-plugins/yt_dlp_plugins/extractor /opt/pw-browsers \
    && python -c "from importlib.metadata import distribution; from pathlib import Path; import shutil; src=Path(distribution('yt-dlp-threads').locate_file('yt_dlp_plugins/extractor/threads.py')); assert src.is_file(), 'yt-dlp-threads extractor file not found'; shutil.copy2(src, '/opt/yt-dlp-plugins/yt_dlp_plugins/extractor/threads.py')" \
    && PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/pw-browsers \
    && printf '%s\n' '--plugin-dirs /opt/yt-dlp-plugins' > /etc/yt-dlp.conf
COPY --chown=videobot:videobot bot.py .
COPY --chown=videobot:videobot entrypoint.py .
COPY --chown=videobot:videobot stats_entrypoint.py .
COPY --chown=videobot:videobot downloader ./downloader
COPY --chown=videobot:videobot jobs ./jobs
COPY --chown=videobot:videobot security ./security
COPY --chown=videobot:videobot plugins ./plugins
COPY --chown=videobot:videobot telegram_layer ./telegram_layer
RUN python - <<'PY'
from pathlib import Path
import re
path = Path('bot.py')
text = path.read_text(encoding='utf-8')
marker = 'youtube_smart_extraction_not_applicable'
if marker not in text:
    pattern = r'(?m)^(?P<indent>\s*)smart_file, smart_diagnostics = await download_with_smart_extraction\(\n(?P<body>.*?)(?P=indent)\)'
    matches = list(re.finditer(pattern, text, re.DOTALL))
    if len(matches) != 1:
        raise SystemExit(f'Expected exactly one Smart Extraction call, found {len(matches)}')
    m = matches[0]
    indent = m.group('indent')
    original = m.group(0)
    replacement = (
        f'{indent}if is_youtube:\n'
        f'{indent}    smart_file = None\n'
        f'{indent}    smart_diagnostics = {{\n'
        f'{indent}        "candidate_count": 0,\n'
        f'{indent}        "valid_candidate_count": 0,\n'
        f'{indent}        "skipped": "youtube_smart_extraction_not_applicable",\n'
        f'{indent}    }}\n'
        f'{indent}    print("ℹ️ Skipping generic Smart Extraction fallback for YouTube")\n'
        f'{indent}else:\n'
        + '\n'.join(indent + '    ' + line if line else line for line in original.splitlines())
        + '\n'
    )
    path.write_text(text[:m.start()] + replacement + text[m.end():], encoding='utf-8')
    print('Applied YouTube fallback isolation during image build')
else:
    print('YouTube fallback isolation already present')
PY
RUN test -s /opt/yt-dlp-plugins/yt_dlp_plugins/extractor/threads.py \
    && python -m py_compile bot.py entrypoint.py stats_entrypoint.py downloader/error_reporter.py downloader/smart_search.py downloader/smart_media_bridge.py downloader/smart_media_resolver.py downloader/browser_media_resolver.py downloader/browser_download_handoff.py downloader/shahid4u_resolver.py downloader/smart_learning_foundation.py jobs/download_manager.py security/download_guard.py security/rate_limit.py plugins/manager.py plugins/core_runtime.py plugins/admin_common.py plugins/admin_control_center.py plugins/admin_layer_v2.py plugins/admin_operations_center.py plugins/admin_fallback_intelligence.py plugins/admin_backup_recovery.py plugins/admin_security_center.py plugins/admin_smart_analytics.py plugins/admin_user_history.py plugins/admin_global_history.py plugins/smart_operations.py plugins/recovered_features.py plugins/smart_search_pro.py plugins/user_features.py plugins/yoinku_compat.py plugins/whatsapp_audio.py telegram_layer/media_retry.py
RUN mkdir -p /app/data /app/tmp && chown -R videobot:videobot /app
ENV TMPDIR=/app/tmp
ENV YTDLP_PLUGIN_DIRS=/opt/yt-dlp-plugins
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
ENV ALIBOT_PLUGINS_ENABLED=1
ENV ALIBOT_BROWSER_RESOLVER=1
# Railway volumes mounted at runtime can be root-owned. Start as root only long enough
# to repair the mounted data directory, then drop privileges before running the bot.
USER root
CMD ["/bin/sh", "-c", "chown -R videobot:videobot /app/data && exec gosu videobot python -u entrypoint.py"]
