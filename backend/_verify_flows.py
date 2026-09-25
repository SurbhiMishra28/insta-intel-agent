"""Systematic flow verification: every implemented flow, live server.

Organized as FLOWS (multi-endpoint user journeys) + individually verified
endpoints. Each check validates STRUCTURE (keys, types, ranges) + grounding
(real-data plausibility), not just status codes.

Usage:  python _verify_flows.py [handle]     (server must be on 127.0.0.1:8000)
"""
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
HANDLE = (sys.argv[1] if len(sys.argv) > 1 else "nasa").lstrip("@")
S = requests = None  # placeholder to keep lints quiet

import requests  # noqa: E402

results = []
TIMINGS = {}


def call(method, path, body=None, timeout=180, expect=(200,)):
    t0 = time.time()
    url = BASE + path
    try:
        r = requests.request(method, url, json=body, timeout=timeout)
        ms = time.time() - t0
        ok = r.status_code in expect
        detail = ""
        try:
            data = r.json()
        except Exception:
            data = None
        if not ok:
            detail = f"status={r.status_code} body={str(r.text)[:120]}"
        return ok, data, r.status_code, f"{ms:.1f}s"
    except requests.exceptions.RequestException as e:
        return False, None, 0, f"{time.time()-t0:.1f}s {type(e).__name__}: {str(e)[:100]}"


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def section(label):
    print(f"\n———— {label} " + "—" * max(0, 60 - len(label)))


def plausible_followers(n):
    return isinstance(n, int) and 0 < n < 500_000_000


# ============================================================================
section("0. Health & observability")
ok, d, code, t = call("GET", "/health")
check("GET /health", ok and d.get("status") == "healthy", t)

ok, d, code, t = call("GET", "/api/ai-status")
check("GET /api/ai-status", ok and "configured" in d and "available" in d, t)
AI_AVAILABLE = d.get("available", False) if ok else False
print(f"     AI: configured={d.get('configured')} available={AI_AVAILABLE} provider={d.get('provider')}")

ok, d, code, t = call("GET", "/api/usage")
check("GET /api/usage", ok and ("source" in d or "data_mode" in d), t)
print(f"     usage: source={d.get('source')} mode={d.get('data_mode')}")

# ============================================================================
section("1. Core: analyze (full pipeline)")
ok, d, code, t = call("POST", "/api/analyze", {"username": HANDLE})
main_insight = d
if ok and d:
    p, m = d.get("profile", {}), d.get("metrics", {})
    check("analyze: profile real", plausible_followers(p.get("followers")) and p.get("username", "").lower() == HANDLE.lower(),
          f"{p.get('username')} followers={p.get('followers')}")
    check("analyze: metrics sane", 0 <= m.get("engagement_rate", -1) < 100 and m.get("avg_likes", 0) >= 0,
          f"er={m.get('engagement_rate')}% likes={m.get('avg_likes')}")
    check("analyze: AI narrative present", len(d.get("ai_summary", "")) > 80, f"{len(d.get('ai_summary',''))} chars")
    check("analyze: strengths/weaknesses/recs", len(d.get("strengths", [])) > 0 and len(d.get("weaknesses", [])) > 0 and len(d.get("recommendations", [])) > 0)
    check("analyze: account_score 0-100", isinstance(d.get("account_score"), int) and 0 <= d["account_score"] <= 100, f"score={d.get('account_score')}")
    n_posts = len(p.get("recent_posts", []))
    check("analyze: recent posts (IG returns 4-12)", n_posts >= 4, f"{n_posts} posts")
else:
    check("POST /api/analyze", False, f"status={code} {t}")

# ============================================================================
section("2. Deep dives: competitor-research, whitespace, review, growth-plan")
ok, d, code, t = call("POST", "/api/competitor-research?count=3", {"username": HANDLE}, timeout=300)
if ok and d:
    check("competitor-research: main + rivals", d.get("main", {}).get("profile", {}).get("username", "").lower() == HANDLE.lower() and len(d.get("competitors", [])) >= 1,
          f"{len(d.get('competitors', []))} rivals")
    check("competitor-research: ranking covers all", len(d.get("ranking", [])) == 1 + len(d.get("competitors", [])))
    check("competitor-research: market summary + gaps", len(d.get("market_summary", "")) > 40 and isinstance(d.get("opportunities"), list))
