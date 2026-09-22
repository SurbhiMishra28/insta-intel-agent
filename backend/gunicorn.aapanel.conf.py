# aaPanel Python Manager — gunicorn startup config (FastAPI + uvicorn workers)
# Select this file as the gunicorn config when adding the project in
# Python Manager (framework: FastAPI, start mode: gunicorn).
#
# Key points:
#  - app entry is main:app (backend/main.py defines `app = FastAPI(...)`)
#  - 2 workers + 4 threads: fine for 2-core VPS; each worker is a full
#    asyncio loop, so concurrency is high per worker already
#  - timeout 300s: the analyze chain (gateway retries + LLM) can take minutes
#  - bind 127.0.0.1:8000 — the aaPanel site reverse-proxies to this

bind = "127.0.0.1:8000"
workers = 2
worker_class = "uvicorn.workers.UvicornWorker"
threads = 4
timeout = 300
graceful_timeout = 120
keepalive = 5
max_requests = 1000
max_requests_jitter = 100
preload_app = False
accesslog = "-"
errorlog = "-"
loglevel = "info"
