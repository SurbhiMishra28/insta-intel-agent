"""
AI Engine (LangChain)
=====================
All natural-language reasoning runs through LangChain chains built on an
OpenAI-compatible chat model (OpenRouter, MiniMax, OpenAI, etc — configured
via LLM_API_KEY / LLM_BASE_URL / LLM_MODEL).

Design principle: the LLM only ever *narrates and reasons over* numbers that
were already computed from real profile data (`compute_metrics`). It never
invents the underlying stats.

Chains:
  1. insight_chain          — per-profile report (summary, strengths,
                              weaknesses, recommendations).
  2. market_research_chain  — competitive-set analysis (market summary,
                              competitive gaps, content gaps, opportunities).
  3. competitor_pick_chain  — selects the 5–10 most relevant competitors
                              from a candidate list *before* deep research,
                              so we only spend data-fetches on the right
                              accounts.

Every chain has a deterministic rule-based fallback with the same output
shape, so the app works with no API key and never breaks because of an
LLM/network failure.
"""
import os
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

from models import (
    CaptionSuggestion,
    GrowthPlan,
    PostIdea,
    ProfileData,
    ProfileInsight,
    ProfileMetrics,
    Post,
    ThemeCoverage,
    TrendAlert,
    TrendInfo,
    TrendSuggestion,
    TrendsResponse,
    WhitespaceArea,
    WhitespaceResponse,
)

# OpenAI-compatible provider (OpenRouter, MiniMax, OpenAI, ...). Set all three
# to enable LLM-generated reports; leave LLM_API_KEY blank to use the
# rule-based fallbacks instead.
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").strip()
LLM_MODEL = (os.getenv("LLM_MODEL", "") or "").strip() or "minimax/minimax-m3"
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))

# NVIDIA NIM auto-detection: keys like nvapi-... must go to NVIDIA's
# OpenAI-compatible endpoint. Without this, the SDK silently targets OpenAI,
# auth fails, and every chain quietly falls back to the same templates.
if LLM_API_KEY.startswith("nvapi") and not LLM_BASE_URL:
    LLM_BASE_URL = "https://integrate.api.nvidia.com/v1"
    LLM_MODEL = (os.getenv("LLM_MODEL", "") or "").strip() or "meta/llama-3.1-8b-instruct"


# ---------------------------------------------------------------------------
# Structured outputs (LangChain with_structured_output)
# ---------------------------------------------------------------------------

class ProfileNarrative(BaseModel):
    """LLM output for the per-profile insight chain."""
    summary: str = Field(description="2-4 sentence analyst summary of the account")
    strengths: List[str] = Field(max_length=3, description="Up to 3 specific strengths")
    weaknesses: List[str] = Field(max_length=3, description="Up to 3 specific weaknesses")
    recommendations: List[str] = Field(max_length=4, description="Up to 4 concrete, specific recommendations")


class MarketResearch(BaseModel):
    """LLM output for the competitive-set research chain."""
    market_summary: str = Field(description="3-5 sentence summary of the competitive landscape")
    competitive_gaps: List[str] = Field(max_length=5, description="Where the main account trails specific rivals")
    content_gaps: List[str] = Field(max_length=5, description="Content/format/topic spaces rivals own that the main account could take")
    opportunities: List[str] = Field(max_length=5, description="Concrete, specific opportunities for the main account")


class CompetitorShortlist(BaseModel):
    """LLM output for the competitor-selection chain."""
    picked: List[str] = Field(max_length=10, description="Usernames of the most relevant competitors, best first")
    rationale: str = Field(description="One or two sentences on why these were chosen")


def _get_llm(temperature: float = LLM_TEMPERATURE):
    """Returns a LangChain ChatOpenAI, or None when no key is configured."""
    if not LLM_API_KEY:
        return None
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=LLM_MODEL,
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL or None,
        temperature=temperature,
        max_retries=1,
        timeout=90,
    )


# ---------------------------------------------------------------------------
# Metrics (deterministic — no LLM involved)
# ---------------------------------------------------------------------------

def compute_metrics(profile: ProfileData) -> ProfileMetrics:
    import statistics

    posts = profile.recent_posts
    if not posts:
        return ProfileMetrics(
            engagement_rate=0, avg_likes=0, avg_comments=0,
            posting_frequency_per_week=0, follower_following_ratio=0,
            top_hashtags=[], best_content_type="n/a",
        )

    avg_likes = statistics.mean(p.likes for p in posts)
    avg_comments = statistics.mean(p.comments for p in posts)
    engagement_rate = round(
        ((avg_likes + avg_comments) / max(profile.followers, 1)) * 100, 3
    )

    span_days = max(p.posted_days_ago for p in posts) or 1
    posting_frequency_per_week = round(len(posts) / (span_days / 7), 2)

    follower_following_ratio = round(
        profile.followers / max(profile.following, 1), 2
    )

    hashtag_counts = {}
    for p in posts:
        for h in p.hashtags:
            hashtag_counts[h] = hashtag_counts.get(h, 0) + 1
    top_hashtags = sorted(hashtag_counts, key=hashtag_counts.get, reverse=True)[:5]

    engagement_by_type = {}
    for p in posts:
        engagement_by_type.setdefault(p.media_type, []).append(p.likes + p.comments)
    best_content_type = max(
        engagement_by_type, key=lambda k: statistics.mean(engagement_by_type[k])
    ) if engagement_by_type else "n/a"

    return ProfileMetrics(
        engagement_rate=engagement_rate,
        avg_likes=round(avg_likes, 1),
        avg_comments=round(avg_comments, 1),
        posting_frequency_per_week=posting_frequency_per_week,
        follower_following_ratio=follower_following_ratio,
        top_hashtags=top_hashtags,
        best_content_type=best_content_type,
    )


# ---------------------------------------------------------------------------
# Shared fact formatting (feeds every chain)
# ---------------------------------------------------------------------------

def _rate_engagement(rate: float) -> str:
    if rate >= 6:
        return "excellent"
    if rate >= 3:
        return "strong"
    if rate >= 1:
        return "average"
    return "below average"


def _profile_facts(profile: ProfileData, m: ProfileMetrics) -> str:
    return f"""Account: @{profile.username} ({profile.full_name})
Category: {profile.category or 'unknown'}
Verified: {profile.is_verified} | Business: {profile.is_business}
Followers: {profile.followers:,} | Following: {profile.following:,} | Total posts: {profile.posts_count:,}
Bio: {profile.bio or '(none)'}
Engagement rate: {m.engagement_rate}% ({_rate_engagement(m.engagement_rate)})
Avg likes/post: {m.avg_likes:,.0f} | Avg comments/post: {m.avg_comments:,.0f}
Posting frequency: {m.posting_frequency_per_week}/week
Best performing format: {m.best_content_type}
Top hashtags: {', '.join(m.top_hashtags) if m.top_hashtags else '(none)'}"""


# ---------------------------------------------------------------------------
# Chain 1: per-profile insight
# ---------------------------------------------------------------------------

_INSIGHT_SYSTEM = (
    "You are a senior Instagram growth analyst. You ground every claim in the "
    "provided metrics — never invent numbers. Recommendations must be concrete "
    "and reference the account's actual data."
)

_INSIGHT_HUMAN = """Analyze this Instagram account's performance:

{facts}

Write the analyst readout. Strengths/weaknesses must cite actual numbers from
the data. Recommendations must be actions this specific account can take."""

