FROM python:3.12-slim

ARG DENO_VERSION=2.8.3
ARG DENO_SHA256=30455b845ffa6082209c3590269c910ad3b7efdf28c9879afd4006c47ae54197

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --uid 10001 videobot \
    && curl -fsSL "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip" -o /tmp/deno.zip \
    && echo "${DENO_SHA256}  /tmp/deno.zip" | sha256sum -c - \
    && unzip -q /tmp/deno.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/deno \
    && rm -f /tmp/deno.zip \
    && deno --version

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt \
    && mkdir -p /opt/yt-dlp-plugins/yt_dlp_plugins/extractor \
    && python -c "from importlib.metadata import distribution; from pathlib import Path; import shutil; src=Path(distribution('yt-dlp-threads').locate_file('yt_dlp_plugins/extractor/threads.py')); assert src.is_file(), 'yt-dlp-threads extractor file not found'; shutil.copy2(src, '/opt/yt-dlp-plugins/yt_dlp_plugins/extractor/threads.py')" \
    && printf '%s\n' '--plugin-dirs /opt/yt-dlp-plugins' > /etc/yt-dlp.conf

COPY --chown=videobot:videobot bot.py .
COPY --chown=videobot:videobot downloader ./downloader
COPY --chown=videobot:videobot plugins ./plugins
COPY --chown=videobot:videobot tools/patch_runtime_features.py ./tools/patch_runtime_features.py
COPY --chown=videobot:videobot tools/apply_alibot_features.py ./tools/apply_alibot_features.py
COPY --chown=videobot:videobot tools/fix_alibot_ui.py ./tools/fix_alibot_ui.py
COPY --chown=videobot:videobot tools/fix_instructions_back.py ./tools/fix_instructions_back.py
COPY --chown=videobot:videobot tools/fix_admin_broadcast_media.py ./tools/fix_admin_broadcast_media.py
COPY --chown=videobot:videobot tools/fix_instructions_full.py ./tools/fix_instructions_full.py
COPY --chown=videobot:videobot tools/enable_internal_plugins.py ./tools/enable_internal_plugins.py
COPY --chown=videobot:videobot tools/audit_users_feature.py ./tools/audit_users_feature.py
COPY --chown=videobot:videobot tools/fix_admin_control_center.py ./tools/fix_admin_control_center.py

# Validate installation and apply runtime compatibility/features before startup.
RUN test -s /opt/yt-dlp-plugins/yt_dlp_plugins/extractor/threads.py \
    && python -m yt_dlp --list-extractors | grep -i 'Threads' || true

# The feature patcher targets the language dictionaries by their closing delimiter.
# Normalize its legacy anchor before executing it so builds remain deterministic.
RUN python -c "from pathlib import Path; p=Path('tools/apply_alibot_features.py'); s=p.read_text(encoding='utf-8'); old=\"end = text.find('\\\\n    \\\"share\\\":', start)\"; new=\"end = text.find('\\\\n    },', start)\"; assert old in s, 'legacy patcher anchor not found'; p.write_text(s.replace(old, new, 1), encoding='utf-8')"
RUN python tools/patch_runtime_features.py \
    && python tools/apply_alibot_features.py \
    && python tools/fix_alibot_ui.py \
    && python tools/fix_instructions_back.py \
    && python tools/fix_instructions_full.py \
    && python tools/fix_admin_broadcast_media.py \
    && python tools/enable_internal_plugins.py \
    && python tools/audit_users_feature.py \
    && python tools/fix_admin_control_center.py \
    && python -m py_compile bot.py downloader/error_reporter.py plugins/manager.py plugins/core_runtime.py

RUN mkdir -p /app/data /app/tmp && chown -R videobot:videobot /app

ENV TMPDIR=/app/tmp
ENV YTDLP_PLUGIN_DIRS=/opt/yt-dlp-plugins
ENV ALIBOT_PLUGINS_ENABLED=1
USER videobot

CMD ["python", "-u", "bot.py"]
