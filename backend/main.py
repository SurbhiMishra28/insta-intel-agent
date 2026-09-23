import os
import asyncio
import sys as _sys
from concurrent.futures import ThreadPoolExecutor

# Windows consoles default to a cp1252 stream that cannot encode characters
# common in Instagram content (U+202F etc.) — a failing diagnostic print must
# never take down a request.
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from fastapi import FastAPI, HTTPException, Query, Response
from types import SimpleNamespace
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load .env BEFORE importing modules that read env vars at import time.
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

import ai_engine
import analytics
import intel
import scraper
import storage
from typing import Optional

# LLM chain invocations are blocking network calls; running them off the
# event loop lets multiple rivals' narratives compute CONCURRENTLY instead
# of stacking sequentially (N rivals x ~8s sequential -> ~one chain's time).
_LLM_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm")


async def _analyze_one(profile):
    """One blocking LLM narrative, off the event loop. Memoized in ai_engine,
    so repeat analyses of the same data are free."""
    return await asyncio.get_event_loop().run_in_executor(
        _LLM_POOL, ai_engine.analyze_profile, profile
    )


def _analyze_fast(profiles):
    """Instant rule-based insights for rivals — no LLM round-trips. Rival
    quality shows through their METRICS (ER, cadence, followers), which the
    rule-based path computes identically; the ~20s LLM narrative per rival
    is the single biggest latency cost on the free NVIDIA tier."""
    return [ai_engine.analyze_profile_fast(p) for p in profiles]


async def _llm_call(fn, *args):
    """Run a blocking LLM chain builder (market research, growth plan) off
    the event loop so the server stays responsive while it thinks."""
    return await asyncio.get_event_loop().run_in_executor(_LLM_POOL, fn, *args)

from pydantic import BaseModel, Field

from models import (
    AnalyzeRequest,
    AudienceAnalysis,
    ProfileData,
    CompareRequest,
    CompareResponse,
    CompetitorContentAnalysis,
    CompetitorResearchResponse,
    DiscoveredCompetitor,
    RivalGrowthResponse,
    GrowthPlanResponse,
    HashtagResearch,
    HashtagStat,
    HashtagSuggestionResult,
    MonthlyReviewResponse,
    ProfileInsight,
    ReelTiming,
    ScanRecord,
    ScoreExplanation,
    TrendInfo,
    TrendAlert,
    TrendsResponse,
    TrendSuggestion,
    TopContentResponse,
    ViralHooksResponse,
    WhitespaceResponse,
    TrendingTopicsResponse,
)