# Fallback kept from the original rule-based engine.
def _rule_based_summary(profile: ProfileData, m: ProfileMetrics) -> ProfileNarrative:
    quality = _rate_engagement(m.engagement_rate)
    summary = (
        f"@{profile.username} is a {profile.category or 'general'} account with "
        f"{profile.followers:,} followers and {quality} engagement at {m.engagement_rate}%. "
        f"They post roughly {m.posting_frequency_per_week}x/week, and "
        f"{m.best_content_type} content performs best for this account. "
        f"Average post pulls {int(m.avg_likes):,} likes and {int(m.avg_comments):,} comments."
    )

    strengths, weaknesses, recs = [], [], []

    if m.engagement_rate >= 3:
        strengths.append(f"Engagement rate of {m.engagement_rate}% beats typical industry benchmarks (1-3%).")
    else:
        weaknesses.append(f"Engagement rate of {m.engagement_rate}% trails the 1-3% industry benchmark.")
        recs.append("Prioritize reels and carousels early in the feed cycle — they historically outperform static images on reach.")

    if m.posting_frequency_per_week < 2:
        weaknesses.append(f"Posting cadence of {m.posting_frequency_per_week}x/week is low; algorithmic reach compounds with consistency.")
        recs.append("Increase posting cadence to 3-4x/week to stay in the algorithm's active-creator bucket.")
    else:
        strengths.append(f"Consistent posting cadence ({m.posting_frequency_per_week}x/week) supports steady reach.")

    if profile.is_verified:
        strengths.append("Verified badge lends credibility and can lift conversion on bio-link CTAs.")

    if m.follower_following_ratio < 1:
        weaknesses.append("Follows more accounts than follow back — can read as low-authority to new visitors.")

    if m.top_hashtags:
        recs.append(f"Double down on the hashtag cluster already working: {', '.join(m.top_hashtags[:3])}.")

    recs.append(f"Lean into {m.best_content_type} format — it's already the top performer for this account.")

    if not weaknesses:
        weaknesses.append("No major weaknesses detected in the sampled data — focus shifts to scaling what already works.")

    return ProfileNarrative(
        summary=summary, strengths=strengths, weaknesses=weaknesses, recommendations=recs
    )


def analyze_profile(profile: ProfileData) -> ProfileInsight:
    metrics = compute_metrics(profile)
    narrative = _rule_based_summary(profile, metrics)

    llm = _get_llm()
    if llm is not None:
        try:
            from langchain_core.prompts import ChatPromptTemplate
            prompt = ChatPromptTemplate.from_messages(
                [("system", _INSIGHT_SYSTEM), ("human", _INSIGHT_HUMAN)]
            )
            chain = prompt | llm.with_structured_output(ProfileNarrative)
            narrative = chain.invoke({"facts": _profile_facts(profile, metrics)})
        except Exception:
            pass  # keep the rule-based narrative on any LLM failure

    return ProfileInsight(
        profile=profile,
        metrics=metrics,
        ai_summary=narrative.summary,
        strengths=narrative.strengths,
        weaknesses=narrative.weaknesses,
        recommendations=narrative.recommendations,
    )


# ---------------------------------------------------------------------------
# Ranking (deterministic)
# ---------------------------------------------------------------------------

def composite_score(insight: ProfileInsight) -> float:
    """Single sortable number for ranking accounts against each other."""
    m = insight.metrics
    return (
        m.engagement_rate * 10
        + m.posting_frequency_per_week * 2
        + (5 if insight.profile.is_verified else 0)
    )


# ---------------------------------------------------------------------------
# Chain 3: competitor selection (runs BEFORE deep research to save data costs)
# ---------------------------------------------------------------------------

_RESEARCH_SYSTEM = (
    "You are a competitive-intelligence researcher for Instagram marketing. "
    "You judge account relevance from bios, names, categories and follower "
    "scale. Never invent accounts that are not in the candidate list."
)

_PICK_HUMAN = """Main account being analyzed:

{main_facts}

Candidate competitors found on Instagram (related accounts / same niche):

{candidates}

Pick the {count} most relevant DIRECT competitors — accounts a marketer would
benchmark this main account against. Prefer same niche/topic (judged from bio,
name, category), similar audience scale, public accounts. Exclude the main
account itself and obvious non-competitors (memes/aggregators/unrelated
celebrities) unless the main account is one."""

def _rule_based_pick(main_profile: ProfileData, candidates: List[dict], count: int) -> CompetitorShortlist:
    """Deterministic selection: public accounts, log-scale closeness to the
    main account's follower size (unknown-size candidates rank mid-pack; if
    the main account's size is unknown, prefer established accounts over
    tiny junk ones), topical-relevance bonus from bio/name overlap, verified
    bonus, keep input order for ties."""
    import math

    main_f = main_profile.followers
    relevance_tokens = {
        w for w in (
            main_profile.bio + " " + main_profile.full_name
        ).lower().replace("\n", " ").split()
        if len(w.strip("#|.,!")) >= 4
    }

    def _relevance(c: dict) -> int:
        text = f"{c.get('username', '')} {c.get('full_name', '')} {c.get('bio', '')}".lower()
        return sum(1 for t in relevance_tokens if t.strip("#|.,!") in text)

    scored: List[Tuple[float, int, str]] = []
    for i, c in enumerate(candidates):
        u = (c.get("username") or "").strip()
        if not u or u.lower() == main_profile.username.lower():
            continue
        if c.get("private"):
            continue
        f = c.get("followers") or 0
        if main_f > 0 and f > 0:
            closeness = -abs(math.log10(f) - math.log10(main_f))
        elif f > 0:
            closeness = min(math.log10(f), 5.0) - 2.0  # unknown main size: favor established accounts
        else:
            closeness = -3.0  # unknown candidate size: below known ones
        closeness += 0.3 * _relevance(c)  # topical overlap with the main account
        if main_profile.is_verified and c.get("verified"):
            closeness += 0.05
        scored.append((closeness, -i, u))

    scored.sort(reverse=True)
    picked = [u for _, _, u in scored[:count]]
    if not picked:
        picked = [c.get("username") for c in candidates if c.get("username")][:count]
    return CompetitorShortlist(
        picked=picked,
        rationale="Selected deterministically: public accounts, closest follower scale to the main account, same related-accounts cluster.",
    )


def pick_competitors(main_profile: ProfileData, candidates: List[dict], count: int) -> Tuple[List[str], str]:
    """Choose `count` competitor usernames from discovery candidates.
    Returns (usernames, rationale)."""
    count = max(1, min(count, 10))
    llm = _get_llm(temperature=0.0)
    if llm is not None and candidates:
        try:
            from langchain_core.prompts import ChatPromptTemplate

            def row(c: dict) -> str:
                f = c.get("followers")
                return (
                    f"- @{c.get('username')} | followers: {f if f else 'unknown'}"
                    f" | verified: {bool(c.get('verified'))} | private: {bool(c.get('private'))}"
                    f" | bio: {(c.get('bio') or c.get('full_name') or '')[:100]}"
                )

            prompt = ChatPromptTemplate.from_messages(
                [("system", _RESEARCH_SYSTEM), ("human", _PICK_HUMAN)]
            )
            chain = prompt | llm.with_structured_output(CompetitorShortlist)
            result: CompetitorShortlist = chain.invoke({
                "main_facts": _profile_facts(main_profile, compute_metrics(main_profile)),
                "candidates": "\n".join(row(c) for c in candidates[:30]),
                "count": count,
            })
            valid = {c.get("username", "").lower() for c in candidates}
            picked = [u for u in result.picked if u and u.lower() in valid and u.lower() != main_profile.username.lower()]
            if picked:
                return picked[:count], result.rationale
        except Exception:
            pass

    fallback = _rule_based_pick(main_profile, candidates, count)
    return fallback.picked, fallback.rationale


