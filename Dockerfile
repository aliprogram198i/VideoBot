FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --uid 10001 videobot

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=videobot:videobot bot.py .

RUN mkdir -p /app/data /app/tmp && chown -R videobot:videobot /app

ENV TMPDIR=/app/tmp
USER videobot

CMD ["python", "-u", "bot.py"]
