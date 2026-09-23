"""Poll until the NVIDIA NIM outage ends, then confirm LLM chat end-to-end.

Two layers:
  1. RECOVERY PROBE — a raw, minimal chat call to integrate.api.nvidia.com
     (bypasses the app's circuit breaker, so recovery is detected the moment
     NIM answers instead of waiting out the escalating breaker cooldown).
  2. APP CONFIRMATION — once NIM answers twice in a row, wait for the app's
     breaker to close (poll /api/ai-status), then POST /api/chat and require
     llm_used: true.

Usage:
  python _poll_llm_recovery.py              # poll every 60s indefinitely
  python _poll_llm_recovery.py --every 30   # faster polling
  python _poll_llm_recovery.py --once       # single probe + status print
"""
import argparse
import json
import os
import sys
import time

import httpx
from dotenv import load_dotenv

BACKEND = os.getenv("POLL_BACKEND_URL", "http://127.0.0.1:8000")
POLL_MODEL = "openai/gpt-oss-20b"  # the app's primary model
CONFIRM_STREAK = 2                 # consecutive OK probes before confirming


def _load_key() -> str:
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    return os.getenv("LLM_API_KEY", "").strip()


def probe_nim(key: str) -> str:
    """Raw minimal chat call. Returns 'ok', 'down', or 'auth'."""
    try:
        r = httpx.post(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": POLL_MODEL,
                "messages": [{"role": "user", "content": "Say OK"}],
                "max_tokens": 10,
                "temperature": 0,
                "stream": False,
            },
            timeout=45,
        )
        if r.status_code == 200 and (r.json().get("choices") or [{}])[0].get("message", {}).get("content"):
            return "ok"
        if r.status_code in (401, 403):
            return "auth"
        return "down"
    except Exception:
        return "down"


def app_status() -> dict:
    try:
        r = httpx.get(f"{BACKEND}/api/ai-status", timeout=15)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def confirm_chat() -> bool:
    """POST /api/chat and require llm_used: true with a non-generic answer."""
    try:
        r = httpx.post(
            f"{BACKEND}/api/chat",
            json={
                "message": "In one sentence: what should a coffee shop post on Instagram this week?",
                "username": "",
                "context": "",
            },
            timeout=120,
        )
        d = r.json()
        used = bool(d.get("llm_used"))
        answer = str(d.get("answer") or "")
        print(f"    chat: llm_used={used} | answer[:100]={answer[:100]!r}")
        return used and len(answer) > 40
    except Exception as e:
        print(f"    chat request failed: {type(e).__name__}: {str(e)[:100]}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=60.0, help="seconds between probes")
    ap.add_argument("--once", action="store_true", help="single probe, then exit")
    ap.add_argument("--max-wait", type=float, default=0.0, help="give up after N seconds (0 = never)")
    args = ap.parse_args()

    key = _load_key()
    if not key:
        print("[poll] LLM_API_KEY not set — cannot probe NIM")
        return 2

    print(f"[poll] probing {POLL_MODEL} every {args.every:.0f}s; app at {BACKEND}")
    streak = 0
    start = time.monotonic()
    while True:
        status = app_status()
        breaker = "OPEN" if time.monotonic() < (status.get("breaker_open_until") or 0) else "closed"
        result = probe_nim(key)
        stamp = time.strftime("%H:%M:%S")
        if result == "ok":
            streak += 1
            print(f"[{stamp}] NIM probe: OK ({streak}/{CONFIRM_STREAK}) | app breaker: {breaker}")
            if streak >= CONFIRM_STREAK:
                if breaker == "OPEN":
                    wait = max(0.0, (status.get("breaker_open_until") or 0) - time.monotonic())
                    print(f"[poll] NIM recovered — waiting {wait:.0f}s for the app breaker to close…")
                    time.sleep(min(wait + 2, 330))
                print("[poll] CONFIRMING through the app now:")
                if confirm_chat():
                    print("[poll] SUCCESS — LLM is answering through the app again (llm_used=true)")
                    return 0
                print("[poll] NIM answered but the app still fell back — keeping the poll alive")
                streak = 0
        elif result == "auth":
            print(f"[{stamp}] NIM probe: AUTH FAILED (401/403) — key problem, not an outage")
            return 3
        else:
            if streak:
                print(f"[{stamp}] NIM probe: down again")
            else:
                print(f"[{stamp}] NIM probe: down (timeout/no data)")
            streak = 0

        if args.once:
            print("[poll] --once: exiting after a single probe")
            return 1
        if args.max_wait and time.monotonic() - start > args.max_wait:
            print(f"[poll] gave up after {args.max_wait:.0f}s — NIM still down")
            return 4
        time.sleep(args.every)


if __name__ == "__main__":
    sys.exit(main())