# ---------------------------------------------------------------------------
# Chain 2: market research over the researched competitor set
# ---------------------------------------------------------------------------

_RESEARCH_HUMAN = """Main account:

{main_facts}

Competitor accounts (real fetched data):

{competitor_rows}

Composite ranking (best first): {ranking}

Produce the competitive-intelligence research: where the main account trails
specific rivals (competitive_gaps), which content spaces rivals own that the
main account could take (content_gaps), and concrete opportunities. Cite
actual numbers from the rows — never invent any."""

def _rule_based_market_research(main: ProfileInsight, competitors: List[ProfileInsight]) -> MarketResearch:
    gaps, content_gaps, opportunities = [], [], []

    if main.metrics.engagement_rate < max(c.metrics.engagement_rate for c in competitors):
        best = max(competitors, key=lambda c: c.metrics.engagement_rate)
        gaps.append(
            f"@{best.profile.username} out-engages you ({best.metrics.engagement_rate}% vs "
            f"{main.metrics.engagement_rate}%) — study their {best.metrics.best_content_type} content."
        )
    if main.metrics.posting_frequency_per_week < max(c.metrics.posting_frequency_per_week for c in competitors):
        best = max(competitors, key=lambda c: c.metrics.posting_frequency_per_week)
        gaps.append(
            f"@{best.profile.username} posts more often ({best.metrics.posting_frequency_per_week}/week) — "
            f"cadence gap likely costs you algorithmic reach."
        )
    if main.profile.followers < max(c.profile.followers for c in competitors):
        biggest = max(competitors, key=lambda c: c.profile.followers)
        opportunities.append(
            f"@{biggest.profile.username} shows the audience ceiling in this niche "
            f"({biggest.profile.followers:,} followers) — their growth playbook is worth deconstructing."
        )

    # Content spaces: formats + hashtags rivals use that the main account doesn't.
    my_tags = set(main.metrics.top_hashtags)
    for c in competitors:
        foreign = [t for t in c.metrics.top_hashtags if t not in my_tags][:2]
        if foreign:
            content_gaps.append(
                f"@{c.profile.username} owns {', '.join(foreign)} — adjacent topics you're not covering."
            )
        if c.metrics.best_content_type != main.metrics.best_content_type:
            opportunities.append(
                f"Test {c.metrics.best_content_type} content: it's @{c.profile.username}'s top format "
                f"but not yours."
            )
    content_gaps = content_gaps[:5]
    opportunities = opportunities[:5]

    ranked = sorted([main] + competitors, key=composite_score, reverse=True)
    leader = ranked[0]
    summary = (
        f"Across {len(competitors) + 1} accounts analyzed, @{leader.profile.username} leads on composite "
        f"performance (engagement, cadence, and trust signals combined). "
        f"@{main.profile.username} ranks #{ranked.index(main) + 1} of {len(ranked)}."
    )
    if not gaps:
        gaps.append("You currently lead this competitive set on the metrics sampled — focus on defending position.")
    return MarketResearch(
        market_summary=summary,
        competitive_gaps=gaps[:5],
        content_gaps=content_gaps,
        opportunities=opportunities,
    )


def build_market_research(main: ProfileInsight, competitors: List[ProfileInsight]) -> MarketResearch:
    research = _rule_based_market_research(main, competitors)

    llm = _get_llm()
    if llm is not None:
        try:
            from langchain_core.prompts import ChatPromptTemplate

            def row(i: ProfileInsight) -> str:
                m, p = i.metrics, i.profile
                return (f"- @{p.username} | {p.followers:,} followers | ER {m.engagement_rate}% | "
                        f"{m.posting_frequency_per_week}/week | best format: {m.best_content_type} | "
                        f"verified: {p.is_verified} | top tags: {', '.join(m.top_hashtags) or '(none)'}")

            prompt = ChatPromptTemplate.from_messages(
                [("system", _RESEARCH_SYSTEM), ("human", _RESEARCH_HUMAN)]
            )
            chain = prompt | llm.with_structured_output(MarketResearch)
            llm_research: MarketResearch = chain.invoke({
                "main_facts": _profile_facts(main.profile, main.metrics),
                "competitor_rows": "\n".join(row(c) for c in competitors),
                "ranking": ", ".join(
                    i.profile.username
                    for i in sorted([main] + competitors, key=composite_score, reverse=True)
                ),
            })
            research = llm_research
        except Exception:
            pass  # keep rule-based research on any LLM failure

    return research


def build_market_summary(main: ProfileInsight, competitors: List[ProfileInsight]) -> Tuple[str, List[str]]:
    """Backward-compatible helper: (market_summary, competitive_gaps)."""
    research = build_market_research(main, competitors)
    return research.market_summary, research.competitive_gaps


# ---------------------------------------------------------------------------
# Chain 4: growth plan (content suggestions + follower-growth actions)
# ---------------------------------------------------------------------------

_GROWTH_SYSTEM = (
    "You are an Instagram growth strategist. You build plans ONLY from the "
    "real data provided - never invent follower counts, benchmarks or "
    "performances. Post ideas must be specific and ready to make (title, "
    "format, why, caption hook, hashtags). Growth targets must be honest "
    "ranges, not hype."
)

_GROWTH_HUMAN = """Account to grow:

{facts}

Sampled post performance (last {n_posts} posts, real data):
{post_rows}

{rival_block}

Build a 30-day growth plan for this exact account:
- content_pillars: 3-4 recurring themes that fit what already works here
- post_ideas: 6-8 concrete posts (title, format, why it should earn likes/
  comments/follows FOR THIS ACCOUNT, caption hook, hashtags)
- weekly_schedule: Mon..Sun posting plan matching their current cadence,
  pushing it slightly upward
- format_mix: recommended reel/carousel/image ratio for this account
- hashtag_sets: 3 rotating ready-to-paste sets of 8-12 hashtags matched to
  their niche and size
- engagement_tactics: daily actions that increase comments and follows
- follower_growth_targets: honest 30/60/90-day follower ranges with what
  it takes to hit them (baseline: {followers} followers, {er}% engagement)
- quick_wins: 3 things they can do TODAY"""


def _post_rows(posts) -> str:
    lines = []
    for p in posts[:12]:
        lines.append(
            f"- [{p.media_type}] {p.likes:,} likes, {p.comments:,} comments, "
            f"{p.posted_days_ago}d ago | tags: {', '.join(p.hashtags[:4]) or '(none)'} | "
            f"caption: {(p.caption or '(none)')[:80]}"
        )
    return "\n".join(lines) if lines else "- (no post data available)"