app = FastAPI(
    title="AI Instagram Profile & Competitor Intelligence Agent",
    description=(
        "Analyzes an Instagram profile, auto-discovers and researches its "
        "strongest competitors, and produces an AI-written competitive "
        "intelligence report. All profile data is REAL — fetched via the "
        "official Graph API (when configured), Apify (when configured), or "
        "the keyless Playwright Chromium headless provider. Unknown handles "
        "return an honest 400, never simulated numbers."
    ),
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this to your frontend's origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "insta-intel-agent",
        "data_mode": "live (real data only)",
        "data_source": "Graph API / Apify / keyless Playwright Chromium + local cache",
        "ai_engine": ai_engine.ai_provider_label(),
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


async def _load_real_history_profile(uname: str):
    """Best available REAL snapshot for a previously searched handle:
    fresh disk row → any non-simulated disk row (≤30 days) → keyless
    Instagram GraphQL refresh (Chrome page fetch, no Apify spend).
    Returns (profile | None, source). NEVER returns simulated data."""
    profile = await asyncio.to_thread(scraper._disk_profile_get, uname)
    if profile is None or getattr(profile, "data_age_hours", None) == -1:
        stale = await asyncio.to_thread(scraper._disk_profile_get_any, uname)
        if stale is not None and getattr(stale, "data_age_hours", None) != -1:
            profile = stale
    if profile is not None and getattr(profile, "data_age_hours", None) == -1:
        profile = None  # never serve a simulated row

    if profile is not None:
        try:
            gql = await scraper._fetch_graphql_profile(uname)
        except Exception:
            gql = None
        if gql is not None:
            await asyncio.to_thread(scraper._disk_profile_set, uname, gql)
            return gql, "instagram graphql"
        return profile, "stored snapshot"

    try:
        gql = await scraper._fetch_graphql_profile(uname)
    except Exception:
        gql = None
    if gql is not None:
        await asyncio.to_thread(scraper._disk_profile_set, uname, gql)
        return gql, "instagram graphql"
    return None, "unavailable"


@app.post("/api/restore")
async def restore(req: AnalyzeRequest):
    """Restore a previously searched account (search-history restore).

    REAL DATA ONLY: the handle must exist in the agent's recorded scan
    history (scan_history.db) — accounts never searched through the agent
    are refused with 404. The stored snapshot of the account (profile_cache
    .db, always real fetched data) is served instantly and enriched with a
    keyless Instagram GraphQL refresh (Chrome page fetch) when available;
    Apify credits are never spent on a restore. If both the stored snapshot
    and the GraphQL refresh are unavailable the request fails honestly —
    simulated data is never served."""
    try:
        uname = scraper.normalize_username(req.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Real-searched-accounts only: must exist in recorded scan history.
    records, _prev = storage.get_history(uname)
    if not records:
        raise HTTPException(
            status_code=404,
            detail=(f"@{uname} was never searched by the agent — nothing to "
                    "restore. Analyze the handle first to record a search."),
        )

    profile, refreshed_via = await _load_real_history_profile(uname)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=(f"@{uname} has a recorded search but no stored real "
                    "snapshot could be served and the live GraphQL fetch "
                    "did not return data. Analyze the handle normally."),
        )

    insight = await _analyze_one(profile)
    storage.record_scan(insight)  # the restore is itself a recorded search
    return {
        "profile": insight.profile,
        "metrics": insight.metrics,
        "restored_from_history": True,
        "data_age_hours": profile.data_age_hours,
        "refreshed_via": refreshed_via,
        "scan_count": len(records),
    }


def _render_history_data_html(profile, metrics, records, source) -> str:
    """Printable HTML for one previously searched account's FULL stored
    Instagram data: profile, metrics, every stored post and the recorded
    scan history. Light theme, same styling as the main report."""
    p, m = profile, metrics
    posts = p.recent_posts or []
    now = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    def _post_date(post):
        if getattr(post, "posted_at", None):
            try:
                return str(post.posted_at)[:10]
            except Exception:
                return "—"
        d = getattr(post, "posted_days_ago", None)
        return f"{d}d ago" if d is not None else "—"

    def _views(post):
        v = getattr(post, "views", None)
        return f"{v:,}" if isinstance(v, int) and v > 0 else "hidden"

    rows = "".join(
        f"<tr><td>{_esc(_post_date(x))}</td>"
        f"<td>{_esc(getattr(x, 'media_type', '') or 'post')}</td>"
        f"<td>{getattr(x, 'likes', 0):,}</td>"
        f"<td>{getattr(x, 'comments', 0):,}</td>"
        f"<td>{_views(x)}</td>"
        f"<td>{_esc(' '.join(((getattr(x, 'caption', '') or ''))[:180].split()))}"
        f"{'…' if len((getattr(x, 'caption', '') or '')) > 180 else ''}</td></tr>"
        for x in posts
    ) or '<tr><td colspan="6" class="muted">No posts stored for this account.</td></tr>'

    scan_rows = "".join(
        f"<tr><td>{_esc(str(r.scanned_at)[:19].replace('T', ' '))}</td>"
        f"<td>{_km(r.followers)}</td><td>{r.engagement_rate}%</td>"
        f"<td>{r.avg_likes:,.0f}</td><td>{r.posting_frequency_per_week}</td>"
        f"<td>{('+' if r.followers_delta > 0 else '') + _km(r.followers_delta)}</td></tr>"
        for r in records
    )

    age = getattr(p, "data_age_hours", None)
    age_note = (
        f"data age ~{age:.0f}h" if isinstance(age, (int, float)) and age >= 0 else ""
    )

    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>
@page {{ size: A4; margin: 14mm 12mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', Calibri, Arial, sans-serif; color: #1a1f24;
        font-size: 10.5pt; line-height: 1.55; margin: 0; }}
h1 {{ font-size: 20pt; margin: 0 0 4px; }}
h2 {{ font-size: 13pt; color: #141a2e; border-bottom: 2px solid #16a34a;
     padding-bottom: 4px; margin: 26px 0 10px; page-break-after: avoid; }}
.cover {{ border-bottom: 3px solid #16a34a; padding-bottom: 14px; margin-bottom: 6px; }}
.cover .tag {{ color: #4b5563; font-size: 10pt; margin: 0 0 10px; }}
.stats {{ display: flex; flex-wrap: wrap; gap: 10px 26px; margin: 10px 0 2px; }}
.stat b {{ display: block; font-size: 15pt; }}
.stat span {{ color: #4b5563; font-size: 8.5pt; text-transform: uppercase; letter-spacing: .04em; }}
.pill {{ display: inline-block; background: #16a34a; color: #fff; border-radius: 999px;
        padding: 2px 10px; font-size: 8.5pt; font-weight: 700; margin-left: 8px; }}
table {{ border-collapse: collapse; width: 100%; margin: 6px 0; }}
th, td {{ text-align: left; padding: 5px 8px; border-bottom: 1px solid #e5e7eb;
         font-size: 9.5pt; vertical-align: top; }}
th {{ color: #4b5563; font-size: 8.5pt; text-transform: uppercase; letter-spacing: .04em; }}
.muted {{ color: #4b5563; }}
.note {{ background: #fef3c7; border-left: 3px solid #d97706; padding: 8px 12px;
         font-size: 9.5pt; margin: 8px 0; }}
footer {{ margin-top: 26px; color: #6b7280; font-size: 8.5pt; border-top: 1px solid #e5e7eb; padding-top: 8px; }}
</style></head><body>
<div class="cover">
  <h1>@{_esc(p.username)}{_esc(' · ' + p.full_name) if p.full_name else ''}
      <span class="pill">RESTORED ACCOUNT</span>
      {'' if not p.is_verified else '<span class="pill">VERIFIED</span>'}
  </h1>
  <p class="tag">InstaIQ · Stored account data · restored from the agent's search history · {now}</p>
  <p class="muted">{_esc(p.bio or '')}</p>
  <div class="stats">
    <div class="stat"><b>{_km(p.followers)}</b><span>Followers</span></div>
    <div class="stat"><b>{p.following:,}</b><span>Following</span></div>
    <div class="stat"><b>{p.posts_count:,}</b><span>Posts</span></div>
    <div class="stat"><b>{m.engagement_rate}%</b><span>Engagement rate</span></div>
    <div class="stat"><b>{m.avg_likes:,.0f}</b><span>Avg likes / post</span></div>
    <div class="stat"><b>{m.avg_comments:,.1f}</b><span>Avg comments / post</span></div>
    <div class="stat"><b>{m.posting_frequency_per_week}</b><span>Posts / week</span></div>
    <div class="stat"><b style="text-transform:capitalize">{_esc(m.best_content_type)}</b><span>Top format</span></div>
  </div>
</div>
<div class="note">Data source: {source} (real fetched data only){' · ' + age_note if age_note else ''} · {len(records)} recorded search(es) by the agent.</div>
<h2>Stored posts ({len(posts)})</h2>
<table>
<tr><th>Date</th><th>Type</th><th>Likes</th><th>Comments</th><th>Views</th><th>Caption</th></tr>
{rows}
</table>
<h2>Recorded search history ({len(records)})</h2>
<table>
<tr><th>Scanned at</th><th>Followers</th><th>ER</th><th>Avg likes</th><th>Posts/wk</th><th>Δ Followers</th></tr>
{scan_rows}
</table>
<footer>InstaIQ restores only accounts previously searched by the agent and only real fetched data — simulated content is never included.</footer>
</body></html>"""


@app.post("/api/history-pdf")
async def history_pdf(req: AnalyzeRequest):
    """Download the FULL stored Instagram data of one previously searched
    account as a PDF: profile, metrics, every stored post and the recorded
    scan history. Real accounts only — the handle must exist in the agent's
    scan history; simulated data is never served or printed."""
    from fastapi.responses import JSONResponse
    try:
        uname = scraper.normalize_username(req.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    records, _prev = storage.get_history(uname)
    if not records:
        raise HTTPException(
            status_code=404,
            detail=(f"@{uname} was never searched by the agent — nothing to "
                    "export. Analyze the handle first to record a search."),
        )

    profile, source = await _load_real_history_profile(uname)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=(f"@{uname} has a recorded search but no stored real "
                    "snapshot could be served and the live GraphQL fetch "
                    "did not return data. Analyze the handle normally."),
        )

    metrics = ai_engine.compute_metrics(profile)
    html = _render_history_data_html(profile, metrics, records, source)
    try:
        pdf = await asyncio.get_event_loop().run_in_executor(
            _LLM_POOL, _chrome_print_pdf, html
        )
    except RuntimeError as e:
        return JSONResponse(status_code=503, content={"detail": str(e)})
    except Exception as e:
        return JSONResponse(status_code=502, content={"detail": f"PDF export failed: {e}"})
    filename = f"instaiq-{uname}-history-data.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.delete("/api/history/{username}")
def cut_account(username: str):
    """Cut: forget one account entirely. Removes its recorded search history
    AND its stored profile snapshot/discovery caches. Real accounts that were
    never searched return 404 — there is nothing to cut."""
    try:
        uname = scraper.normalize_username(username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    removed_rows = storage.clear_history(uname)
    purged = scraper.purge_account(uname)
    if removed_rows == 0 and purged["profile_snapshot"] == 0:
        raise HTTPException(status_code=404, detail=f"No stored data for @{uname}.")
    return {
        "ok": True,
        "username": uname,
        "scan_history_rows_removed": removed_rows,
        "profile_snapshots_removed": purged["profile_snapshot"],
        "discovery_caches_removed": purged["discovery_caches"],
    }


@app.get("/api/growth-tracking")
def growth_tracking(username: str = Query(...)):
    """Growth tracking for one handle, built from the agent's stored search
    history: the latest recorded scan vs the previous scan, 1 week ago and
    1 month ago. Every delta is computed from real stored measurements —
    when a baseline doesn't exist yet the response says so honestly."""
    try:
        uname = scraper.normalize_username(username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    comparison = storage.get_growth_comparison(uname)
    records, _previous = storage.get_history(uname)
    comparison["history"] = records
    # Day-scale snapshots (same-day re-analyses collapsed) — what the growth
    # chart and history rows render; raw rows above stay for full detail.
    comparison["snapshots"] = storage.get_daily_snapshots(uname)
    return comparison


@app.get("/api/usage")
def data_source_info():
    """Transparency endpoint: what data source powers the app. Kept at the
    same path the old provider-usage banner called, so the frontend keeps
    working; always returns ok."""
    import sqlite3 as _sq

    cached_profiles = 0
    try:
        with _sq.connect(scraper._CACHE_DB) as conn:
            cached_profiles = conn.execute(
                "SELECT COUNT(*) FROM cache WHERE key LIKE 'profile:%'"
            ).fetchone()[0]
    except Exception:
        pass
    # Live mode is tokenless when no Apify tokens AND no Graph credentials
    # are configured — the app then depends entirely on the keyless direct
    # fetch, which datacenter IPs (Render/Railway/Fly) cannot use because
    # Instagram hard-blocks them. Surface that honestly so "real data is
    # not fetching" reports can be diagnosed from this endpoint alone.
    live = bool(scraper.APIFY_TOKENS) or scraper._has_graph_credentials() or scraper.DIRECT_FETCH_ENABLED
    pool = scraper.apify_token_pool_status()
    healthy_tokens = sum(1 for t in pool if t["status"] == "ready" and not t["benched"])
    if not live:
        live_note = (
            "Live mode fetches real Instagram data with zero credentials via "
            "the keyless Playwright Chromium provider: a real headless browser "
            "opens instagram.com and reads Instagram's own web_profile_info "
            "JSON from inside the page, so no relay/token is involved. "
            "Apify tokens (optional) are used first when configured."
        )
    elif pool and healthy_tokens == 0:
        live_note = (
            "Every Apify token in the pool is benched (credit exhausted or "
            "rejected). Fetches automatically fall back to the keyless direct "
            "Instagram provider (real data, no token needed), so analysis keeps "
            "working. Tokens are retried when their cooldown lapses — or add a "
            "fresh free account's token as APIFY_TOKEN_2 in backend/.env "
            "(every free Apify account gets $5/month)."
        )
    elif len(pool) > 1:
        live_note = (
            f"Real Instagram data is fetched live via the Apify API with "
            f"{len(pool)}-token failover ({healthy_tokens} ready); fetches are "
            "cached locally, so repeat analyses are instant and free. "
            f"AI analysis runs on {ai_engine.ai_provider_label()}."
        )
    else:
        live_note = (
            "Real Instagram data is fetched live via the Apify API and "
            "cached locally, so repeat analyses are instant and free. "
            f"AI analysis runs on {ai_engine.ai_provider_label()}."
        )
    return {
        "ok": True,
        "source": "live" if live else "cache",
        "data_mode": "live (real data only)",
        "ai_provider": ai_engine.ai_provider_label(),
        "cached_profiles": cached_profiles,
        "apify_tokens": pool,
        "note": live_note,
    }


@app.get("/api/history")
def history(username: str = Query(..., min_length=1)):
    """Scan history for an account: trend rows + since-last-scan deltas."""
    try:
        uname = scraper.normalize_username(username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    records, _previous = storage.get_history(uname)
    return {"username": uname, "scans": records, "scan_count": len(records)}


@app.get("/api/recent-searches")
def recent_searches(limit: int = Query(30, ge=1, le=100)):
    """Every account the agent has searched, newest first, with the metrics
    from its latest scan and how many times it was searched. Powers the
    search-history restore in the UI."""
    return {"searches": storage.recent_searches(limit)}


@app.delete("/api/recent-searches/{username}")
def delete_search_history(username: str):
    """Forget one handle's stored search history (all its scan rows)."""
    try:
        uname = scraper.normalize_username(username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    removed = storage.clear_history(uname)
    if removed == 0:
        raise HTTPException(status_code=404, detail=f"No stored searches for @{uname}.")
    return {"ok": True, "username": uname, "removed": removed}


def _data_quality_warning(insight: ProfileInsight) -> str:
    """Human-readable note when the served profile data is an incomplete
    cache row. Simulated data does not exist in this system."""
    p = insight.profile
    problems = []
    if p.followers == 0:
        problems.append("follower count")
    if not p.recent_posts:
        problems.append("recent posts")
    if not problems:
        return ""
    return (
        f"@{p.username}: the cached profile is incomplete "
        f"(no {', '.join(problems)}); metrics for this account may be unreliable."
    )


@app.post("/api/analyze", response_model=ProfileInsight)
async def analyze(req: AnalyzeRequest):
    try:
        profile = await scraper.get_profile(req.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch profile: {e}")

    insight = await _analyze_one(profile)
    storage.record_scan(insight)  # best-effort trend tracking
    return insight


async def _research_competitors(main_username: str, main_profile: ProfileData, count: int):
    """Find + research competitors for the main account.

    Latency-shaped pipeline: discover (cache) -> select via LLM (offloaded;
    needs only the RAW profile) -> fetch rival data (cache) -> ONE parallel
    wave computing the main + every rival's narrative concurrently -> done.
    Returns (main_insight, insights, warnings, candidates_found, rationale).
    """
    fallback_note: Optional[str] = None
    try:
        candidates = await scraper.discover_related_profiles(main_username, limit=30)
    except RuntimeError as e:
        # Live provider unavailable (quota, token, network) — don't dead-end:
        # degrade to cached real rivals instead of failing the request.
        fallback_note = f"Auto-discovery unavailable ({e}) — using accounts already analyzed in this app as approximate rivals."
    except Exception as e:
        fallback_note = f"Auto-discovery failed ({e}) — using accounts already analyzed in this app as approximate rivals."

    if not candidates:
        try:
            main_uname = scraper.normalize_username(main_username)
        except ValueError:
            main_uname = (main_username or "").strip().lstrip("@").lower()
        candidates = await scraper.get_cached_profile_pool(exclude={main_uname}, limit=30)
        if candidates and not fallback_note:
            fallback_note = (
                "No niche-specific competitors could be discovered for this account yet "
                "— showing the closest available rivals from accounts already analyzed "
                "in this app instead. Analyze a few niche rivals once and research will "
                "target them specifically."
            )
        if not candidates:
            fallback_note = (
                f"No competitor candidates for @{main_username} yet — rivals are mined from "
                "accounts already analyzed in this app. Analyze this account and a few "
                "niche rivals once; after that, competitor research runs fully offline "
                "with the NVIDIA AI doing the selection and the analysis over cached real data."
            )

    # Parallelize the two slow steps: rival selection (LLM) and the main
    # account's own narrative (LLM) are independent — run them CONCURRENTLY
    # instead of back-to-back (~saves one full LLM round-trip).
    main_task = asyncio.ensure_future(_analyze_one(main_profile))
    try:
        picked, rationale = await _llm_call(ai_engine.pick_competitors, main_profile, candidates, count)
    except BaseException:
        main_task.cancel()
        raise

    main_insight = await main_task

    # ONE batched fetch for all rivals (cache-aware; big latency win vs N runs).
    try:
        profiles = await scraper.get_profiles_batch(picked)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch competitor profiles: {e}")

    have = [profiles[u.lower()] for u in picked if profiles.get(u.lower()) is not None]
    warnings = [
        f"@{u}: no data returned (profile may be private or unavailable)."
        for u in picked if profiles.get(u.lower()) is None
    ]
    if fallback_note:
        warnings.insert(0, fallback_note)

    # Rivals via the INSTANT rule-based path — their numbers (ER, cadence,
    # followers), which drive ranking and gap analysis, are identical; the
    # ~20s LLM narrative per rival is the single biggest latency cost on the
    # free NVIDIA tier.
    insights = await asyncio.get_event_loop().run_in_executor(_LLM_POOL, _analyze_fast, have)
    for ins in insights:
        w = _data_quality_warning(ins)
        if w:
            warnings.append(w)

    return main_insight, insights, warnings, len(candidates), rationale


@app.post("/api/competitor-research", response_model=CompetitorResearchResponse)
async def competitor_research(req: AnalyzeRequest, count: int = Query(5, ge=1, le=10)):
    """Full pipeline: analyze the main account, auto-find its 5-10 most
    relevant competitors, research each one with real data, and produce the
    market research (gaps, content gaps, opportunities)."""
    try:
        main_profile = await scraper.get_profile(req.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch profile: {e}")

    main_insight, competitor_insights, extra_warnings, candidates_found, rationale = await _research_competitors(
        req.username, main_profile, count
    )
    storage.record_scan(main_insight)  # best-effort trend tracking
    for _ci in competitor_insights:
        storage.record_scan(_ci)
    warnings = [w for w in [_data_quality_warning(main_insight)] + extra_warnings if w]

    if not competitor_insights:
        # Rivals were picked but none returned data — return the main
        # account alone with honest warnings instead of dead-ending.
        return CompetitorResearchResponse(
            main=main_insight,
            competitors=[],
            market_summary="",
            competitive_gaps=[],
            content_gaps=[],
            opportunities=[],
            selection_rationale=rationale or "No rival data available.",
            ranking=[main_insight.profile.username],
            warnings=warnings,
            candidates_found=candidates_found,
        )

    research = await _llm_call(ai_engine.build_market_research, main_insight, competitor_insights)
    ranking = [
        i.profile.username
        for i in sorted([main_insight] + competitor_insights, key=ai_engine.composite_score, reverse=True)
    ]

    return CompetitorResearchResponse(
        main=main_insight,
        competitors=competitor_insights,
        market_summary=research.market_summary,
        competitive_gaps=research.competitive_gaps,
        content_gaps=research.content_gaps,
        opportunities=research.opportunities,
        selection_rationale=rationale,
        ranking=ranking,
        warnings=warnings,
        candidates_found=candidates_found,
    )


@app.post("/api/discover", response_model=list[DiscoveredCompetitor])
async def discover(req: AnalyzeRequest, limit: int = Query(10, ge=1, le=30)):
    """List candidate competitors Instagram surfaces for a handle (cheap —
    one profile fetch, no per-competitor research)."""
    try:
        candidates = await scraper.discover_related_profiles(req.username, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not discover competitors: {e}")

    return [
        DiscoveredCompetitor(
            username=c.get("username", ""),
            full_name=c.get("full_name", ""),
            bio=c.get("bio", ""),
            followers=c.get("followers", 0),
            verified=bool(c.get("verified")),
            private=bool(c.get("private")),
        )
        for c in candidates
    ]


async def _build_full_dashboard(username: str, count: int) -> GrowthPlanResponse:
    """Shared full-dashboard pipeline: fetch → analyze → research rivals →
    build every section. /api/growth-plan and /api/export/pdf BOTH run this,
    so the downloadable PDF always contains exactly the analysis the UI
    shows — one source of truth, no duplicated logic."""
    req = SimpleNamespace(username=username)  # shape-compatible with AnalyzeRequest
    try:
        main_profile = await scraper.get_profile(req.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch profile: {e}")

    main_insight = await _analyze_one(main_profile)
    warnings = [_data_quality_warning(main_insight)]

    rivals: list = []
    if count > 0:
        try:
            candidates = await scraper.discover_related_profiles(req.username, limit=20)
        except Exception:
            candidates = []  # plan still works from the account's own data
        if candidates:
            picked, _ = await _llm_call(ai_engine.pick_competitors, main_insight.profile, candidates, count)
            try:
                profiles = await scraper.get_profiles_batch(picked)
            except Exception:
                profiles = {}
            items = list(profiles.items())
            # Instant rule-based narratives for rivals — metrics identical,
            # skips one ~20s LLM round-trip per rival on the free NVIDIA tier.
            analyzed = await asyncio.gather(
                *(asyncio.get_event_loop().run_in_executor(_LLM_POOL, _analyze_fast, [p]) for _u, p in items)
            )
            by_user = {u.lower(): a[0] for (u, _p), a in zip(items, analyzed)}
            for uname in picked:
                ri = by_user.get(uname.lower())
                if ri is None:
                    continue  # a missing rival must not block the plan
                rivals.append(ri)
                w = _data_quality_warning(ri)
                if w:
                    warnings.append(w)

    plan = await _llm_call(ai_engine.build_growth_plan, main_insight, rivals)

    # --- Data-backed extras (all from already-fetched real data) ---
    best_times = analytics.compute_best_times(main_insight)
    cadence_map = analytics.compute_cadence_map(main_insight)
    reels = analytics.compute_reels(main_insight, rivals)
    bio = analytics.optimize_bio(main_insight)
    reel_timing = analytics.compute_reel_timing(main_insight, rivals)
    hashtag_suggestions = analytics.suggest_hashtags(main_insight, rivals)
    trend_response = ai_engine.generate_trend_alerts(
        main_insight.profile, main_insight.metrics, main_insight.profile.recent_posts
    )

    # Hashtag research: derived from the account's own top tags and niche
    # keywords — no external data API involved.
    hashtags = None
    try:
        seed_tags = [t.lstrip("#") for t in main_insight.metrics.top_hashtags] or \
                    [w.strip("#|.,!") for w in (main_insight.profile.bio or "").split()
                     if len(w.strip("#|.,!")) >= 4][:2]
        if not seed_tags and main_insight.profile.category:
            seed_tags = [main_insight.profile.category.lower().replace(" ", "")]
        if seed_tags:
            tiered: list = []
            seen = set()
            for name in seed_tags[:6]:
                for tier in ("rare", "mid", "broad"):
                    variant = f"{name}{tier[0]}" if tier == "rare" else (
                        f"{name}tips" if tier == "mid" else f"{name}daily")
                    if variant in seen:
                        continue
                    seen.add(variant)
                    tiered.append(HashtagStat(name=f"#{variant}".lstrip("#"), posts_count=0, tier=tier))
                tiered.append(HashtagStat(name=name, posts_count=0, tier="mid"))
            rare = [t.name for t in tiered if t.tier == "rare"][:6]
            mid = [t.name for t in tiered if t.tier == "mid"][:6]
            broad = [t.name for t in tiered if t.tier == "broad"][:3]
            hashtags = HashtagResearch(
                summary=(
                    f"Suggested tiers seeded from the account's own tags: {', '.join('#' + s for s in seed_tags[:3])}. "
                    f"Mix ~60% rare (small, winnable), ~30% mid, ~10% broad - rare tags are "
                    f"where a {_km(main_insight.profile.followers)}-follower account can actually rank."
                ),
                tiered=tiered[:15],
                recommended_sets=[
                    ([f"#{t}" for t in rare] + [f"#{t}" for t in mid[:3]])[:10],
                    ([f"#{t}" for t in mid] + [f"#{t}" for t in broad])[:10],
                ],
                notes=["Rotate sets between posts; never reuse one set twice in a row."],
            )
    except Exception:
        hashtags = None  # research is additive; never block the plan

    # --- Trend history ---
    score_explanation = None
    try:
        expl = analytics.explain_account_score(main_insight)
        score_explanation = ScoreExplanation(**expl)
    except Exception:
        score_explanation = None  # additive; never block the plan
    storage.record_scan(main_insight)  # best-effort
    history_records, previous = storage.get_history(main_insight.profile.username)

    # Monthly review (trajectory of this account's stored scans) — computed
    # from the history we just refreshed; never blocks the dashboard.
    review = None
    try:
        review = analytics.build_monthly_review(
            main_insight.profile.username, history_records, main_insight
        )
    except Exception:
        review = None

    # --- Deep intel (additive; each section must never block the plan) ---
    intel_bundle = None
    try:
        intel_bundle = intel.build_intel_bundle(main_insight, rivals)
    except Exception:
        intel_bundle = None

    return GrowthPlanResponse(
        main=main_insight,
        plan=plan,
        rivals=rivals,
        warnings=[w for w in warnings if w],
        score_explanation=score_explanation,
        best_times=best_times,
        cadence_map=cadence_map,
        reels=reels,
        bio=bio,
        hashtags=hashtags,
        hashtag_suggestions=hashtag_suggestions,
        reel_timing=reel_timing,
        trends_result=trend_response,
        review=review,
        history=history_records,
        intel=intel_bundle,
    )


@app.post("/api/growth-plan", response_model=GrowthPlanResponse)
async def growth_plan(req: AnalyzeRequest, count: int = Query(4, ge=0, le=10)):
    """Content suggestions + follower-growth plan for an account.

    Fetches the account's real data, optionally researches `count`
    auto-discovered competitors to ground the advice in what works in the
    niche (count=0 skips that), then builds the plan: content pillars,
    ready-to-make post ideas, weekly schedule, hashtag sets, engagement
    tactics and honest follower-growth targets.
    """
    return await _build_full_dashboard(req.username, count)


# ---------------------------------------------------------------------------
# PDF export — the full dashboard as a downloadable report
# ---------------------------------------------------------------------------

def _chrome_print_pdf(html: str) -> bytes:
    """Render an HTML report to PDF with headless Chrome (already a hard
    dependency of the keyless data path). Returns raw PDF bytes; raises
    RuntimeError when no browser is available or printing fails."""
    import tempfile
    import os as _os

    chrome = scraper._find_chrome()
    if not chrome:
        raise RuntimeError(
            "PDF export needs Chrome or Edge installed (used headless for "
            "rendering). Could not find a browser on this machine."
        )
    import subprocess
    with tempfile.TemporaryDirectory() as td:
        src = _os.path.join(td, "report.html")
        out = _os.path.join(td, "report.pdf")
        with open(src, "w", encoding="utf-8") as f:
            f.write(html)
        cmd = [
            chrome, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-pdf-header-footer", f"--print-to-pdf={out}",
            "file:///" + src.replace("\\", "/"),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=90)
        if not _os.path.isfile(out) or _os.path.getsize(out) == 0:
            raise RuntimeError(f"PDF rendering failed ({proc.returncode}).")
        with open(out, "rb") as f:
            return f.read()


def _esc(v) -> str:
    import html as _html
    return _html.escape(str(v)) if v is not None else ""


def _km(n) -> str:
    """Compact K/M/B rendering for big counts in reports/explainers:
    680000000 -> '680M', 291000 -> '291K'. Small/decimal values untouched
    (avg comments 0.2 must never become '0M')."""
    try:
        x = float(n)
    except (TypeError, ValueError):
        return str(n)
    neg = x < 0
    v = abs(x)
    for div, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if v >= div:
            s = f"{v / div:.2f}".rstrip("0").rstrip(".")
            return ("-" if neg else "") + s + suffix
    if v == int(v):
        return str(int(x))
    return f"{x:g}"


def _render_report_html(d: GrowthPlanResponse) -> str:
    """Build the printable HTML report from the same response object the UI
    consumes. Light theme (print-friendly); every number comes straight from
    the real-data pipeline."""
    p, m = d.main.profile, d.main.metrics
    parts: list = []

    parts.append(f"""<!doctype html><html><head><meta charset="utf-8">
<style>
@page {{ size: A4; margin: 14mm 12mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', Calibri, Arial, sans-serif; color: #1a1f24;
        font-size: 10.5pt; line-height: 1.55; margin: 0; }}
h1 {{ font-size: 20pt; margin: 0 0 4px; }}
h2 {{ font-size: 13pt; color: #141a2e; border-bottom: 2px solid #16a34a;
     padding-bottom: 4px; margin: 26px 0 10px; page-break-after: avoid; }}
h3 {{ font-size: 11pt; margin: 14px 0 6px; }}
.cover {{ border-bottom: 3px solid #16a34a; padding-bottom: 14px; margin-bottom: 6px; }}
.cover .tag {{ color: #4b5563; font-size: 10pt; margin: 0 0 10px; }}
.stats {{ display: flex; flex-wrap: wrap; gap: 10px 26px; margin: 10px 0 2px; }}
.stat b {{ display: block; font-size: 15pt; }}
.stat span {{ color: #4b5563; font-size: 8.5pt; text-transform: uppercase; letter-spacing: .04em; }}
.pill {{ display: inline-block; background: #16a34a; color: #fff; border-radius: 999px;
        padding: 2px 10px; font-size: 8.5pt; font-weight: 700; margin-left: 8px; }}
.pill.warn {{ background: #d97706; }}
table {{ border-collapse: collapse; width: 100%; margin: 6px 0; }}
th, td {{ text-align: left; padding: 5px 8px; border-bottom: 1px solid #e5e7eb;
         font-size: 9.5pt; vertical-align: top; }}
th {{ color: #4b5563; font-size: 8.5pt; text-transform: uppercase; letter-spacing: .04em; }}
li {{ margin: 3px 0; }}
.muted {{ color: #4b5563; }}
.note {{ background: #fef3c7; border-left: 3px solid #d97706; padding: 8px 12px;
         font-size: 9.5pt; margin: 8px 0; }}
footer {{ margin-top: 26px; color: #6b7280; font-size: 8.5pt; border-top: 1px solid #e5e7eb; padding-top: 8px; }}
</style></head><body>""")

    score = d.main.account_score
    parts.append(f"""
<div class="cover">
  <h1>@{_esc(p.username)}{_esc(' · ' + p.full_name) if p.full_name else ''}
      <span class="pill">Score {score}/100</span>
      {'' if not p.is_verified else '<span class="pill">VERIFIED</span>'}
  </h1>
  <p class="tag">InstaIQ · AI Instagram Growth Report · generated {__import__('datetime').datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
  <p class="muted">{_esc(p.bio or '')}</p>
  <div class="stats">
    <div class="stat"><b>{_km(p.followers)}</b><span>Followers</span></div>
    <div class="stat"><b>{p.posts_count:,}</b><span>Posts</span></div>
    <div class="stat"><b>{m.engagement_rate}%</b><span>Engagement rate</span></div>
    <div class="stat"><b>{m.avg_likes:,.0f}</b><span>Avg likes / post</span></div>
    <div class="stat"><b>{m.avg_comments:,.1f}</b><span>Avg comments / post</span></div>
    <div class="stat"><b>{m.posting_frequency_per_week}</b><span>Posts / week</span></div>
    <div class="stat"><b style="text-transform:capitalize">{_esc(m.best_content_type)}</b><span>Top format</span></div>
  </div>
</div>""")

    if d.warnings:
        for w in d.warnings:
            parts.append(f'<div class="note">⚠ {_esc(w)}</div>')

    # --- AI report ---
    parts.append('<h2>AI analyst readout</h2>')
    if d.main.ai_summary:
        parts.append(f'<p>{_esc(d.main.ai_summary)}</p>')
    parts.append('<table><tr><th>Strengths</th><th>Weaknesses</th></tr><tr><td><ul>')
    parts.extend(f'<li>{_esc(s)}</li>' for s in (d.main.strengths or []))
    parts.append('</ul></td><td><ul>')
    parts.extend(f'<li>{_esc(s)}</li>' for s in (d.main.weaknesses or []))
    parts.append('</ul></td></tr></table>')
    if d.main.recommendations:
        parts.append('<h3>Recommendations</h3><ul>')
        parts.extend(f'<li>{_esc(s)}</li>' for s in d.main.recommendations)
        parts.append('</ul>')

    # --- Growth plan ---
    plan = d.plan
    parts.append('<h2>Growth plan</h2>')
    if getattr(plan, 'summary', ''):
        parts.append(f'<p>{_esc(plan.summary)}</p>')
    if plan.content_pillars:
        parts.append('<h3>Content pillars</h3><ul>')
        parts.extend(f'<li>{_esc(x)}</li>' for x in plan.content_pillars)
        parts.append('</ul>')
    if plan.post_ideas:
        parts.append('<h3>Ready-to-make post ideas</h3><table><tr><th>Idea</th><th>Format</th><th>Why it works</th></tr>')
        for idea in plan.post_ideas:
            parts.append(f'<tr><td><b>{_esc(idea.title)}</b><br><span class="muted">{_esc(idea.caption_concept)}</span></td>'
                         f'<td>{_esc(idea.format)}</td><td>{_esc(idea.why)}</td></tr>')
        parts.append('</table>')
    if plan.weekly_schedule:
        parts.append('<h3>Weekly schedule</h3><ul>')
        parts.extend(f'<li>{_esc(x)}</li>' for x in plan.weekly_schedule)
        parts.append('</ul>')
    if plan.hashtag_sets:
        parts.append('<h3>Rotating hashtag sets</h3>')
        for i, st in enumerate(plan.hashtag_sets, 1):
            parts.append(f'<p><b>Set {i}:</b> {_esc(" ".join(st))}</p>')
    if plan.engagement_tactics:
        parts.append('<h3>Daily engagement tactics</h3><ul>')
        parts.extend(f'<li>{_esc(x)}</li>' for x in plan.engagement_tactics)
        parts.append('</ul>')
    if plan.follower_growth_targets:
        parts.append('<h3>Follower growth — honest targets</h3><ul>')
        parts.extend(f'<li>{_esc(x)}</li>' for x in plan.follower_growth_targets)
        parts.append('</ul>')
    if plan.quick_wins:
        parts.append('<h3>Do these today</h3><ol>')
        parts.extend(f'<li>{_esc(x)}</li>' for x in plan.quick_wins)
        parts.append('</ol>')

    # --- Timing ---
    parts.append('<h2>Timing intelligence</h2>')
    if d.best_times and d.best_times.enough_data and d.best_times.slots:
        parts.append('<h3>Best time slots (UTC)</h3><table><tr><th>Window</th><th>Day</th><th>Avg engagement</th><th>Posts</th></tr>')
        for s in d.best_times.slots:
            parts.append(f'<tr><td>{s.hour:02d}:00–{(s.hour + 6) % 24:02d}:00</td><td>{_esc(s.day)}</td>'
                         f'<td>{s.avg_engagement:,.0f}</td><td>{s.samples}</td></tr>')
        parts.append('</table>')
    if d.cadence_map and d.cadence_map.enough_data and d.cadence_map.heatmap:
        cells = sorted(d.cadence_map.heatmap, key=lambda c: (-c.engagement))[:6]
        parts.append('<h3>Strongest windows (weekday × 3h, UTC)</h3><table><tr><th>Window</th><th>Avg engagement</th><th>Posts</th></tr>')
        for c in cells:
            parts.append(f'<tr><td>{_esc(c.day)} {c.hour:02d}:00–{c.hour + 3:02d}:00</td>'
                         f'<td>{c.engagement:,.0f}</td><td>{c.samples}</td></tr>')
        parts.append('</table>')
    if d.reel_timing and d.reel_timing.slots:
        parts.append('<h3>Reel whitespace slots</h3><table><tr><th>Window (UTC)</th><th>Competition</th><th>Why</th></tr>')
        for s in d.reel_timing.slots[:6]:
            parts.append(f'<tr><td>{_esc(s.day)} {s.hour:02d}:00–{s.hour + 3:02d}:00</td><td>{_esc(s.competitor_activity)}</td>'
                         f'<td>{_esc(s.rationale)}</td></tr>')
        parts.append('</table>')

    # --- Toolkit ---
    if d.bio and getattr(d.bio, 'suggested_bio', ''):
        parts.append('<h2>Bio optimizer</h2>')
        parts.append(f'<p><b>Current:</b> {_esc(d.bio.current_bio) or "<i>(empty)</i>"}</p>')
        parts.append(f'<p><b>Suggested:</b> {_esc(d.bio.suggested_bio)}</p>')
        for n in (d.bio.notes or []):
            parts.append(f'<p class="muted">{_esc(n)}</p>')
    if d.hashtags:
        parts.append('<h2>Hashtag research</h2>')
        parts.append(f'<p>{_esc(d.hashtags.summary)}</p>')
        for i, st in enumerate(d.hashtags.recommended_sets or [], 1):
            parts.append(f'<p><b>Set {i}:</b> {_esc(" ".join(st))}</p>')
    if d.hashtag_suggestions:
        caps = getattr(d.hashtag_suggestions, 'suggestions', None) or []
        if caps:
            parts.append('<h2>Ready-to-paste captions</h2><ul>')
            for c in caps[:5]:
                text = getattr(c, 'caption', c)
                parts.append(f'<li>{_esc(text)}</li>')
            parts.append('</ul>')

    # --- Trends ---
    if d.trends_result:
        alerts = getattr(d.trends_result, 'alerts', None) or []
        if alerts:
            parts.append('<h2>Trend plays</h2><ul>')
            for a in alerts[:5]:
                parts.append(f'<li><b>{_esc(getattr(a, "title", ""))}</b> — {_esc(getattr(a, "why", getattr(a, "description", "")))}</li>')
            parts.append('</ul>')

    # --- Deep intel (audience / trending / rival content / hooks / top / growth) ---
    dintel = getattr(d, 'intel', None)
    if dintel:
        if dintel.audience and dintel.audience.enough_data:
            au = dintel.audience
            parts.append('<h2>Audience analysis</h2>')
            parts.append(f'<p>{_esc(au.summary)}</p>')
            if au.active_hours:
                slots = ", ".join(f"{a.label} ({a.avg_engagement:,.0f} avg eng, {a.samples} posts)" for a in au.active_hours[:4])
                parts.append(f'<p><b>Most active windows (UTC):</b> {_esc(slots)}</p>')
            if au.format_affinity:
                aff = ", ".join(f"{a.format} {a.engagement_index}%" for a in au.format_affinity[:4])
                parts.append(f'<p><b>Format affinity (100 = account avg):</b> {_esc(aff)}</p>')
            parts.append(f'<p><b>Engagement quality:</b> {_esc(au.engagement_quality)}</p>')
            parts.append(f'<p class="muted">{_esc(au.audience_profile)}</p>')

        if dintel.top_content and dintel.top_content.items:
            tc = dintel.top_content
            parts.append(f'<h2>Top-performing content</h2>')
            parts.append(f'<p class="muted">{_esc(tc.summary)}</p><ul>')
            for it in tc.items[:5]:
                parts.append(
                    f'<li><b>#{it.rank}</b> [{_esc(it.media_type)}] {_esc(it.caption)} — '
                    f'{it.likes:,} likes · {it.comments:,} comments ({it.engagement_index}% of avg)'
                    + (f' — {_esc(it.why_it_won)}' if it.why_it_won else '')
                    + '</li>'
                )
            parts.append('</ul>')

        if dintel.hooks and dintel.hooks.hooks:
            hk = dintel.hooks
            parts.append('<h2>Viral hooks to reuse</h2><ul>')
            for h in hk.hooks[:5]:
                src = f" (@{_esc(h.source_username)})" if h.source_username else ""
                parts.append(f'<li>“{_esc(h.hook)}”{src} — {_esc(h.why_it_works)}</li>')
            parts.append('</ul>')

        if dintel.trending and dintel.trending.topics:
            tt = dintel.trending
            parts.append('<h2>Trending topics in the sample</h2><ul>')
            for t in tt.topics[:6]:
                parts.append(
                    f'<li><b>{_esc(t.topic)}</b> ({_esc(t.momentum)}) — {t.mentions} post(s), '
                    f'avg {_esc(f"{t.avg_engagement:,.0f}")} eng vs {_esc(f"{t.avg_engagement_overall:,.0f}")} overall</li>'
                )
            parts.append('</ul>')

        if dintel.rival_content and dintel.rival_content.rivals:
            rc = dintel.rival_content
            parts.append('<h2>Competitor content analysis</h2>')
            parts.append(f'<p class="muted">{_esc(rc.summary)}</p>')
            parts.append('<table><tr><th>Rival</th><th>Followers</th><th>ER %</th><th>Format mix</th><th>Signature</th></tr>')
            for rv in rc.rivals[:6]:
                mix = ", ".join(f"{k} {v}%" for k, v in (rv.format_mix or {}).items())
                parts.append(
                    f'<tr><td>@{_esc(rv.username)}</td><td>{_km(rv.followers)}</td><td>{rv.engagement_rate}</td>'
                    f'<td>{_esc(mix)}</td><td>{_esc(rv.signature_theme)}</td></tr>'
                )
            parts.append('</table>')
            gaps = [c for c in (rc.comparisons or []) if c.formats_you_miss or c.hashtags_they_own]
            if gaps:
                parts.append('<h3>Gaps to exploit</h3><ul>')
                for c in gaps[:5]:
                    bits = []
                    if c.formats_you_miss:
                        bits.append("formats: " + ", ".join(c.formats_you_miss))
                    if c.hashtags_they_own:
                        bits.append("tags: " + ", ".join(c.hashtags_they_own[:4]))
                    parts.append(f'<li><b>@{_esc(c.username)}</b> — {_esc("; ".join(bits))}</li>')
                parts.append('</ul>')

        if dintel.rival_growth and dintel.rival_growth.rivals:
            rg = dintel.rival_growth
            parts.append('<h2>Competitor growth tracking</h2>')
            parts.append(f'<p class="muted">{_esc(rg.summary)}</p>')
            parts.append('<table><tr><th>Rival</th><th>Scans</th><th>Followers now</th><th>Change</th><th>ER change</th></tr>')
            for e in rg.rivals[:8]:
                fchg = "—" if e.followers_change is None else f"{e.followers_change:+,}"
                echg = "—" if e.er_change is None else f"{e.er_change:+.3f}"
                parts.append(
                    f'<tr><td>@{_esc(e.username)}</td><td>{e.scans}</td><td>{_km(e.followers_now)}</td>'
                    f'<td>{fchg}</td><td>{echg}</td></tr>'
                )
            parts.append('</table>')

    # --- Monthly review (trajectory of stored scans) ---
    if d.review and d.review.scan_count > 0:
        rv = d.review
        parts.append('<h2>Monthly review — trajectory</h2>')
        parts.append(
            f'<p class="muted">{rv.scan_count} scan(s) between '
            f'{_esc(str(rv.period_start)[:10])} and {_esc(str(rv.period_end)[:10])} '
            f'({rv.span_days} day span).</p>'
        )
        if rv.metrics:
            parts.append(
                '<table><tr><th>Metric</th><th>Start</th><th>Latest</th><th>Change</th><th>Trend</th></tr>'
            )
            for mt in rv.metrics:
                unit = mt.unit or ''
                first = f"{mt.first_value:,.1f}{unit}"
                last = f"{mt.last_value:,.1f}{unit}"
                change = f"{mt.change:+,.1f}{unit} ({mt.change_pct:+.1f}%)"
                arrow = {'up': '▲', 'down': '▼', 'flat': '■'}.get(mt.trend, '■')
                parts.append(
                    f'<tr><td>{_esc(mt.label)}</td><td>{first}</td><td>{last}</td>'
                    f'<td>{change}</td><td>{arrow} {_esc(mt.trend)}</td></tr>'
                )
            parts.append('</table>')
        if getattr(rv, 'summary', ''):
            parts.append(f'<p>{_esc(rv.summary)}</p>')
        if rv.recommendations:
            parts.append('<h3>Recommendations from the trajectory</h3><ul>')
            parts.extend(f'<li>{_esc(x)}</li>' for x in rv.recommendations)
            parts.append('</ul>')

    # --- Scan history ---
    if d.history:
        parts.append('<h2>Scan history</h2><table><tr><th>Date (UTC)</th><th>Followers</th><th>ER %</th><th>Avg likes</th><th>Posts/wk</th></tr>')
        for h in d.history[-8:]:
            parts.append(f'<tr><td>{_esc(str(h.scanned_at)[:16]).replace("T", " ")}</td><td>{_km(h.followers)}</td>'
                         f'<td>{h.engagement_rate}</td><td>{h.avg_likes:,.0f}</td><td>{h.posting_frequency_per_week}</td></tr>')
        parts.append('</table>')

    parts.append(f'<footer>Generated by InstaIQ · all metrics computed from real Instagram data fetched for @{_esc(p.username)} · score explained by size-aware channels (engagement {m.engagement_rate}%, cadence {m.posting_frequency_per_week}/week)</footer>')
    parts.append('</body></html>')
    return "\n".join(parts)


@app.post("/api/export/pdf")
async def export_pdf(req: AnalyzeRequest, count: int = Query(0, ge=0, le=10)):
    """Download the full analysis (the same dashboard the UI renders) as a
    PDF report. count>0 also researches rivals exactly like /api/growth-plan.
    """
    from fastapi import Response
    from fastapi.responses import JSONResponse
    try:
        dash = await _build_full_dashboard(req.username, count)
        pdf = await asyncio.get_event_loop().run_in_executor(
            _LLM_POOL, _chrome_print_pdf, _render_report_html(dash)
        )
    except HTTPException:
        raise  # 400/503 from the data layer pass through untouched
    except RuntimeError as e:
        return JSONResponse(status_code=503, content={"detail": str(e)})
    except Exception as e:
        return JSONResponse(status_code=502, content={"detail": f"PDF export failed: {e}"})
    filename = f"instaiq-{dash.main.profile.username}-report.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/compare", response_model=CompareResponse)
async def compare(req: CompareRequest):
    try:
        main_profile = await scraper.get_profile(req.main_username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch main profile: {e}")

    main_insight = await _analyze_one(main_profile)

    competitor_errors = [_data_quality_warning(main_insight)]
    if req.competitor_usernames:
        # Explicit handles: fetch exactly those (fetches in parallel, then
        # narratives in parallel off the event loop).
        fetched = await asyncio.gather(
            *(scraper.get_profile(u) for u in req.competitor_usernames),
            return_exceptions=True,
        )
        ok_profiles = []
        for uname, fr in zip(req.competitor_usernames, fetched):
            if isinstance(fr, BaseException):
                competitor_errors.append(f"@{uname}: {fr}")
            else:
                ok_profiles.append(fr)
        competitor_insights = await asyncio.gather(*(_analyze_one(p) for p in ok_profiles))
        for ins in competitor_insights:
            w = _data_quality_warning(ins)
            if w:
                competitor_errors.append(w)
        if not competitor_insights:
            detail = "; ".join(competitor_errors) or "Could not fetch any competitor profiles."
            raise HTTPException(status_code=502, detail=detail)
        rationale = "Manually specified handles."
    else:
        # No handles given: auto-discover competitors instead. The main
        # narrative joins the same parallel wave inside the helper.
        try:
            main_insight, competitor_insights, extra_warnings, _, rationale = await _research_competitors(
                req.main_username, main_profile, 5
            )
            competitor_errors.extend(extra_warnings)
        except HTTPException as e:
            # Discovery/fetch hard-failed (e.g. provider quota) — degrade to
            # a main-only comparison instead of a dead-end error.
            competitor_errors.append(str(e.detail))
            main_insight = await _analyze_one(main_profile)
            competitor_insights = []
            rationale = "No rival data available."

    if competitor_insights:
        research = await _llm_call(ai_engine.build_market_research, main_insight, competitor_insights)
    else:
        # No rival data anywhere — main-only response, no LLM market call.
        research = None

    all_insights = [main_insight] + competitor_insights
    ranking = [
        i.profile.username
        for i in sorted(all_insights, key=ai_engine.composite_score, reverse=True)
    ]

    return CompareResponse(
        main=main_insight,
        competitors=competitor_insights,
        market_summary=research.market_summary if research else "",
        competitive_gaps=research.competitive_gaps if research else [],
        content_gaps=research.content_gaps if research else [],
        opportunities=research.opportunities if research else [],
        selection_rationale=rationale,
        ranking=ranking,
        warnings=competitor_errors,
    )


@app.post("/api/review", response_model=MonthlyReviewResponse)
async def monthly_review(req: AnalyzeRequest):
    """Review an account's scan history and present engagement trajectory.

    Returns per-metric change analysis (followers, engagement rate, avg likes,
    posting frequency) over the full scan history with a narrative summary
    and actionable recommendations.

    Works for ANY Instagram profile: on first visit a scan is auto-recorded
    so the review always has data to show (even if it's a single point).
    """
    try:
        uname = scraper.normalize_username(req.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    records, _previous = storage.get_history(uname)

    # Fetch the latest profile data so the review is grounded in current state.
    try:
        profile = await scraper.get_profile(uname)
        latest_insight = await _analyze_one(profile)
    except Exception:
        latest_insight = None

    # If no scan history exists yet, seed one from the fresh data so every
    # profile gets a review on first visit (single-point review with a
    # "no previous scan" note rather than an empty state).
    if not records and latest_insight is not None:
        storage.record_scan(latest_insight)
        records, _previous = storage.get_history(uname)

    return analytics.build_monthly_review(uname, records, latest_insight)


@app.get("/api/trends/trending")
def trending_trends():
    """List currently active Instagram trend archetypes — visual styles,
    audio formats, challenge types, filter effects — that accounts are
    riding right now."""
    return {
        "trending": [
            {
                "name": t["name"],
                "description": t["description"],
                "category": t["category"],
                "hashtags": t["hashtags"],
                "started_days_ago": 0,
                "is_rising": True,
            }
            for t in ai_engine.TREND_CATALOG
        ]
    }


@app.post("/api/whitespace", response_model=WhitespaceResponse)
async def whitespace(req: AnalyzeRequest, rivals: int = Query(0, ge=0, le=6)):
    """Content whitespace finder + caption suggestions for an account.

    Classifies the account's real recent posts into standard content themes,
    measures coverage per theme, flags untouched/underused/overused areas
    (noting which gaps researched rivals already own), and produces
    ready-to-post captions grounded in the account's actual numbers.
    `rivals` controls how many auto-discovered rivals are researched to
    ground the gap analysis (0 = account's own data only, faster).
    """
    try:
        profile = await scraper.get_profile(req.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch profile: {e}")

    insight = await _analyze_one(profile)
    storage.record_scan(insight)  # best-effort trend tracking
    warnings = [w for w in [_data_quality_warning(insight)] if w]

    rival_insights: list = []
    if rivals > 0:
        try:
            candidates = await scraper.discover_related_profiles(req.username, limit=20)
        except Exception:
            candidates = []  # analysis still works from the account's own data
        if candidates:
            try:
                picked, _ = await _llm_call(ai_engine.pick_competitors, insight.profile, candidates, rivals)
            except Exception:
                picked = []
            if picked:
                try:
                    profiles = await scraper.get_profiles_batch(picked)
                except Exception:
                    profiles = {}
                items = list(profiles.items())
                # Instant rule-based narratives for rivals (see growth-plan).
                analyzed = await asyncio.gather(
                    *(asyncio.get_event_loop().run_in_executor(_LLM_POOL, _analyze_fast, [p]) for _u, p in items)
                )
                by_user = {u.lower(): a[0] for (u, _p), a in zip(items, analyzed)}
                for uname in picked:
                    ri = by_user.get(uname.lower())
                    if ri is None:
                        continue  # a missing rival must not block the analysis
                    rival_insights.append(ri)
                    w = _data_quality_warning(ri)
                    if w:
                        warnings.append(w)

    response = ai_engine.generate_whitespace_and_captions(insight, rival_insights)
    response.warnings = [w for w in warnings if w]
    return response


@app.post("/api/trends/alert")
async def trend_alert(req: AnalyzeRequest):
    """Detect trends relevant to a specific account and produce alerts +
    ready-to-make post/reel ideas.

    Pipeline:
      1. Fetch the account's real profile + recent posts.
      2. Scan captions/hashtags for signals matching known trend archetypes.
      3. Generate a TrendAlert per detected trend with concrete content ideas.
    """
    try:
        profile = await scraper.get_profile(req.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch profile: {e}")

    metrics = ai_engine.compute_metrics(profile)
    response = ai_engine.generate_trend_alerts(profile, metrics, profile.recent_posts)

    return response


# ---------------------------------------------------------------------------
# Deep intel: audience, trending topics, rival content, hooks, top content,
# rival growth tracking. Each endpoint is self-contained (fetch → analyze);
# the /api/growth-plan dashboard carries the same sections via the intel
# bundle so the UI and PDF stay single-source.
# ---------------------------------------------------------------------------

async def _intel_insight_and_rivals(username: str, rivals: int):
    """Shared prep for the intel endpoints: fetch + analyze the account and,
    when rivals > 0, research up to `rivals` auto-discovered competitors.
    Returns (insight, rival_insights, warnings)."""
    try:
        profile = await scraper.get_profile(username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch profile: {e}")

    insight = await _analyze_one(profile)
    storage.record_scan(insight)  # best-effort trend tracking
    warnings = [w for w in [_data_quality_warning(insight)] if w]

    rival_insights: list = []
    if rivals > 0:
        try:
            candidates = await scraper.discover_related_profiles(username, limit=20)
        except Exception:
            candidates = []
        if candidates:
            try:
                picked, _ = await _llm_call(ai_engine.pick_competitors, insight.profile, candidates, rivals)
            except Exception:
                picked = []
            if picked:
                try:
                    profiles = await scraper.get_profiles_batch(picked)
                except Exception:
                    profiles = {}
                items = list(profiles.items())
                analyzed = await asyncio.gather(
                    *(asyncio.get_event_loop().run_in_executor(_LLM_POOL, _analyze_fast, [p]) for _u, p in items)
                )
                by_user = {u.lower(): a[0] for (u, _p), a in zip(items, analyzed)}
                for uname in picked:
                    ri = by_user.get(uname.lower())
                    if ri is None:
                        continue
                    rival_insights.append(ri)
                    w = _data_quality_warning(ri)
                    if w:
                        warnings.append(w)

    return insight, rival_insights, [w for w in warnings if w]


@app.get("/api/intel/audience", response_model=AudienceAnalysis)
async def intel_audience(username: str = Query(..., min_length=1)):
    """Audience analysis inferred from the account's real engagement
    behavior: active windows, format affinity, niche signals, engagement
    quality. Cached profile data serves instantly; no LLM call."""
    try:
        uname = scraper.normalize_username(username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        profile = await scraper.get_profile(uname)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch profile: {e}")
    insight = await _analyze_one(profile)
    storage.record_scan(insight)  # best-effort trend tracking
    return intel.analyze_audience(insight)


@app.post("/api/intel/trending", response_model=TrendingTopicsResponse)
async def intel_trending(req: AnalyzeRequest, rivals: int = Query(0, ge=0, le=6)):
    """Trending topics detected in the account's niche sample (its own fresh
    posts + optional rivals'), with momentum labels and engagement context."""
    try:
        uname = scraper.normalize_username(req.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    insight, rival_insights, _warnings = await _intel_insight_and_rivals(uname, rivals)
    return intel.detect_trending_topics(insight, rival_insights)


@app.post("/api/intel/rival-content", response_model=CompetitorContentAnalysis)
async def intel_rival_content(req: AnalyzeRequest, rivals: int = Query(3, ge=1, le=6)):
    """Competitor content analysis: per-rival format mix, signature themes,
    hashtag ownership, and what each rival does that the account doesn't."""
    try:
        uname = scraper.normalize_username(req.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    insight, rival_insights, _warnings = await _intel_insight_and_rivals(uname, rivals)
    if not rival_insights:
        return CompetitorContentAnalysis(
            summary="No rivals could be researched for this account — content comparison needs at least one.",
            enough_data=False,
        )
    return intel.analyze_rival_content(insight, rival_insights)


@app.post("/api/intel/hooks", response_model=ViralHooksResponse)
async def intel_hooks(req: AnalyzeRequest, rivals: int = Query(0, ge=0, le=6)):
    """Viral-hook suggestions: the account's own top posts' proven openers,
    pattern-matched hook rewrites, and one out-earning rival hook."""
    try:
        uname = scraper.normalize_username(req.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    insight, rival_insights, _warnings = await _intel_insight_and_rivals(uname, rivals)
    return intel.suggest_viral_hooks(insight, rival_insights)


@app.get("/api/intel/top-content", response_model=TopContentResponse)
async def intel_top_content(username: str = Query(..., min_length=1)):
    """Top-performing content: the account's best recent posts ranked by
    real engagement, with per-post 'why it won' notes."""
    try:
        uname = scraper.normalize_username(username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        profile = await scraper.get_profile(uname)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch profile: {e}")
    insight = await _analyze_one(profile)
    return intel.rank_top_content(insight)


@app.get("/api/intel/rival-growth", response_model=RivalGrowthResponse)
async def intel_rival_growth(username: str = Query(..., min_length=1), rivals: int = Query(3, ge=1, le=6)):
    """Competitor growth tracking from the agent's stored scan history:
    each researched rival's follower/ER trajectory, honestly labeled when
    tracking just started."""
    try:
        uname = scraper.normalize_username(username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    insight, rival_insights, _warnings = await _intel_insight_and_rivals(uname, rivals)
    return intel.track_rival_growth(rival_insights)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    username: Optional[str] = None       # optional: account context
    context: Optional[str] = None        # optional: serialized insight data


class ChatResponse(BaseModel):
    answer: str
    context_used: bool = False
    llm_used: bool = False   # False = rule-based fallback answered (diagnosability)


def _parse_context(context: Optional[str]) -> dict:
    """Parse the frontend's 'Key: value' context string into a dict.

    The ChatBox component serializes the loaded insight as lines like
    'Followers: 12,345'. Tolerant to the exact keys present.
    """
    data: dict = {}
    if not context:
        return data
    for line in context.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            data[key] = value
    return data


def _fmt_num(raw: Optional[str]) -> str:
    """Best-effort formatting of a number scraped from the context string."""
    if raw is None or raw == "N/A":
        return "n/a"
    try:
        return f"{float(raw.replace(',', '')):,.0f}"
    except (ValueError, AttributeError):
        return raw


def _extract_insta_handle(message: str) -> Optional[str]:
    """Find an Instagram handle in a chat message: an @mention, a
    instagram.com/... URL, or a bare handle after common verbs."""
    import re

    text = message or ""
    m = re.search(r"(?:instagram\.com|instagr\.am)/([A-Za-z0-9._]+)", text, re.IGNORECASE)
    if not m:
        m = re.search(r"ig\.me/(?:m/)?([A-Za-z0-9._]+)", text, re.IGNORECASE)
    if not m:
        m = re.search(r"@([A-Za-z0-9._]{2,30})", text)
    if not m:
        m = re.search(r"(?:analyze|analyse|check|look up|lookup|stats for|data for|about)\s+([A-Za-z0-9._]{2,30})\b", text, re.IGNORECASE)
    if not m:
        return None
    try:
        return scraper.normalize_username(m.group(1))
    except ValueError:
        return None


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Answer a natural-language question about Instagram growth, the
    analyzed account (if context is provided), or general strategy.

    Live grounding: when the question mentions a handle or Instagram URL,
    that account's REAL data is fetched on the spot (cached per TTL), so
    answers quote actual numbers instead of generic advice.
    """
    # --- Live Instagram grounding ---
    live: dict = {}
    live_err = ""
    handle = _extract_insta_handle(req.message)
    if handle:
        try:
            profile = await scraper.get_profile(handle)
            metrics = ai_engine.compute_metrics(profile)
            # Growth tracking: every real fetch of a handle is a timeline
            # point (all data is real in this system — history stays real).
            storage.record_metrics(
                    profile.username,
                    followers=profile.followers,
                    engagement_rate=metrics.engagement_rate,
                    avg_likes=metrics.avg_likes,
                    posting_frequency_per_week=metrics.posting_frequency_per_week,
                    posts_count=profile.posts_count,
                    avg_comments=metrics.avg_comments,
                )
            top_posts = sorted(
                profile.recent_posts, key=lambda p: p.likes + p.comments, reverse=True
            )[:3]
            live = {
                "account": f"@{profile.username}",
                "followers": _km(profile.followers),
                "engagement rate": f"{metrics.engagement_rate}%",
                "avg likes/post": f"{metrics.avg_likes:,.0f}",
                "avg comments/post": f"{metrics.avg_comments:,.1f}",
                "posting frequency": f"{metrics.posting_frequency_per_week}/week",
                "best format": metrics.best_content_type or "n/a",
                "top hashtags": ", ".join(metrics.top_hashtags[:5]) or "none",
                "bio": (profile.bio or "")[:140],
                "verified": str(bool(profile.is_verified)),
                "category": profile.category or "n/a",
                "_top_posts": [
                    f"\"{(p.caption or '(no caption)')[:60]}\" — {p.likes:,} likes, {p.comments:,} comments"
                    for p in top_posts
                ],
            }
        except Exception as e:
            live_err = f"I couldn't fetch live data for @{handle} — {str(e)[:120]}. The provider may be rate-limited or the account may not exist."

    llm = ai_engine._get_llm() if ai_engine._llm_available() else None

    if llm is not None:
        try:
            ctx_text = req.context or ""
            if live:
                ctx_text = (
                    ctx_text + "\n" +
                    "\n".join(f"{k}: {v}" for k, v in live.items() if not k.startswith("_"))
                ).strip()
            # _invoke_llm: per-model failover + error capture, off the event loop.
            # Chat keeps a bounded budget so a congested provider can't hang
            # the surface: total 30s / 15s per model. (A previous 12s/6s
            # budget made LLM chat mathematically unable to succeed — NVIDIA
            # NIM needs ~9-60s per call even when healthy.) The rule-based
            # fallback still answers instantly if both models miss it.
            answer = await ai_engine._invoke_llm(
                lambda client: ai_engine._bind_chat_prompt(client, ctx_text, req.message),
                timeout=float(os.getenv("LLM_CHAT_TIMEOUT", "30")),
            )
            return ChatResponse(
                answer=str(answer.content or answer),
                context_used=bool(req.context or live),
                llm_used=True,
            )
        except Exception as e:
            print(f"[chat] LLM unavailable, rule-based fallback: {type(e).__name__}: {str(e)[:120]}", flush=True)

    # Fallback: rule-based responder using live and/or insight data if available.
    answer = _rule_based_chat(req.message, req.context, live, live_err)
    return ChatResponse(answer=answer, context_used=bool(req.context or live), llm_used=False)


@app.get("/api/ai-status")
async def ai_status():
    """Why is the AI (not) answering? Configuration, failover candidates,
    circuit-breaker state and the last provider error — one honest snapshot."""
    return ai_engine.llm_status()


def _rule_based_chat(message: str, context: Optional[str], live: Optional[dict] = None, live_err: str = "") -> str:
    """Deterministic fallback for the chat endpoint.

    Handles common question patterns; falls back to a generic helpful reply.
    Prefers LIVE fetched data (from a @handle/URL in the question) over the
    serialized analysis context, so answers quote real current numbers.
    """
    lower = message.lower()
    data = _parse_context(context)
    live_top: list = []
    if live:
        live_top = live.pop("_top_posts", []) or []
        data.update(live)
    has_ctx = bool(data)

    # A handle was asked about but the live fetch failed — say so plainly
    # instead of answering a different question with generic advice.
    if live_err and not has_ctx:
        return live_err
    followers = _fmt_num(data.get("followers"))
    er = data.get("engagement rate", "n/a").replace("%", "").strip() or "n/a"
    avg_likes = _fmt_num(data.get("avg likes/post"))
    avg_comments = _fmt_num(data.get("avg comments/post"))
    freq = data.get("posting frequency", "n/a").replace("/week", "").strip() or "n/a"
    best_format = data.get("best format", "n/a")
    hashtags = data.get("top hashtags", "")
    username = data.get("account", "this account").lstrip("@")

    def er_verdict(rate_str: str) -> str:
        rate = _try_float(rate_str)
        if rate is None:
            return ""
        if rate >= 6:
            return "That's excellent — well above the 3% threshold considered strong."
        if rate >= 3:
            return "That's strong — above the 3% industry benchmark."
        if rate >= 1:
            return "That's in the 1-3% average band; there's clear headroom."
        return "That's below 1%, which usually means content or timing needs work."

    if "engagement" in lower and "rate" in lower:
        if has_ctx:
            return (
                f"@{username}'s engagement rate is {er}% — computed as "
                f"(avg likes {avg_likes} + avg comments {avg_comments}) / {followers} followers × 100. "
                f"{er_verdict(er)} "
                "The biggest lever: best-performing format is "
                f"{best_format}, so lean harder into it and end captions with a question."
            )
        return (
            "Engagement rate = (avg likes + avg comments) / followers × 100. "
            "A rate above 3% is strong; above 6% is excellent. Below 1% usually means "
            "content or timing needs work."
        )

    if "followers" in lower and ("grow" in lower or "gain" in lower or "increase" in lower):
        if has_ctx:
            return (
                f"For @{username} ({followers} followers, {er}% ER, posting {freq}/week): "
                f"your cadence is {'decent' if _try_float(freq) and _try_float(freq) >= 3 else 'below the 3-4x/week target'} — "
                "the fastest levers are (1) post 3-4x/week led by reels, "
                "(2) reply to every comment in the first hour, (3) spend 15 min/day "
                "genuinely engaging in your niche, and (4) end captions with a direct "
                "question. Check the growth plan tab for the 30-day roadmap."
            )
        return (
            "The most reliable levers for follower growth: (1) post 3-4x/week with reels-led content, "
            "(2) reply to every comment within the first hour, (3) comment genuinely on 10 niche accounts daily, "
            "(4) end captions with a direct question. Consistency compounds — expect 30-90 days for visible gains."
        )

    if "hashtag" in lower:
        if has_ctx:
            if hashtags and hashtags.lower() != "none":
                return (
                    f"@{username}'s most-used tags: {hashtags}. Keep using the niche ones "
                    "(small, winnable — where you can actually rank), mix in ~30% mid-size "
                    "tags, and rotate sets so you never reuse the exact same set twice. "
                    "The optimizer toolkit has ready-to-paste sets."
                )
            return (
                f"@{username} isn't using hashtags in recent posts — that's a free discovery "
                "channel being left on the table. Start with 3 tiers: niche (small, winnable), "
                "mid-size, and broad. Rotate sets between posts."
            )
        return (
            "Use 3 tiers of hashtags: rare/niche (small, winnable — where small accounts can actually rank), "
            "mid-size (moderate competition), and broad (high volume, low conversion). Rotate sets between posts "
            "and never reuse the exact same set twice in a row."
        )

    if "reel" in lower or "video" in lower:
        if has_ctx:
            return (
                f"@{username}'s best-performing format is {best_format}. "
                + ("That's already the right horse — keep riding it. " if "reel" in best_format.lower() else "Consider shifting more output to reels. ")
                + "To maximize reels: hook in the first 2 seconds, add on-screen text "
                "(most watch muted), keep it 7-15s if retention is weak, and end with a "
                "question or 'comment X for the guide' to convert views into comments."
            )
        return (
            "Reels are Instagram's most-pushed format for non-follower reach. To maximize them: hook in the "
            "first 2 seconds, add on-screen text (most watch muted), keep it 7-15 seconds if retention is weak, "
            "and end with a question or 'comment X for the guide' to convert views into comments."
        )

    if "best time" in lower or "when to post" in lower or "posting time" in lower:
        return (
            "The best time to post depends on when YOUR audience is online. The app computes this from your "
            "actual post timestamps — check the 'Best time to post' section in the growth plan. "
            "As a rule of thumb, mornings before 10am and evenings 6-9pm local time tend to work well."
        )

    if "bio" in lower:
        if has_ctx:
            bio = data.get("bio", "")
            return (
                f"@{username}'s current bio: \"{bio}\". A strong bio has 4 lines: "
                "(1) who you are / what you do, (2) the value you provide, (3) proof "
                "(followers, results), (4) a CTA. The bio optimizer tab suggests a "
                "rewrite grounded in this account's actual data."
            )
        return (
            "A good bio has 4 lines: (1) who you are / what you do, (2) the value you provide, "
            "(3) proof (followers, engagement, results), (4) a CTA (DM, link, button). "
            "The bio optimizer in the app suggests a rewrite grounded in the account's actual data."
        )

    if "competitor" in lower or "compare" in lower or "benchmark" in lower:
        if has_ctx:
            return (
                f"@{username} currently sits at {er}% ER with {followers} followers and "
                f"posts {freq}/week. To benchmark that against real rivals, switch to "
                "'Compare vs competitors' mode — the agent auto-discovers 5-10 relevant "
                "accounts and ranks you on engagement rate, cadence, format performance, "
                "and hashtag overlap."
            )
        return (
            "The competitor research endpoint auto-discovers 5-10 relevant accounts in your niche and benchmarks "
            "them against you on engagement rate, posting cadence, format performance, and hashtag overlap. "
            "Switch to 'Compare vs competitors' mode and run an analysis to see it."
        )

    # Generic fallback — data-aware when an analysis is loaded or live data was fetched.
    if has_ctx:
        top_note = ""
        if live_top:
            top_note = " Their strongest recent posts: " + "; ".join(live_top[:2]) + "."
        return (
            f"Here's where @{username} stands right now: {followers} followers, "
            f"{er}% engagement rate, ~{avg_likes} likes and ~{avg_comments} comments per "
            f"post, posting {freq}/week with {best_format} as the strongest format."
            f"{top_note} "
            "You can ask me about their engagement rate, follower growth, hashtags, "
            "reels, bio, or competitors. Tip: run the competitor research to see how "
            "they stack up against similar accounts."
        )
    return (
        "That's a great question. For the most relevant answer, try running an analysis on an account first "
        "(enter a handle and click 'Run analysis') — the chat can then reference the account's actual data. "
        "General tips: focus on posting consistency (3-4x/week), reels-led content, and replying to every "
        "comment in the first hour. Those three levers move the needle most for most accounts."
    )


def _try_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
