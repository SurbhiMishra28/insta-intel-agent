import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load .env BEFORE importing modules that read env vars at import time.
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

import ai_engine
import analytics
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
    ProfileData,
    CompareRequest,
    CompareResponse,
    CompetitorResearchResponse,
    DiscoveredCompetitor,
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
    WhitespaceResponse,
)

app = FastAPI(
    title="AI Instagram Profile & Competitor Intelligence Agent",
    description=(
        "Analyzes an Instagram profile, auto-discovers and researches its "
        "strongest competitors, and produces an AI-written competitive "
        "intelligence report. Runs entirely on the NVIDIA NIM API: real "
        "profile data is served from the local cache of past fetches, and "
        "unknown handles get clearly-badged simulated data."
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
        "data_mode": scraper.DATA_MODE,
        "data_source": (
            "Apify live fetch + local cache" if scraper.DATA_MODE == "live"
            else "local-cache + NVIDIA-only AI"
        ),
        "ai_engine": "langchain" if ai_engine.LLM_API_KEY else "rule-based-fallback",
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


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
    live = scraper.DATA_MODE == "live" and bool(scraper.APIFY_TOKENS)
    pool = scraper.apify_token_pool_status()
    healthy_tokens = sum(1 for t in pool if t["status"] == "ready" and not t["benched"])
    if pool and healthy_tokens == 0:
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
            "All AI analysis runs on the NVIDIA NIM API."
        )
    else:
        live_note = (
            "Real Instagram data is fetched live via the Apify API and "
            "cached locally, so repeat analyses are instant and free. "
            "All AI analysis runs on the NVIDIA NIM API."
        )
    return {
        "ok": True,
        "source": "live" if live else "cache",
        "data_mode": scraper.DATA_MODE,
        "ai_provider": "nvidia-nim" if ai_engine.LLM_API_KEY else "rule-based-fallback",
        "cached_profiles": cached_profiles,
        "apify_tokens": pool,
        "note": live_note if live else (
            "Real Instagram data is served from the local cache of past "
            "fetches; unknown handles get clearly-badged simulated data. "
            "All AI analysis runs on the NVIDIA NIM API."
        ),
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


def _data_quality_warning(insight: ProfileInsight) -> str:
    """Human-readable note when the served profile data is simulated or an
    incomplete cache row."""
    p = insight.profile
    if scraper.is_demo_row(insight.profile):
        return (
            f"@{p.username}: no cached real data for this handle yet — showing "
            "SIMULATED data so the analysis still works. Numbers are not real."
        )
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


@app.post("/api/growth-plan", response_model=GrowthPlanResponse)
async def growth_plan(req: AnalyzeRequest, count: int = Query(4, ge=0, le=10)):
    """Content suggestions + follower-growth plan for an account.

    Fetches the account's real data, optionally researches `count`
    auto-discovered competitors to ground the advice in what works in the
    niche (count=0 skips that), then builds the plan: content pillars,
    ready-to-make post ideas, weekly schedule, hashtag sets, engagement
    tactics and honest follower-growth targets.
    """
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
                    f"where a {main_insight.profile.followers:,}-follower account can actually rank."
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
        history=history_records,
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

    review = analytics.build_monthly_review(uname, records, latest_insight)
    return review


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


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    username: Optional[str] = None       # optional: account context
    context: Optional[str] = None        # optional: serialized insight data


class ChatResponse(BaseModel):
    answer: str
    context_used: bool = False


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
            top_posts = sorted(
                profile.recent_posts, key=lambda p: p.likes + p.comments, reverse=True
            )[:3]
            live = {
                "account": f"@{profile.username}",
                "followers": f"{profile.followers:,}",
                "engagement rate": f"{metrics.engagement_rate}%",
                "avg likes/post": f"{metrics.avg_likes:,.0f}",
                "avg comments/post": f"{metrics.avg_comments:,.0f}",
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

    llm = ai_engine._get_llm()

    if llm is not None:
        try:
            from langchain_core.prompts import ChatPromptTemplate

            prompt = ChatPromptTemplate.from_messages([
                ("system",
                 "You are an Instagram growth analyst. Answer the user's question "
                 "concisely and concretely. If context about an account is provided, "
                 "ground your answer in that data. Never invent numbers."),
                ("human",
                 "Account context (may be empty):\n{context}\n\n"
                 "Question:\n{message}\n\n"
                 "Answer:"),
            ])
            chain = prompt | llm
            ctx_text = req.context or ""
            if live:
                ctx_text = (
                    ctx_text + "\n" +
                    "\n".join(f"{k}: {v}" for k, v in live.items() if not k.startswith("_"))
                ).strip()
            # Offload: chain.invoke is a blocking HTTP round-trip; running it
            # inline would freeze every other endpoint for the duration.
            answer = await asyncio.get_event_loop().run_in_executor(
                _LLM_POOL,
                lambda: chain.invoke({"context": ctx_text or "(no account data)", "message": req.message}),
            )
            return ChatResponse(answer=str(answer.content or answer), context_used=bool(req.context or live))
        except Exception:
            pass

    # Fallback: rule-based responder using live and/or insight data if available.
    answer = _rule_based_chat(req.message, req.context, live, live_err)
    return ChatResponse(answer=answer, context_used=bool(req.context or live))


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