def _rival_block(rivals: List[ProfileInsight]) -> str:
    if not rivals:
        return "(No competitor data fetched - plan from the account's own data only.)"
    lines = ["Competitor accounts (real data) - what is working in this niche:"]
    for r in rivals:
        m, p = r.metrics, r.profile
        lines.append(
            f"- @{p.username} | {p.followers:,} followers | ER {m.engagement_rate}% | "
            f"{m.posting_frequency_per_week}/wk | best format: {m.best_content_type} | "
            f"top tags: {', '.join(m.top_hashtags[:4]) or '(none)'}"
        )
    return "\n".join(lines)


def _rule_based_growth_plan(
    main: ProfileInsight,
    rivals: List[ProfileInsight],
) -> GrowthPlan:
    """Deterministic growth plan grounded in the account's own metrics."""
    m, p = main.metrics, main.profile
    tags = [t.lstrip("#") for t in m.top_hashtags]
    niche = (p.category or "your niche").strip()
    fmt = m.best_content_type if m.best_content_type != "n/a" else "reel"
    cadence = m.posting_frequency_per_week
    target_cadence = min(7, max(3, round(cadence + 1)) if cadence else 4)

    summary = (
        f"@{p.username} posts {cadence}/week with {m.engagement_rate}% engagement "
        f"({ _rate_engagement(m.engagement_rate)}); {fmt} is the strongest format. "
        f"The plan doubles down on {fmt} around {', '.join(tags[:3]) or niche}, "
        f"lifts cadence to {target_cadence}/week, and adds daily comment-first "
        f"engagement to convert reach into followers."
    )

    pillars = [
        f"Educate: beginner mistakes & how-tos in {niche}",
        f"Show proof: results, before/after, behind-the-scenes in {fmt} form",
        f"Converse: questions and hot takes that invite comments",
    ]
    if tags:
        pillars.append(f"Trend-ride: {fmt} takes on trending audio/topics in {', '.join(tags[:2])}")

    ideas = [
        PostIdea(
            title=f"3 {niche} mistakes beginners keep making",
            format="reel",
            why="Mistake-framed hooks earn saves and shares; saves signal the algorithm to push reach, which drives follows.",
            caption_concept="Hook: 'Stop doing #3.' End with: 'Which one are you guilty of? Comment below.'",
            hashtags=[f"#{t}" for t in (tags[:3] or [niche.replace(" ", "")])],
        ),
        PostIdea(
            title=f"How I'd start {niche} from zero in 2026",
            format="carousel",
            why="Step-by-step carousels get saved and re-shared; 'start from zero' framing pulls in new audiences.",
            caption_concept="Slide 1 states the promise, last slide asks: 'Save this for later.'",
            hashtags=[f"#{t}" for t in (tags[1:4] or [niche.replace(" ", "")])],
        ),
        PostIdea(
            title=f"Behind the scenes: how we actually make our {niche} work",
            format="reel",
            why="BTS content humanizes the account - comment rates rise because it invites questions.",
            caption_concept="Real process, no polish. End with 'Ask me anything about this process.'",
            hashtags=[f"#{t}" for t in (tags[:2] + ["behindthescenes"])],
        ),
        PostIdea(
            title=f"Replying to your top comment about {niche}",
            format="video",
            why="Reply-to-comment posts convert existing engagers into repeat commenters and signal a responsive community.",
            caption_concept="Reference the commenter by name, answer in depth, invite the next question.",
            hashtags=[f"#{t}" for t in (tags[:2] or [niche.replace(" ", "")])],
        ),
        PostIdea(
            title=f"Before/after: one week of focused {niche} effort",
            format="carousel",
            why="Transformation content earns high saves and follows - people follow to see the next result.",
            caption_concept="Side-by-side visuals; caption tells what changed and the one habit that mattered.",
            hashtags=[f"#{t}" for t in (tags[:3] or [niche.replace(" ", "")])],
        ),
        PostIdea(
            title=f"The truth about {niche} nobody posts",
            format="reel",
            why="Contrarian takes spark comment debates - the single strongest signal for comments.",
            caption_concept="One bold but defensible claim; caption asks 'Agree or disagree?'",
            hashtags=[f"#{t}" for t in (tags[:2] + [f"{niche.replace(' ', '')}tips"])],
        ),
    ]

    schedule = [
        f"Mon: {fmt} (pillar 1)",
        "Tue: story day - poll + question sticker (no feed post)",
        f"Wed: carousel (pillar 2)",
        f"Thu: {fmt} reply-to-comment or trend-ride",
        f"Fri: {fmt} (pillar 3 - conversation bait)",
        "Sat: story day - BTS + countdown to Sunday post",
        "Sun: carousel or image + CTA to follow for the series",
    ]

    base = "#" + niche.replace(" ", "").lower()
    hashtag_sets = [
        [f"#{t}" for t in (tags[:6] or [niche.replace(" ", "")])] + [f"{base}tips", f"{base}daily", f"{base}community", "#instagramgrowth"],
        ["#reelsinstagram", "#explorepage", "#instagrowth", f"{base}lover", f"{base}oftheday", f"#{niche.replace(' ', '')}2026", f"{base}inspo", "#contentcreator"],
        [f"#{t}" for t in (tags[2:6] or [niche.replace(" ", "")])] + [f"small{niche.replace(' ', '')}business", f"{base}tutorial", "#learnoninstagram", "#creatoreconomy"],
    ]

    tactics = [
        "Reply to every comment within the first hour - early reply velocity is what pushes posts into Explore.",
        "Spend 15 min/day commenting genuine, specific notes on 10 accounts in your niche - visibility that converts.",
        "End every caption with ONE direct question; questions beat 'double tap' CTAs for comment counts.",
        "Post stories daily with a poll or slider - story interactions lift your feed ranking.",
        "Pin your 3 best-performing posts as a first-impression wall for new visitors.",
        f"Publish {fmt} content before 10am or 6-9pm local time when your audience is most active.",
    ]

    targets = [
        f"30 days: +{max(2, round(m.engagement_rate * 5))}-{max(5, round(m.engagement_rate * 10))}% relative engagement lift if cadence hits {target_cadence}/week with reels-led content.",
        f"60 days: first compounding follower gains from Explore placement on 1-2 {fmt} posts per month.",
        "90 days: follower growth tracks engagement growth - the honest lever is cadence + reply velocity, not follow/unfollow tricks.",
        "Note: bought followers are not a target - they suppress engagement rate and kill reach.",
    ]

    quick_wins = [
        f"Rewrite the bio to state who you help and how - then pin your best {fmt} post.",
        "Add a question sticker to today's story to harvest comment fodder for this week's posts.",
        "Reply to every unanswered comment on your last 5 posts right now.",
    ]

    return GrowthPlan(
        summary=summary,
        content_pillars=pillars[:4],
        post_ideas=ideas,
        weekly_schedule=schedule,
        format_mix=f"Aim for ~{max(2, target_cadence - 2)} {fmt}/week, 1-2 carousels, 1 conversation post; stories daily.",
        hashtag_sets=hashtag_sets,
        engagement_tactics=tactics,
        follower_growth_targets=targets,
        quick_wins=quick_wins,
    )


