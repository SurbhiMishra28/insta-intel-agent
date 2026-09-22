# Vercel serverless entrypoint: mounts the FastAPI app on the Python runtime.
# The @vercel/python builder auto-detects the module-level ASGI `app` and wraps
# it — no adapter needed. The Vercel project root is backend/ itself, so the
# flat modules (main.py, scraper.py, ...) are bundled at the lambda root
# (/var/task) and /tmp is the only writable directory (DB paths point there;
# storage resets per deployment).
import os
import sys

# api/index.py -> api/ -> project root, where the flat backend modules live.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("PROFILE_CACHE_DB", "/tmp/profile_cache.db")
os.environ.setdefault("SCAN_HISTORY_DB", "/tmp/scan_history.db")

# Backend modules use flat imports (import scraper, import ai_engine, ...).
sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

from main import app  # noqa: E402  (ASGI app auto-detected by Vercel)
