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

RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=videobot:videobot bot.py .

RUN mkdir -p /app/data /app/tmp && chown -R videobot:videobot /app

ENV TMPDIR=/app/tmp
USER videobot

CMD ["python", "-u", "bot.py"]