def build_growth_plan(main: ProfileInsight, rivals: Optional[List[ProfileInsight]] = None) -> GrowthPlan:
    """Content + follower-growth plan. LangChain chain when a key is set,
    deterministic planner otherwise. Both are grounded ONLY in real data."""
    rivals = rivals or []
    plan = _rule_based_growth_plan(main, rivals)

    llm = _get_llm()
    if llm is not None:
        try:
            from langchain_core.prompts import ChatPromptTemplate
            prompt = ChatPromptTemplate.from_messages(
                [("system", _GROWTH_SYSTEM), ("human", _GROWTH_HUMAN)]
            )
            chain = prompt | llm.with_structured_output(GrowthPlan)
            llm_plan: GrowthPlan = chain.invoke({
                "facts": _profile_facts(main.profile, main.metrics),
                "n_posts": len(main.profile.recent_posts),
                "post_rows": _post_rows(main.profile.recent_posts),
                "rival_block": _rival_block(rivals),
                "followers": f"{main.profile.followers:,}",
                "er": main.metrics.engagement_rate,
            })
            # Sanity: never let an LLM hallucinate an empty plan.
            if llm_plan.post_ideas and llm_plan.content_pillars:
                plan = llm_plan
        except Exception:
            pass

    return plan


# ---------------------------------------------------------------------------
# Chain 5: Instagram trend detection + content suggestions
# ---------------------------------------------------------------------------

# A curated set of known recurring Instagram trend patterns. These are
# archetypes that tend to reappear in waves (visual styles, audio formats,
# challenges). New trends are spotted by matching against recent post captions
# and hashtags from the account's niche.
#
# In production this would be fed by a real-time trend API; here we provide
# a starter catalog plus a deterministic matcher that flags anything the
# account's own posts are already touching.

TREND_CATALOG: List[dict] = [
    {
        "name": "Vintage/Retro Filter Aesthetic",
        "description": (
            "Accounts mass-post retro-styled photos (80s/Y2K/vintage film look) "
            "often triggered by a new filter, template or AI image tool going viral."
        ),
        "category": "visual",
        "hashtags": ["#retro", "#vintage", "#80saesthetic", "#y2k", "#filmlook", "#throwback"],
        "signals": ["80s", "90s", "vintage", "retro", "film", "y2k", "throwback", "nostalgia", "filter", "vintage photo", "vintage aesthetic"],
    },
    {
        "name": "AI-Generated Image Challenge",
        "description": (
            "A surge of AI-generated images (ChatGPT, Midjourney, DALL-E style) "
            "posted as a series — often a specific prompt theme everyone copies."
        ),
        "category": "visual",
        "hashtags": ["#aiart", "#midjourney", "#chatgpt", "#generativeart", "#aiimages", "#aichallenge"],
        "signals": ["ai generated", "chatgpt", "midjourney", "ai art", "prompt", "ai image", "generative", "dall-e"],
    },
    {
        "name": "Trending Audio/Reel Sound",
        "description": (
            "A specific audio clip or song snippet spikes in usage across Reels "
            "- riding it early can push a reel into Explore."
        ),
        "category": "audio",
        "hashtags": ["#trendingaudio", "#reelsound", "#viralaudio", "#trendingreels"],
        "signals": ["trending audio", "viral sound", "reels audio", "trending sound", "use this sound"],
    },
    {
        "name": "Photo Dump / Week in My Life",
        "description": (
            "Carousel of casual, unfiltered photos from the week — a low-effort "
            "high-engagement format that resurfaces constantly."
        ),
        "category": "format",
        "hashtags": ["#photodump", "#weekincars", "#lifestyle", "#myweek", "#candid"],
        "signals": ["photo dump", "week in", "my week", "life lately", "current mood", "candid", "carousell"],
    },
    {
        "name": "Before/After Transformation",
        "description": (
            "Side-by-side or swipe transformation posts (fitness, home, makeup, "
            "design). High save rate, strong follow conversion."
        ),
        "category": "format",
        "hashtags": ["#beforeandafter", "#transformation", "#results", "#glowup", "#makeover"],
        "signals": ["before and after", "transformation", "before/after", "results", "glow up", "makeover", "change"],
    },
    {
        "name": "Day in the Life / Routine Reel",
        "description": (
            "Fast-cut vlog-style reel showing a morning/night routine or a day "
            "in a specific role (student, creator, founder, etc.)."
        ),
        "category": "format",
        "hashtags": ["#dayinthelife", "#routine", "#morningroutine", "#vlog", "#mylife"],
        "signals": ["day in the life", "routine", "morning routine", "night routine", "vlog", "a day in"],
    },
    {
        "name": "Hot Take / Controversial Opinion",
        "description": (
            "A bold, slightly contrarian take on a niche topic — designed to "
            "spark comments and debate. Comments drive algorithmic reach."
        ),
        "category": "challenge",
        "hashtags": ["#hottake", "#opinion", "#untouched", "#controversial", "#realtalk"],
        "signals": ["hot take", "unpopular opinion", "contrarian", "here's the truth", "nobody says this", "controversial"],
    },
    {
        "name": "Tutorial / How-To Reel",
        "description": (
            "Quick step-by-step tutorial in Reel form — high save rate, strong "
            "for authority-building in any niche."
        ),
        "category": "format",
        "hashtags": ["#tutorial", "#howto", "#tips", "#learn", "#stepbystep"],
        "signals": ["how to", "tutorial", "tips", "step by step", "giveaway", "learn", "this is how"],
    },
    {
        "name": "Behind the Scenes / Process",
        "description": (
            "Raw, unpolished look at how something is made or done — humanizes "
            "the account and invites questions in comments."
        ),
        "category": "visual",
        "hashtags": ["#bts", "#behindthescenes", "#process", "#howitsmade", "#workspace"],
        "signals": ["behind the scenes", "bts", "process", "how we", "how i", "workspace", "studio"],
    },
    {
        "name": "Meme / Relatable Comedy",
        "description": (
            "Light-hearted meme or relatable joke post in the niche — broad "
            "reach, shares, and comment engagement."
        ),
        "category": "challenge",
        "hashtags": ["#meme", "#relatable", "#funny", "#comedy", "#lol"],
        "signals": ["meme", "relatable", "funny", "lol", "haha", "same", "me IRL", "POV"],
    },
    {
        "name": "Color Filter / Photo Effect Challenge",
        "description": (
            "A specific color grade, filter or editing effect that a lot of "
            "accounts suddenly adopt — often tied to a new Lightroom preset or app."
        ),
        "category": "filter",
        "hashtags": ["#colorgrade", "#filter", "#preset", "#editing", "#photoeffect"],
        "signals": ["color grade", "filter", "preset", "color tone", "dreamy", "aesthetic edit", "editing style"],
    },
    {
        "name": " 챌린지 / Challenge Format",
        "description": (
            "A defined challenge (dance, art, photo, answer-a-question) that "
            "accounts copy with their own spin. Participation = visibility."
        ),
        "category": "challenge",
        "hashtags": ["#challenge", "#challengeaccepted", "#trying", "#participating"],
        "signals": ["challenge", "I accepted", "trying the", "participating in", "took the challenge", "doing the"],
    },
]


