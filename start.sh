#!/usr/bin/env bash
# =============================================================
#  InstaIQ - start backend (FastAPI) + frontend (React/Vite)
#  Usage: ./start.sh
#  Safe to run repeatedly - already-running services are skipped.
# =============================================================
set -u
cd "$(dirname "$0")"

echo ""
echo "  InstaIQ - starting services"
echo "  ---------------------------"

command -v python >/dev/null 2>&1 || { echo "  [error] python not found on PATH"; exit 1; }

port_up() {  # port_up <port> -> 0 if something is listening
  if command -v netstat >/dev/null 2>&1; then
    netstat -ano 2>/dev/null | grep "LISTEN" | grep -Eq ":$1[[:space:]]" && return 0
  fi
  return 1
}

# --- Backend (FastAPI on port 8000) --------------------------
if port_up 8000; then
  echo "  [skip] Backend already running on port 8000"
else
  echo "  [start] Backend: checking dependencies, then launching..."
  python -c "import fastapi, httpx, langchain_core, langchain_openai" >/dev/null 2>&1 \
    || { echo "          Installing backend dependencies (one-time)..."; pip install -r backend/requirements.txt >/dev/null 2>&1; }
  ( cd backend && nohup python -m uvicorn main:app --host 127.0.0.1 --port 8000 </dev/null > uvicorn.log 2>&1 & )
fi

# --- Frontend (React/Vite on port 5173) ----------------------
if port_up 5173; then
  echo "  [skip] React UI already running on port 5173"
else
  if [ ! -d frontend/node_modules ]; then
    echo "  [start] Installing frontend dependencies (one-time)..."
    ( cd frontend && npm install --no-audit --no-fund > npm-install.log 2>&1 )
  fi
  echo "  [start] React UI: launching vite dev server..."
  ( cd frontend && nohup npm run dev </dev/null > vite.log 2>&1 & )
fi

# --- Wait, then verify ---------------------------------------
echo "  [wait]  Giving services a few seconds to boot..."
sleep 10

BACKEND_OK=0; FRONTEND_OK=0
curl -s -m 5 http://127.0.0.1:8000/health 2>/dev/null | grep -q "healthy" && BACKEND_OK=1
[ "$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:5173/ 2>/dev/null)" = "200" ] && FRONTEND_OK=1

echo ""
[ "$BACKEND_OK"  = "1" ] && echo "  [ ok ] Backend    http://localhost:8000   (API docs at /docs)" || echo "  [ !! ] Backend not responding - see backend/uvicorn.log"
[ "$FRONTEND_OK" = "1" ] && echo "  [ ok ] React UI   http://localhost:5173"                               || echo "  [ !! ] React UI not responding - see frontend/vite.log"
echo ""
echo "  Done. Run ./stop.sh to shut both services down."