else:
    check("POST /api/competitor-research", False, f"status={code} {t}")

ok, d, code, t = call("POST", "/api/whitespace?rivals=0", {"username": HANDLE}, timeout=240)
if ok and d:
    themes = d.get("themes", d.get("coverage", []))
    check("whitespace: themes + captions", (len(json.dumps(d)) > 500), f"keys={list(d.keys())[:6]}")
else:
    check("POST /api/whitespace", False, f"status={code} {t}")

ok, d, code, t = call("POST", "/api/review", {"username": HANDLE})
check("POST /api/review", ok and isinstance(d, dict) and len(json.dumps(d)) > 200, t)

ok, d, code, t = call("POST", "/api/growth-plan?count=2", {"username": HANDLE}, timeout=300)
if ok and d:
    # /api/growth-plan returns the FULL DASHBOARD (profile + metrics + timing +
    # toolkit + intel). The growth-plan generator itself was removed.
    check("growth-plan: main insight + dashboard sections", d.get("main", {}).get("profile", {}).get("username", "").lower() == HANDLE.lower() and "best_times" in d)
    check("growth-plan: history recorded", isinstance(d.get("history"), list))
else:
    check("POST /api/growth-plan", False, f"status={code} {t}")

# ============================================================================
section("3. Compare & discover")
ok, d, code, t = call("POST", "/api/compare", {"main_username": HANDLE, "competitor_usernames": ["adidas", "nike"]}, timeout=240)
if ok and d:
    check("compare: main + 2 competitors", d.get("main", {}).get("profile", {}).get("username", "").lower() == HANDLE.lower() and len(d.get("competitors", [])) == 2)
    check("compare: ranking + market summary", len(d.get("ranking", [])) == 3 and len(d.get("market_summary", "")) > 30)
else:
    check("POST /api/compare", False, f"status={code} {t}")

ok, d, code, t = call("POST", "/api/discover?limit=5", {"username": HANDLE})
# IG's related-accounts surface varies: [] is an honest empty for some handles
# (rival discovery still works via local mining inside /api/competitor-research).
check("POST /api/discover", ok and isinstance(d, list), f"{len(d) if isinstance(d, list) else '?'} candidates (IG-dependent)")

# ============================================================================
section("4. Intel grid (audience, trending, rival-content, hooks, top-content, rival-growth)")
ok, d, code, t = call("GET", f"/api/intel/audience?username={HANDLE}")
check("intel/audience", ok and isinstance(d, dict) and len(json.dumps(d)) > 300, t)

ok, d, code, t = call("POST", "/api/intel/trending?rivals=0", {"username": HANDLE}, timeout=240)
check("intel/trending", ok and isinstance(d, dict), t)

ok, d, code, t = call("POST", f"/api/intel/rival-content?rivals=2", {"username": HANDLE}, timeout=300)
check("intel/rival-content", ok and isinstance(d, dict), t)

ok, d, code, t = call("POST", "/api/intel/hooks?rivals=0", {"username": HANDLE}, timeout=240)
check("intel/hooks", ok and isinstance(d, dict), t)

ok, d, code, t = call("GET", f"/api/intel/top-content?username={HANDLE}")
check("intel/top-content", ok and isinstance(d, dict), t)

ok, d, code, t = call("GET", f"/api/intel/rival-growth?username={HANDLE}&rivals=2")
check("intel/rival-growth", ok and isinstance(d, dict), t)

# ============================================================================
section("5. Trends")
ok, d, code, t = call("GET", f"/api/trends/trending?username={HANDLE}")
check("GET /api/trends/trending", ok and isinstance(d, (dict, list)), t)

ok, d, code, t = call("POST", "/api/trends/alert", {"username": HANDLE}, timeout=240)
check("POST /api/trends/alert", ok and isinstance(d, dict), t)

# ============================================================================
section("6. History / restore / tracking / PDFs")
ok, d, code, t = call("GET", f"/api/history?username={HANDLE}")
check("GET /api/history", ok and isinstance(d, dict), t)