def _detect_trends_from_posts(posts: List[Post], profile_username: str, profile_category: Optional[str] = None, profile_bio: Optional[str] = None, profile_tags: Optional[List[str]] = None) -> List[TrendInfo]:
    """Scan an account's recent post captions/hashtags AND profile metadata
    (category, bio, top hashtags) for signals matching known trend archetypes.
    Returns any trends the account is already touching or could ride.

    Also seeds category-relevant catalog trends when the account's own posts
    don't carry strong signals, so every account gets niche-relevant trend
    suggestions on first run.
    """
    # Aggregate all caption text + hashtags from recent posts.
    all_text_chunks: List[str] = []
    for p in posts:
        text = (p.caption or "").lower()
        tags = " ".join(p.hashtags or []).lower()
        all_text_chunks.append(text)
        all_text_chunks.append(tags)

    # Also fold in profile-level signals: category + bio + top hashtags.
    profile_text = " ".join(
        [x.lower() for x in [profile_category, profile_bio] if x]
    )
    tag_text = " ".join(t.lower().lstrip("#") for t in (profile_tags or []))
    combined = " ".join([*all_text_chunks, profile_text, tag_text])

    found: List[TrendInfo] = []
    seen_names: set = set()

    for trend in TREND_CATALOG:
        name = trend["name"]
        if name in seen_names:
            continue
        signals = [s.lower() for s in trend["signals"]]
        hits = sum(1 for sig in signals if sig in combined)
        if hits >= 1:
            seen_names.add(name)
            recent_hit = any(
                any(sig in (p.caption or "").lower() or sig in " ".join(p.hashtags or []).lower()
                    for sig in signals)
                for p in posts if p.posted_days_ago <= 7
            )
            found.append(TrendInfo(
                name=name,
                description=trend["description"],
                category=trend["category"],
                hashtags=trend["hashtags"],
                started_days_ago=min((p.posted_days_ago for p in posts if p.posted_days_ago <= 7), default=14) if recent_hit else 14,
                is_rising=recent_hit,
            ))

    # If no signals detected at all, seed with category-relevant catalog trends
    # so every account gets meaningful trend suggestions on first run.
    if not found and (profile_category or profile_tags):
        niche = (profile_category or "").lower()
        # Score catalog trends by how relevant their hashtags are to the niche.
        niche_tokens = {w for w in niche.split() if len(w) >= 3}
        scored = []
        for t in TREND_CATALOG:
            text = " ".join(t["hashtags"]).lower() + " " + " ".join(t["signals"]).lower()
            overlap = sum(1 for tok in niche_tokens if tok in text)
            scored.append((overlap, id(t), t))
        scored.sort(reverse=True)
        # Pick top 2-3 category-relevant trends.
        picks = [t for _, _, t in scored[:3]][:3]
        for t in picks:
            if t["name"] in seen_names:
                continue
            seen_names.add(t["name"])
            found.append(TrendInfo(
                name=t["name"],
                description=t["description"],
                category=t["category"],
                hashtags=t["hashtags"],
                started_days_ago=3,
                is_rising=True,
            ))

    # Absolute fallback: pick any 2 catalog trends if still nothing.
    if not found:
        for t in TREND_CATALOG[:2]:
            if t["name"] in seen_names:
                continue
            seen_names.add(t["name"])
            found.append(TrendInfo(
                name=t["name"],
                description=t["description"],
                category=t["category"],
                hashtags=t["hashtags"],
                started_days_ago=5,
                is_rising=True,
            ))

    return found


_TREND_ALERT_SYSTEM = (
    "You are an Instagram trend-spotter and content strategist. You spot rising "
    "trends and translate each one into a concrete, ready-to-post idea for a "
    "specific account. You never invent numbers or facts about the account — "
    "everything is grounded in the provided profile data."
)

_TREND_ALERT_HUMAN = """Account being advised:

{account_facts}

Currently active trend(s) detected on Instagram:

{trend_block}

For EACH trend above, produce a TrendAlert:
- why_relevant: why this trend matters specifically for THIS account
- suggested_post_idea: a concrete static post idea to ride the trend
- suggested_reel_idea: a concrete reel idea to ride the trend
- caption_hook: a strong opening line for the caption
- recommended_hashtags: 8-12 ready-to-paste hashtags (mix niche + trend tags)
- posting_tip: format/timing tip to maximize reach for this trend

Make each alert specific and actionable — not generic."""


def _rule_based_trend_alerts(
    profile: ProfileData,
    metrics: ProfileMetrics,
    detected_trends: List[TrendInfo],
) -> List[TrendAlert]:
    """Deterministic trend alerts: one alert per detected trend, grounded in
    the account's own data."""
    alerts: List[TrendAlert] = []
    niche = (profile.category or profile.full_name or "your niche").strip()
    tags = [t.lstrip("#") for t in metrics.top_hashtags]
    fallback_tag = niche.lower().replace(" ", "")
    base_tags = tags[:3] if tags else [fallback_tag]

    for trend in detected_trends:
        trend_tags = [t for t in trend.hashtags[:4]]  # already includes # prefix
        niche_tags = [f"#{t}" for t in base_tags[:3]]
        all_tags = list(dict.fromkeys(trend_tags + niche_tags + ["#instagram", "#trending"]))[:12]

        alert = TrendAlert(
            trend=trend,
            why_relevant=(
                f"This trend is surging right now and fits your {niche} content. "
                f"Riding it early lets you tap into existing search and Explore demand "
                f"before the feed gets saturated."
            ),
            suggested_post_idea=(
                f"A carousel or single image that plays into the '{trend.name}' trend: "
                f"match the visual style everyone's using, but add your own {niche} angle."
            ),
            suggested_reel_idea=(
                f"A Reel using the same visual/audio cue as the trend — fast cuts, "
                f"showing your {niche} take on it in under 15 seconds. Hook in the first frame."
            ),
            caption_hook=(
                f"'Have you seen everyone posting about {trend.name.lower()} lately? Here's my {niche} version…'"
            ),
            recommended_hashtags=all_tags,
            posting_tip=(
                f"Post within the first 48-72 hours of spotting this trend for maximum "
                f"Explore exposure. Use Reels for reach, carousels for saves."
            ),
        )
        alerts.append(alert)

    return alerts


def _rule_based_trend_suggestions(
    alerts: List[TrendAlert],
    category: str,
) -> List[TrendSuggestion]:
    """Turn trend alerts into ready-to-make content suggestions."""
    suggestions: List[TrendSuggestion] = []
    for alert in alerts:
        t = alert.trend
        if t.category == "audio":
            fmt = "reel"
            concept = f"Use the trending audio '{t.name}' — create a Reel that syncs your {category} content to the beat."
        elif t.category == "challenge":
            fmt = "reel"
            concept = f"Participate in the '{t.name}' challenge — show your unique take on it in a Reel or carousel."
        elif t.category == "filter":
            fmt = "image"
            concept = f"Use the trending {t.name} filter/effect on your best {category} photo and caption with the story behind it."
        else:
            fmt = "reel"
            concept = f"Create a {t.category}-style post riding the '{t.name}' trend — match the aesthetic but bring your {category} perspective."

        suggestions.append(TrendSuggestion(
            title=f"Ride the '{t.name}' trend",
            format=fmt,
            concept=concept,
            caption=alert.caption_hook,
            hashtags=alert.recommended_hashtags,
            why=alert.why_relevant,
        ))

    return suggestions


