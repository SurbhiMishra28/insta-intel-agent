# Vercel serverless entrypoint: mounts the FastAPI app on the Python runtime.
# The @vercel/python builder auto-detects the module-level ASGI `app` and wraps
# it — no adapter needed. DB paths move to /tmp (the only writable directory
# on Vercel) BEFORE the backend modules import; storage resets per deployment.
import os
import sys

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))

# /tmp is the only writable location on Vercel's lambda filesystem.
os.environ.setdefault("PROFILE_CACHE_DB", "/tmp/profile_cache.db")
os.environ.setdefault("SCAN_HISTORY_DB", "/tmp/scan_history.db")

# Backend modules use flat imports (import scraper, import ai_engine, ...).
sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

from main import app  # noqa: E402  (ASGI app auto-detected by Vercel)
