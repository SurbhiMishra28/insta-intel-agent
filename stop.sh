#!/usr/bin/env bash
# =============================================================
#  InstaIQ - stop backend (port 8000) + React UI (port 5173)
#  Usage: ./stop.sh
# =============================================================
cd "$(dirname "$0")"

echo ""
echo "  Stopping InstaIQ..."

killed=0

# Prefer Windows-native process kill when available (Git Bash on Windows).
if command -v netstat >/dev/null 2>&1 && command -v taskkill >/dev/null 2>&1; then
  for port in 8000 5173; do
    pids=$(netstat -ano 2>/dev/null | grep "LISTEN" | grep -E ":$port[[:space:]]" | awk '{print $NF}' | sort -u)
    for pid in $pids; do
      echo "  [stop] Port $port (PID $pid)"
      taskkill //PID "$pid" //F >/dev/null 2>&1
      killed=1
    done
  done
else
  # Unix fallback: kill by port via fuser, then pkill patterns.
  if command -v fuser >/dev/null 2>&1; then
    for port in 8000 5173; do
      if fuser -k "$port"/tcp >/dev/null 2>&1; then
        echo "  [stop] Port $port"
        killed=1
      fi
    done
  else
    pkill -f "uvicorn main:app" >/dev/null 2>&1 && killed=1
    pkill -f "vite" >/dev/null 2>&1 && killed=1
  fi
fi

[ "$killed" = "0" ] && echo "  Nothing was running."
[ "$killed" = "1" ] && echo "  All services stopped."
echo ""