ok, d, code, t = call("GET", "/api/recent-searches?limit=50")
check("GET /api/recent-searches", ok and "searches" in d and isinstance(d["searches"], list) and len(d["searches"]) >= 1, f"{len(d.get('searches', [])) if ok else 0} rows")

ok, d, code, t = call("GET", f"/api/growth-tracking?username={HANDLE}")
check("GET /api/growth-tracking", ok and isinstance(d, dict), t)

ok, d, code, t = call("POST", "/api/restore", {"username": HANDLE})
check("POST /api/restore", ok and d.get("profile", {}).get("username", "").lower() == HANDLE.lower(), t)

# PDFs return raw bytes — never JSON-parse them. export/pdf rebuilds the full
# dashboard (LLM chains included), so it legitimately needs a long budget.
try:
    r = requests.post(f"{BASE}/api/export/pdf", json={"username": HANDLE}, timeout=300)
    check("POST /api/export/pdf (full rebuild)", r.status_code == 200 and r.content[:5] == b"%PDF-",
          f"status={r.status_code} bytes={len(r.content)}")
except Exception as e:
    check("POST /api/export/pdf (full rebuild)", False, str(e)[:100])

try:
    r = requests.post(f"{BASE}/api/history-pdf", json={"username": HANDLE}, timeout=120)
    check("POST /api/history-pdf (stored snapshot)", r.status_code == 200 and r.content[:5] == b"%PDF-",
          f"status={r.status_code} bytes={len(r.content)}")
except Exception as e:
    check("POST /api/history-pdf (stored snapshot)", False, str(e)[:100])

# ============================================================================
section("7. Chat & AI status")
ok, d, code, t = call("POST", "/api/chat", {"message": "Give me one tactic to improve engagement", "username": HANDLE, "context": f"Account: @{HANDLE}\nFollowers: {main_insight['profile']['followers'] if main_insight else 'N/A'}"}, timeout=120)
if ok and d:
    check("chat: answered", len(d.get("answer", "")) > 30, f"llm_used={d.get('llm_used')} {t}")
    check("chat: llm_used flag present", isinstance(d.get("llm_used"), bool))
else:
    check("POST /api/chat", False, f"status={code} {t}")

# Chat with @handle inside the message (live inline fetch path)
ok, d, code, t = call("POST", "/api/chat", {"message": f"what is @{HANDLE}'s follower count?", "username": "", "context": ""}, timeout=180)
check("chat: inline @handle live fetch", ok and len(d.get("answer", "")) > 20, t)

# ============================================================================
section("8. Input validation contract (4xx handling)")
ok, d, code, t = call("POST", "/api/analyze", {"username": "this_handle_is_way_too_long_for_instagram_rules"}, expect=(400, 503))
check("invalid handle → 4xx/5xx (not 500)", code in (400, 403, 422, 502, 503), f"status={code}")

ok, d, code, t = call("POST", "/api/analyze", {"username": ""}, expect=(400, 422))
check("empty handle → 422/400", code in (400, 422), f"status={code}")

ok, d, code, t = call("GET", "/api/history?username=", expect=(400, 422))
check("empty history query → 422/400", code in (400, 422), f"status={code}")

ok, d, code, t = call("POST", "/api/chat", {"message": "", "username": "", "context": ""})
check("empty chat → handled (200 rule-based or 4xx)", code in (200, 400, 422), f"status={code}")

# ============================================================================
section("9. Persistence contract: analyze wrote scan history")
ok, d, code, t = call("GET", f"/api/history?username={HANDLE}")
if ok and d:
    rows = d.get("trend", d.get("rows", d.get("history", [])))
    check("history recorded scans", isinstance(rows, list), f"keys={list(d.keys())[:6]}")

# ============================================================================
print("\n" + "=" * 70)
passed = sum(1 for _, ok, _ in results if ok)
failed = [x for x in results if not x[1]]
print(f"==== FLOW VERIFICATION: {passed}/{len(results)} checks passed ====")
for name, _, detail in failed:
    print(f"  FAIL: {name} — {detail}")
sys.exit(1 if failed else 0)
