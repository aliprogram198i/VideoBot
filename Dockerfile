FROM python:3.12-slim

ARG DENO_VERSION=2.8.3

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --uid 10001 videobot \
    && curl -fsSL "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip" -o /tmp/deno.zip \
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
COPY --chown=videobot:videobot tools/patch_runtime_features.py ./tools/patch_runtime_features.py
COPY --chown=videobot:videobot tools/apply_alibot_features.py ./tools/apply_alibot_features.py

# Validate installation and apply runtime compatibility/features before startup.
RUN test -s /opt/yt-dlp-plugins/yt_dlp_plugins/extractor/threads.py \
    && python -m yt_dlp --list-extractors | grep -i 'Threads' || true
RUN python tools/patch_runtime_features.py \
    && python tools/apply_alibot_features.py \
    && python -m py_compile bot.py downloader/error_reporter.py

RUN mkdir -p /app/data /app/tmp && chown -R videobot:videobot /app

ENV TMPDIR=/app/tmp
ENV YTDLP_PLUGIN_DIRS=/opt/yt-dlp-plugins
USER videobot

CMD ["python", "-u", "bot.py"]