def generate_trend_alerts(
    profile: ProfileData,
    metrics: ProfileMetrics,
    recent_posts: List[Post],
) -> TrendsResponse:
    """Detect active Instagram trends and produce alerts + ready-to-make
    content suggestions for the account.

    In live mode this would call a real-time trend API. Here we:
      1. Scan the account's own recent posts for signals matching known trend
         archetypes (so we spot trends the account is already touching).
      2. Supplement with a hand-curated catalog of recurring trend patterns.
      3. Generate a concrete alert + post/reel idea per detected trend.

    The output shape is stable; swap the detection layer for a real trend API
    without touching the rest of the app.
    """
    detected = _detect_trends_from_posts(
        recent_posts,
        profile.username,
        profile_category=profile.category,
        profile_bio=profile.bio,
        profile_tags=metrics.top_hashtags,
    )

    alerts = _rule_based_trend_alerts(profile, metrics, detected)
    suggestions = _rule_based_trend_suggestions(alerts, profile.category or "your niche")

    return TrendsResponse(
        trending=detected,
        alerts=alerts,
        suggestions=suggestions,
    )


# ---------------------------------------------------------------------------
# Chain 6: content whitespace finder + caption suggestions
# ---------------------------------------------------------------------------

# Standard content themes for Instagram. Detection is keyword-based over the
# account's real captions/hashtags — the same deterministic approach the
# trend engine uses. The whitespace finder measures coverage across these.
THEME_CATALOG: List[dict] = [
    {
        "theme": "Educational / How-to",
        "keywords": ["how to", "guide", "tips", "tutorial", "learn", "step", "beginner", "mistakes", "mistake", "101", "explain", "what is", "why you"],
        "formats": ["carousel", "reel"],
        "hashtags": ["#howto", "#tips", "#tutorial", "#learning"],
        "opportunity": "Teach one thing you know that your audience keeps asking. How-to carousels get saved and re-shared — saves are the strongest reach signal.",
    },
    {
        "theme": "Behind-the-scenes",
        "keywords": ["behind", "bts", "process", "workshop", "studio", "making", "in progress", "wip", "how we", "our team", "a day in"],
        "formats": ["reel", "story"],
        "hashtags": ["#bts", "#behindthescenes", "#process", "#maker"],
        "opportunity": "Show the messy middle — raw process footage builds trust faster than polished finals. One phone-shot reel of your real workflow.",
    },
    {
        "theme": "Social proof / Results",
        "keywords": ["result", "before", "after", "transformation", "review", "testimonial", "client", "customer", "feedback", "win", "milestone", "case study"],
        "formats": ["carousel", "image"],
        "hashtags": ["#results", "#testimonial", "#beforeandafter", "#casestudy"],
        "opportunity": "Turn one real outcome (a client win, a before/after, a milestone) into a carousel. Proof converts followers into buyers.",
    },
    {
        "theme": "Personality / Founder story",
        "keywords": ["i am", "my story", "we started", "our journey", "i believe", "honest", "personal", "grateful", "thank you", "anniversary"],
        "formats": ["reel", "image"],
        "hashtags": ["#mystory", "#founder", "#smallbusiness", "#journey"],
        "opportunity": "Post one founder-to-camera reel or a personal caption. People follow people — personality posts lift comment rates.",
    },
    {
        "theme": "Interactive / Community",
        "keywords": ["comment", "vote", "question", "ask", "tell us", "which one", "your favorite", "dm", "poll", "q&a", "ama"],
        "formats": ["reel", "carousel"],
        "hashtags": ["#qanda", "#community", "#askme"],
        "opportunity": "End posts with a direct question or a this-or-that prompt. Comments in the first hour decide how far a post travels.",
    },
    {
        "theme": "Product / Offer showcase",
        "keywords": ["new", "launch", "available", "shop", "order", "price", "stock", "drop", "collection", "sale", "link in bio"],
        "formats": ["image", "carousel"],
        "hashtags": ["#newlaunch", "#shopsmall", "#newdrop"],
        "opportunity": "Keep selling — but frame every product post around the buyer's problem, not the product's features.",
    },
    {
        "theme": "Trend / Entertainment",
        "keywords": ["trend", "viral", "meme", "funny", "relatable", "pov", "challenge", "audio"],
        "formats": ["reel"],
        "hashtags": ["#trending", "#reels", "#viral"],
        "opportunity": "Ride one trend a week through your niche's lens — entertainment reach is the cheapest follower acquisition there is.",
    },
]


def _classify_post_theme(post: Post) -> str:
    """Return the THEME_CATALOG theme name that best matches a post's caption
    and hashtags, or '' when nothing matches."""
    text = (post.caption or "").lower()
    tags = " ".join(post.hashtags).lower()
    best_theme, best_hits = "", 0
    for t in THEME_CATALOG:
        hits = sum(1 for kw in t["keywords"] if kw in text) \
               + sum(1 for kw in t["keywords"] if kw in tags)
        if hits > best_hits:
            best_theme, best_hits = t["theme"], hits
    return best_theme


def _rule_based_whitespace(
    insight: ProfileInsight,
    rivals: Optional[List[ProfileInsight]] = None,
) -> tuple:
    """Deterministic whitespace + coverage analysis over real post data.

    Returns (coverage: List[ThemeCoverage], whitespace: List[WhitespaceArea],
    summary: str)."""
    rivals = rivals or []
    posts = insight.profile.recent_posts
    m = insight.metrics
    n = len(posts)

    # Account-wide engagement baseline.
    avg_all = (m.avg_likes + m.avg_comments) if n else 0.0

    # Per-theme coverage for the main account.
    coverage: List[ThemeCoverage] = []
    theme_posts: dict = {}
    for t in THEME_CATALOG:
        matched = [p for p in posts if _classify_post_theme(p) == t["theme"]]
        theme_posts[t["theme"]] = matched
        share = round(len(matched) / n * 100, 1) if n else 0.0
        eng = round(sum(p.likes + p.comments for p in matched) / len(matched), 1) if matched else 0.0
        if not matched:
            verdict = "untouched"
        elif share < 10:
            verdict = "underused"
        elif share > 40:
            verdict = "overused"
        else:
            verdict = "balanced"
        example = matched[0].caption[:90] + ("…" if len(matched[0].caption) > 90 else "") if matched else ""
        coverage.append(ThemeCoverage(
            theme=t["theme"],
            post_count=len(matched),
            share_pct=share,
            avg_engagement=eng,
            avg_engagement_all=round(avg_all, 1),
            verdict=verdict,
            example_caption=example,
        ))

    # Do the rivals cover a theme this account ignores?
    def rivals_cover(theme: str) -> bool:
        for r in rivals:
            for p in r.profile.recent_posts:
                if _classify_post_theme(p) == theme:
                    return True
        return False

    whitespace: List[WhitespaceArea] = []
    for t in THEME_CATALOG:
        matched = theme_posts[t["theme"]]
        cov = next(c for c in coverage if c.theme == t["theme"])
        if not matched:
            ws_type = "untouched"
            desc = (f"@{insight.profile.username} hasn't posted any '{t['theme']}' content in their "
                    f"last {n} posts.")
            if rivals and rivals_cover(t["theme"]):
                desc += " Researched rivals are already active here — they own this space by default."
            whitespace.append(WhitespaceArea(
                theme=t["theme"], gap_type=ws_type, description=desc,
                opportunity=t["opportunity"],
                suggested_format=t["formats"][0],
                hashtags=t["hashtags"],
                rivals_own_it=bool(rivals) and rivals_cover(t["theme"]),
            ))
        elif cov.share_pct < 10:
            whitespace.append(WhitespaceArea(
                theme=t["theme"], gap_type="underused",
                description=(f"Only {len(matched)} of {n} recent posts ({cov.share_pct}%) touch "
                             f"'{t['theme']}' — and that content averages {cov.avg_engagement} "
                             f"engagement vs {round(avg_all, 1)} account-wide."),
                opportunity=t["opportunity"],
                suggested_format=t["formats"][0],
                hashtags=t["hashtags"],
                rivals_own_it=False,
            ))
        elif cov.share_pct > 40:
            whitespace.append(WhitespaceArea(
                theme=t["theme"], gap_type="overused",
                description=(f"{cov.share_pct}% of recent posts are '{t['theme']}' — the feed is "
                             f"mono-thematic, which reads as repetitive to new visitors."),
                opportunity=("Rebalance: for every showcase post this week, add one post from "
                             "an untouched theme below."),
                suggested_format=t["formats"][0],
                hashtags=t["hashtags"],
                rivals_own_it=False,
            ))

    # Summary of the whitespace landscape.
    untouched = [w.theme for w in whitespace if w.gap_type == "untouched"]
    summary = (
        f"Across the last {n} posts, @{insight.profile.username} covers "
        f"{len([c for c in coverage if c.post_count > 0])} of {len(THEME_CATALOG)} standard content themes."
    )
    if untouched:
        summary += f" Fully untouched: {', '.join(untouched[:3])}."
    over = [w.theme for w in whitespace if w.gap_type == "overused"]
    if over:
        summary += f" Over-indexed on: {', '.join(over[:2])}."
    if not whitespace:
        summary += " The content mix is well balanced — scale what works."

    return coverage, whitespace, summary


