# Root build entry — mirrors backend/Dockerfile with backend/-prefixed paths.
# Platforms that build from the repo root (e.g. a Render service created
# without the Blueprint's dockerfilePath: ./backend/Dockerfile) find this
# file and produce the exact same backend image. The canonical backend
# Dockerfile remains backend/Dockerfile (build context: ./backend).
FROM python:3.11-slim

# Playwright Chromium powers the keyless direct-fetch path (real Instagram
# data fetched from inside a real browser page — no relay, no tokens);
# fonts keep rendered pages natural.
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY backend/ .

EXPOSE 8000
# Render (and most platforms) inject a dynamic PORT env var — bind to it
# when present, 8000 otherwise (local/docker defaults).
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
