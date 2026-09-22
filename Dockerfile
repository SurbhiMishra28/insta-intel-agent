# Root build entry — mirrors backend/Dockerfile with backend/-prefixed paths.
# Platforms that build from the repo root (e.g. a Render service created
# without the Blueprint's dockerfilePath: ./backend/Dockerfile) find this
# file and produce the exact same backend image. The canonical backend
# Dockerfile remains backend/Dockerfile (build context: ./backend).
FROM python:3.11-slim

# Chromium powers the keyless direct-fetch fallback (real Instagram data
# when the Apify pool is exhausted); fonts keep rendered pages natural.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

ENV IG_CHROME_PATH=/usr/bin/chromium \
    IG_CHROME_FETCH=true \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

EXPOSE 8000
# Render (and most platforms) inject a dynamic PORT env var — bind to it
# when present, 8000 otherwise (local/docker defaults).
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