def _rule_based_captions(insight: ProfileInsight, whitespace: List[WhitespaceArea]) -> List[CaptionSuggestion]:
    """Ready-to-post captions grounded in the account's real numbers."""
    p, m = insight.profile, insight.metrics
    niche = p.category or "your niche"
    followers = f"{p.followers:,}"
    captions: List[CaptionSuggestion] = []

    # 1. Caption closing the biggest whitespace (first untouched/underused area).
    if whitespace:
        ws = whitespace[0]
        captions.append(CaptionSuggestion(
            title=f"Close the gap: {ws.theme}",
            caption=(
                f"Something we've never shown you: {ws.theme.lower()}. \n\n"
                f"Most {niche} accounts keep this part invisible — but it's where the "
                f"real work happens. Save this for the next time you're stuck. \n\n"
                f"Which part surprised you? Tell me in the comments. 👇"
            ),
            hashtags=ws.hashtags,
            format=ws.suggested_format,
            why=f"'{ws.theme}' is {ws.gap_type} on this account — posting it differentiates the feed immediately.",
        ))

    # 2. Social-proof caption quoting the account's real engagement.
    captions.append(CaptionSuggestion(
        title="Proof post",
        caption=(
            f"{followers} people trust this page. That number still breaks my brain. \n\n"
            f"If you're one of the {int(m.avg_likes):,} who liked our last post — this one's "
            f"for you. Here's what's next: \n\n"
            f"[your next drop / result / lesson here]"
        ),
        hashtags=["#community", "#thankyou"],
        format="image",
        why="Milestone + gratitude posts reliably lift comments and shares; grounded in this account's actual follower and like counts.",
    ))

    # 3. Interactive caption — the comment-driving lever.
    captions.append(CaptionSuggestion(
        title="This-or-that engagement post",
        caption=(
            f"A or B? \n\nA: [option one]\nB: [option two]\n\n"
            f"Comment your pick — I'll reply to every single one in the first hour. \n\n"
            f"(Hot take: most people pick B. Prove me wrong.)"
        ),
        hashtags=["#thisorthat", "#communityfirst"],
        format="reel",
        why="Direct comment prompts + a reply commitment are the fastest lever on this account's comment rate.",
    ))

    # 4. Educational caption referencing the account's best format.
    captions.append(CaptionSuggestion(
        title="Teach one thing",
        caption=(
            f"3 things I wish someone told me about {niche.lower()} when I started: \n\n"
            f"1. [the mistake that cost you most]\n"
            f"2. [the shortcut you found late]\n"
            f"3. [the habit that changed everything]\n\n"
            f"Save this — future you will need it."
        ),
        hashtags=["#tips", "#howto", "#learn"],
        format="carousel",
        why=(f"Educational carousels get saved; '{m.best_content_type}' is already this account's "
             f"strongest format, and saves extend reach beyond followers."),
    ))

    return captions


def generate_whitespace_and_captions(
    insight: ProfileInsight,
    rivals: Optional[List[ProfileInsight]] = None,
) -> WhitespaceResponse:
    """Content whitespace finder + caption suggestions.

    Deterministic engine grounded in real post data (same principle as the
    trend engine); optionally refined by a LangChain chain when a key is set.
    """
    coverage, whitespace, summary = _rule_based_whitespace(insight, rivals)
    captions = _rule_based_captions(insight, whitespace)
    warnings: List[str] = []

    llm = _get_llm()
    if llm is not None:
        try:
            from langchain_core.prompts import ChatPromptTemplate

            coverage_block = "\n".join(
                f"- {c.theme}: {c.post_count} posts ({c.share_pct}%), avg engagement {c.avg_engagement} (account avg {c.avg_engagement_all}) [{c.verdict}]"
                for c in coverage
            )
            rival_block = "\n".join(
                f"- @{r.profile.username}: {r.profile.followers:,} followers, ER {r.metrics.engagement_rate}%, "
                f"themes: {', '.join(sorted(set(filter(None, (_classify_post_theme(p) for p in r.profile.recent_posts)))) or ['(unclassified)'])}"
                for r in rivals
            ) or "(no rivals researched)"

            prompt = ChatPromptTemplate.from_messages([
                ("system",
                 "You are an Instagram content strategist. You ground every claim in the "
                 "provided data — never invent numbers. Captions must be ready to post, "
                 "human-sounding, and specific to this account."),
                ("human",
                 "Account: @{username} ({followers} followers, ER {er}%, category {category})\n"
                 "Theme coverage of the last {n_posts} posts:\n{coverage}\n\n"
                 "Researched rivals:\n{rivals}\n\n"
                 "Identify the content whitespace and write 4 ready-to-post captions "
                 "targeting those gaps."),
            ])
            chain = prompt | llm.with_structured_output(WhitespaceResponse)
            llm_result: WhitespaceResponse = chain.invoke({
                "username": insight.profile.username,
                "followers": f"{insight.profile.followers:,}",
                "er": insight.metrics.engagement_rate,
                "category": insight.profile.category or "unknown",
                "n_posts": len(insight.profile.recent_posts),
                "coverage": coverage_block,
                "rivals": rival_block,
            })
            # Never let an LLM hallucinate away the real numbers.
            if llm_result.captions and llm_result.whitespace:
                llm_result.main = insight
                llm_result.coverage = coverage  # deterministic numbers win
                llm_result.warnings = warnings
                return llm_result
        except Exception:
            pass

    return WhitespaceResponse(
        main=insight,
        coverage=coverage,
        whitespace=whitespace,
        captions=captions,
        summary=summary,
        warnings=warnings,
    )
