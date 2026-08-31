FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

RUN echo "=== DOCKER BUILD OK ===" \
    && python --version \
    && ls -lh /app/bot.py

CMD ["sh", "-c", "echo '=== CONTAINER STARTED ==='; echo '=== CHECKING FILES ==='; pwd; ls -la /app; echo '=== STARTING BOT ==='; python -u bot.py"]
