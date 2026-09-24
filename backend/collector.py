"""InstaIQ collector — fetch current Instagram data, store it locally, then let
the AI agent (NVIDIA NIM / OpenRouter via ai_engine) analyze the cached data.

Standalone CLI that reuses the backend's exact provider stack, so data quality
and caching behave identically to the app:

    1. FETCH    scraper.get_profile(handle)
                memory cache → SQLite disk cache → live providers
                (Graph API → Apify → keyless Playwright Chromium/HTTP ladder)
    2. STORE    • backend/profile_cache.db   (app cache — written by the fetch)
                • backend/scan_history.db    (timeline point via storage.record_metrics)
                • data/collect/<handle>/<date>.json  (portable collector archive)
    3. ANALYZE  ai_engine.analyze_profile(profile) — LLM narrative when
                LLM_API_KEY is configured, deterministic rule-based otherwise.

Usage:
    python collector.py nasa                     # one handle
    python collector.py @nasa github spotify     # several handles (@ and URLs accepted)
    python collector.py nasa --no-ai             # collect + store only (skip LLM)
    python collector.py --list                   # show collected handles
    python collector.py nasa --out report.json   # custom archive location

Everything is real data: the collector never invents numbers, and failures are
honest (non-existent handle → exit 1 with the backend's ValueError message;
every provider blocked → exit 2). Zero new dependencies.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

# Make `import scraper/ai_engine/storage` work from anywhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scraper  # noqa: E402  (also loads backend/.env — LLM keys etc.)
import ai_engine  # noqa: E402
import storage  # noqa: E402

ARCHIVE_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.getenv("COLLECTOR_DATA_DIR", "data"), "collect"
)


def _archive_path(handle: str) -> str:
    return os.path.join(ARCHIVE_ROOT, handle, f"{datetime.now():%Y-%m-%d}.json")


def collect_one(handle: str, use_ai: bool = True) -> dict:
    """Fetch → store → analyze one handle. Raises on honest fetch failure."""
    uname = scraper.normalize_username(handle)
    print(f"\n[{uname}] fetching (cache → live providers)…")
    profile = asyncio.run(scraper.get_profile(uname))  # ValueError/RuntimeError propagate honestly
    metrics = ai_engine.compute_metrics(profile)

    # --- Store ------------------------------------------------------------
    # Timeline point in the app's SQLite store (same rows the UI reads).
    try:
        storage.record_metrics(
            uname,
            followers=profile.followers,
            engagement_rate=metrics.engagement_rate,
            avg_likes=metrics.avg_likes,
            posting_frequency_per_week=metrics.posting_frequency_per_week,
            posts_count=profile.posts_count,
            avg_comments=metrics.avg_comments,
        )
    except Exception as e:  # storage must not kill a collection run
        print(f"[{uname}] warn: timeline store failed: {str(e)[:120]}")

    archive = _archive_path(uname)
    os.makedirs(os.path.dirname(archive), exist_ok=True)

    # --- Analyze ----------------------------------------------------------
    insight = None
    if use_ai:
        print(f"[{uname}] analyzing with {'LLM (' + ai_engine.LLM_MODEL + ')' if ai_engine.LLM_API_KEY else 'rule-based engine (no LLM key)'}…")
        try:
            # _LLM_LAST_ERROR is cleared on LLM success and set on failover,
            # so it tells us honestly which engine wrote the narrative.
            insight = ai_engine.analyze_profile(profile)
            engine = (
                "llm"
                if ai_engine.LLM_API_KEY and not getattr(ai_engine, "_LLM_LAST_ERROR", "x")
                else "rule-based"
            )
        except Exception as e:
            print(f"[{uname}] warn: AI analysis failed ({str(e)[:120]}) — storing metrics only")
            insight, engine = None, "failed"

    snapshot = {
        "handle": uname,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": json.loads(profile.model_dump_json()),
        "metrics": json.loads(metrics.model_dump_json()),
        "insight": json.loads(insight.model_dump_json()) if insight is not None else None,
    }
    with open(archive, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2, default=str)

    # --- Digest -----------------------------------------------------------
    print(f"[{uname}] ✔ collected — {profile.followers:,} followers · "
          f"ER {metrics.engagement_rate}% · {metrics.posting_frequency_per_week}/wk · "
          f"best format: {metrics.best_content_type or 'n/a'}")
    if insight is not None:
        summary = (insight.ai_summary or "").strip().replace("\n", " ")
        print(f"[{uname}] AI ({engine}): {summary[:220]}{'…' if len(summary) > 220 else ''}")
    print(f"[{uname}] stored: {archive}")
    scraper.record_fetch_event("ok", f"collector archived @{uname} ({profile.followers:,} followers)")
    return snapshot


def list_collected() -> None:
    if not os.path.isdir(ARCHIVE_ROOT):
        print("Nothing collected yet — run: python collector.py <handle>")
        return
    for handle in sorted(os.listdir(ARCHIVE_ROOT)):
        d = os.path.join(ARCHIVE_ROOT, handle)
        files = sorted(f for f in os.listdir(d) if f.endswith(".json"))
        if not files:
            continue
        try:
            with open(os.path.join(d, files[-1]), encoding="utf-8") as f:
                snap = json.load(f)
            p, m = snap.get("profile", {}), snap.get("metrics", {})
            print(f"@{handle:<24} {p.get('followers', 0):>12,} followers · "
                  f"ER {m.get('engagement_rate', '?')}% · last: {files[-1]}")
        except Exception:
            print(f"@{handle:<24} (unreadable archive)")


def main() -> int:
    ap = argparse.ArgumentParser(description="InstaIQ collector: fetch → store → AI analyze")
    ap.add_argument("handles", nargs="*", help="Instagram handle(s), @handle or profile URLs")
    ap.add_argument("--no-ai", action="store_true", help="collect + store only (skip AI analysis)")
    ap.add_argument("--list", action="store_true", help="list collected handles and exit")
    args = ap.parse_args()

    if args.list:
        list_collected()
        return 0
    if not args.handles:
        ap.print_help()
        return 0

    results, failures = [], []
    for h in args.handles:
        try:
            results.append(collect_one(h, use_ai=not args.no_ai))
        except ValueError as e:
            failures.append((h, str(e)[:160]))
            print(f"[{h}] ✘ does not exist / unusable: {str(e)[:140]}")
        except (RuntimeError, Exception) as e:  # provider blocked, network, …
            failures.append((h, str(e)[:160]))
            print(f"[{h}] ✘ fetch failed: {str(e)[:140]}")
            scraper.record_fetch_event("fail", f"collector @{h}: {str(e)[:120]}")

    print(f"\n==== collected {len(results)}/{len(results) + len(failures)} ====")
    for h, err in failures:
        print(f"  failed: @{h} — {err}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted — partial archives are kept")
        sys.exit(130)
