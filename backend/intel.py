"""
Deep-intel layers — deterministic analyzers over data the scraper already
fetched (no extra network calls):

- analyze_audience()        — who the audience likely is, from real engagement
- detect_trending_topics()  — trending topics in the account's niche sample
- analyze_rival_content()   — competitor content mix vs the account
- suggest_viral_hooks()     — hook patterns proven by the account's top posts
- rank_top_content()        — the account's best-performing recent posts
- track_rival_growth()      — competitors' follower/ER trajectories over time

Everything is computed from ProfileInsight objects; storage-backed growth
tracking reads the same scans table the dashboard history uses. Like the
rest of the app: no fabricated numbers — when the sample is too thin the
response says so (enough_data=False).
"""
from collections import defaultdict
from typing import Dict, List, Optional

from models import (
    AudienceActiveHour,
    AudienceAnalysis,
    AudienceFormatAffinity,
    CompetitorContentAnalysis,
    RivalContentComparison,
    RivalContentOverlap,
    RivalGrowthEntry,
    RivalGrowthPoint,
    RivalGrowthResponse,
    TopContentItem,
    TopContentResponse,
    TrendingTopic,
    TrendingTopicsResponse,
    IntelResponse,
    ViralHook,
    ViralHooksResponse,
)
from models import ProfileInsight

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "the", "and", "for", "with", "your", "you", "this", "that", "have",
    "from", "are", "was", "were", "will", "our", "out", "get", "got", "has",
    "had", "how", "why", "what", "when", "who", "all", "can", "not", "but",
    "its", "it's", "we're", "they", "them", "then", "than", "now", "new",
    "one", "two", "just", "like", "more", "very", "into", "over", "about",
    "here", "there", "been", "being", "into", "also", "day", "days",
}


def _eng(post) -> int:
    return int(post.likes or 0) + int(post.comments or 0)


def _overall_avg(insight: ProfileInsight) -> float:
    posts = insight.profile.recent_posts or []
    if not posts:
        return 0.0
    return sum(_eng(p) for p in posts) / len(posts)


def _index(value: float, baseline: float) -> int:
    """Value vs baseline as an index (100 = baseline)."""
    if baseline <= 0:
        return 100 if value <= 0 else 200
    return int(round(value / baseline * 100))


def _first_line(caption: str, limit: int = 90) -> str:
    line = (caption or "").strip().splitlines()[0].strip()
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line


def _fmt_mix(posts) -> Dict[str, int]:
    mix: Dict[str, int] = defaultdict(int)
    for p in posts:
        mix[(p.media_type or "image").lower()] += 1
    return dict(mix)


# ---------------------------------------------------------------------------
# 1) Audience analysis — inferred from real posting signal
# ---------------------------------------------------------------------------

def analyze_audience(insight: ProfileInsight) -> AudienceAnalysis:
    """Infer who the audience is and when it shows up, from the account's
    own engagement behavior. Instagram exposes follower demographics only to
    the account owner via the official API, so every conclusion here is an
    inference — the response says so in `caveats`."""
    profile = insight.profile
    posts = profile.recent_posts or []
    m = insight.metrics

    enough = len(posts) >= 4
    if not enough:
        return AudienceAnalysis(
            summary="Not enough recent posts to infer an audience picture yet.",
            audience_profile="",
            engagement_quality="",
            enough_data=False,
            caveats=["Re-analyze once the account has at least ~4 recent posts."],
        )

    # --- Active hours: bucket real post timestamps by 3h UTC window, weight
    # by engagement. Same approach as analytics.compute_best_times, but
    # expressed as audience windows rather than posting slots.
    buckets: Dict[int, List[int]] = defaultdict(list)
    undated = 0
    for p in posts:
        ts = (p.posted_at or "").strip()
        hour = None
        if ts:
            try:
                hour = int(ts[11:13])  # ISO 'YYYY-MM-DDTHH:MM'
            except (ValueError, TypeError):
                hour = None
        if hour is None:
            undated += 1
            continue
        buckets[hour // 3 * 3].append(_eng(p))

    active_hours: List[AudienceActiveHour] = []
    for slot, engs in sorted(buckets.items(), key=lambda kv: sum(kv[1]) / len(kv[1]), reverse=True):
        active_hours.append(
            AudienceActiveHour(
                hour=slot,
                label=f"{slot:02d}-{(slot + 3) % 24:02d} UTC",
                avg_engagement=round(sum(engs) / len(engs), 1),
                samples=len(engs),
                share_of_sample=int(round(len(engs) / len(posts) * 100)),
            )
        )

    # --- Format affinity: engagement index per format
    by_fmt: Dict[str, List[int]] = defaultdict(list)
    for p in posts:
        by_fmt[(p.media_type or "image").lower()].append(_eng(p))
    overall = _overall_avg(insight)
    affinity = [
        AudienceFormatAffinity(
            format=fmt,
            posts=len(engs),
            avg_engagement=round(sum(engs) / len(engs), 1),
            engagement_index=_index(sum(engs) / len(engs), overall),
        )
        for fmt, engs in by_fmt.items()
    ]
    affinity.sort(key=lambda a: a.engagement_index, reverse=True)

    # --- Niche signals: hashtags the account actually uses (the audience's
    # topical world), from the real metrics.
    niche_signals = list(m.top_hashtags[:8])

    # --- Engagement quality: comment share of engagement (real numbers).
    total_likes = sum(p.likes or 0 for p in posts)
    total_comments = sum(p.comments or 0 for p in posts)
    total = total_likes + total_comments
    comment_share = (total_comments / total * 100) if total > 0 else 0.0
    if comment_share >= 8:
        quality = (
            f"Conversation-driving audience — comments are {comment_share:.0f}% of all "
            f"engagement, well above the ~2-5% typical on Instagram."
        )
    elif comment_share >= 3:
        quality = (
            f"Balanced audience — comments are {comment_share:.0f}% of engagement; "
            f"questions and CTAs in captions should lift this further."
        )
    else:
        quality = (
            f"Passive-heavy audience — only {comment_share:.0f}% of engagement is "
            f"comments. Drive replies with question captions and comment-bait formats."
        )

    # --- Audience profile inference: from category, bio and niche tags.
    cat = (profile.category or "").strip()
    tag_hint = ", ".join(m.top_hashtags[:3]) if m.top_hashtags else ""
    bio_hint = (profile.bio or "").strip().split(".")[0][:120]
    audience_profile = (
        f"Followers engaging with {('@' + profile.username)} most likely follow for "
        + (f"the {cat.lower()} content" if cat else "the core content")
        + (f" (signal tags: {tag_hint})" if tag_hint else "")
        + (f' — bio promise: "{bio_hint}"' if bio_hint else "")
        + ". The format and timing patterns below show how that audience actually behaves."
    )

    windows_txt = ", ".join(a.label for a in active_hours[:3]) if active_hours else "n/a (posts lack timestamps)"
    top_fmt = affinity[0].format if affinity else "n/a"
    summary = (
        f"Sampled {len(posts)} recent posts: the audience engages most in {windows_txt} "
        f"and responds best to {top_fmt} content. Comment share of engagement: "
        f"{comment_share:.0f}%."
    )

    caveats = [
        "Follower age/gender/location are private to the account owner (official "
        "insights API only) — this read is inferred from observed engagement, "
        "not platform demographics.",
    ]
    if undated:
        caveats.append(f"{undated} of {len(posts)} sampled posts had no usable timestamp; time windows use the rest.")
    if m.comments_unresolved_in_sample:
        caveats.append(
            f"{m.comments_unresolved_in_sample} posts' comment counts were hidden by the "
            f"feed and could not be resolved — engagement numbers may be slightly low."
        )

    return AudienceAnalysis(
        summary=summary,
        audience_profile=audience_profile,
        active_hours=active_hours,
        format_affinity=affinity,
        niche_signals=niche_signals,
        engagement_quality=quality,
        caveats=caveats,
        enough_data=True,
    )


# ---------------------------------------------------------------------------
# 2) Trending-topic detection — keyword graph over the niche sample
# ---------------------------------------------------------------------------

def detect_trending_topics(
    insight: ProfileInsight,
    rival_insights: Optional[List[ProfileInsight]] = None,
) -> TrendingTopicsResponse:
    """Topics gaining traction in the account's own recent sample plus any
    researched rivals. 'Trending' here means: appearing across multiple fresh
    posts with above-average engagement — real signals from the fetched data,
    not a global trend feed (TREND_CATALOG in ai_engine covers archetypes)."""
    posts = list(insight.profile.recent_posts or [])
    if rival_insights:
        for r in rival_insights:
            posts.extend(r.profile.recent_posts or [])

    # Fresh slice: posts from the most recent ~21 days of the sample.
    days = [p.posted_days_ago for p in posts if p.posted_days_ago is not None]
    horizon = min(21, max(days)) if days else 21
    fresh = [p for p in posts if p.posted_days_ago is not None and p.posted_days_ago <= horizon]

    if len(fresh) < 3:
        return TrendingTopicsResponse(
            summary="Too few dated posts in the sample to detect topic momentum.",
            topics=[],
            enough_data=False,
        )

    # Tokenize captions + hashtags into candidate topics.
    def tokens(p) -> List[str]:
        words = set()
        for w in (p.caption or "").lower().split():
            w = w.strip("#.,!?()[]\"'")
            if len(w) >= 4 and w not in _STOPWORDS and not w.isdigit():
                words.add(w)
        for h in p.hashtags or []:
            h = (h or "").lstrip("#").lower()
            if len(h) >= 4 and h not in _STOPWORDS:
                words.add(h)
        return list(words)

    overall = sum(_eng(p) for p in fresh) / len(fresh)
    stats: Dict[str, dict] = {}

    def add_topic(tok: str, p) -> None:
        s = stats.setdefault(tok, {"count": 0, "eng": 0, "recents": [], "tags": set(), "caption": ""})
        s["count"] += 1
        s["eng"] += _eng(p)
        s["recents"].append(p.posted_days_ago)
        for h in (p.hashtags or [])[:4]:
            s["tags"].add("#" + h.lstrip("#").lower())
        if not s["caption"]:
            s["caption"] = _first_line(p.caption)

    for p in fresh:
        for tok in tokens(p):
            if len(tok) >= 4:
                add_topic(tok, p)

    # Rank: multi-post topics with above-average engagement first.
    rows = [
        (tok, s)
        for tok, s in stats.items()
        if s["count"] >= 2 and s["eng"] / s["count"] >= overall * 0.8
    ]
    rows.sort(key=lambda kv: (kv[1]["count"], kv[1]["eng"] / kv[1]["count"]), reverse=True)

    topics: List[TrendingTopic] = []
    for tok, s in rows[:8]:
        avg = s["eng"] / s["count"]
        # Momentum: median recency of the posts touching the topic.
        rec = sorted(s["recents"])
        mid = rec[len(rec) // 2]
        momentum = "rising" if mid <= horizon / 3 else ("fading" if mid > horizon * 0.75 else "steady")
        topics.append(
            TrendingTopic(
                topic=tok,
                momentum=momentum,
                mentions=s["count"],
                avg_engagement=round(avg, 1),
                avg_engagement_overall=round(overall, 1),
                hashtags=sorted(s["tags"])[:5],
                sample_caption=s["caption"],
            )
        )

    rising = [t.topic for t in topics if t.momentum == "rising"]
    summary = (
        f"Detected {len(topics)} topic(s) across {len(fresh)} fresh posts"
        + (f" (+{len(rival_insights)} rivals)" if rival_insights else "")
        + "."
    )
    if rising:
        summary += f" Rising right now: {', '.join(rising[:3])}."
    if not topics:
        summary = "No repeated above-average topics in the fresh sample — the account posts too breadth-wise to detect a trend yet."

    return TrendingTopicsResponse(summary=summary, topics=topics, enough_data=len(topics) > 0)


# ---------------------------------------------------------------------------
# 3) Competitor content analysis — what rivals post vs the account
# ---------------------------------------------------------------------------

def analyze_rival_content(
    insight: ProfileInsight,
    rival_insights: List[ProfileInsight],
) -> CompetitorContentAnalysis:
    """Per-rival format mix, signature themes and hashtag ownership, plus a
    comparison against the analyzed account for each rival."""
    account = insight.profile
    account_posts = account.recent_posts or []
    if not rival_insights:
        return CompetitorContentAnalysis(
            summary="No researched rivals available — run competitor research first (or the full dashboard).",
            enough_data=False,
        )

    my_mix = _fmt_mix(account_posts)
    my_tags = {(h or "").lstrip("#").lower() for p in account_posts for h in (p.hashtags or [])}
    my_er = insight.metrics.engagement_rate
    account_avg = _overall_avg(insight)

    # The account's dominant formats (>=15% of its sample).
    total_my = max(1, sum(my_mix.values()))
    my_dominant = {f for f, n in my_mix.items() if n / total_my >= 0.15}

    overlaps: List[RivalContentOverlap] = []
    comparisons: List[RivalContentComparison] = []

    for r in rival_insights:
        rposts = r.profile.recent_posts or []
        rmix = _fmt_mix(rposts)
        rtotal = max(1, sum(rmix.values()))
        r_mix_pct = {f: int(round(n / rtotal * 100)) for f, n in sorted(rmix.items(), key=lambda kv: -kv[1])}

        rtags = [(h or "").lstrip("#").lower() for p in rposts for h in (p.hashtags or [])]
        tag_counts: Dict[str, int] = defaultdict(int)
        for t in rtags:
            tag_counts[t] += 1
        top_tags = [t for t, _ in sorted(tag_counts.items(), key=lambda kv: -kv[1])[:8]]

        ravg = sum(_eng(p) for p in rposts) / len(rposts) if rposts else 0.0
        # Signature theme: the rival's single most-used hashtag, capitalized.
        signature = top_tags[0].replace("#", "").replace("_", " ").capitalize() if top_tags else ""

        overlaps.append(
            RivalContentOverlap(
                username=r.profile.username,
                followers=r.profile.followers,
                engagement_rate=r.metrics.engagement_rate,
                format_mix=r_mix_pct,
                top_hashtags=[f"#{t}" for t in top_tags],
                signature_theme=signature,
                avg_engagement=round(ravg, 1),
                posts_sampled=len(rposts),
            )
        )

        # --- Comparison vs the account
        r_only_tags = [t for t in top_tags if t not in my_tags][:6]
        formats_they_use = {f for f in rmix}
        missed = sorted(formats_they_use - my_dominant)

        er_delta = 0.0
        if my_er > 0:
            er_delta = round((r.metrics.engagement_rate - my_er) / my_er * 100, 1)

        # Theme gap: one line contrasting signature theme vs the account's.
        my_sig = sorted(my_mix.items(), key=lambda kv: -kv[1])[0][0] if my_mix else "image"
        if signature and signature.lower() not in (account.category or "").lower():
            theme_gap = (
                f"{r.profile.username} centers on '{signature}' while the account leads "
                f"with {my_sig} content — a positioning contrast worth exploiting."
            )
        else:
            theme_gap = f"Both lean on {my_sig} content; differentiation must come from format and hooks."

        comparisons.append(
            RivalContentComparison(
                username=r.profile.username,
                formats_you_miss=missed,
                hashtags_they_own=[f"#{t}" for t in r_only_tags],
                theme_gap=theme_gap,
                engagement_gap_pct=er_delta,
            )
        )

    # Whitespace: hashtags owned by 2+ rivals but absent from the account.
    owner_count: Dict[str, int] = defaultdict(int)
    for c in comparisons:
        for h in c.hashtags_they_own:
            owner_count[h] += 1
    contested = sorted([h for h, n in owner_count.items() if n >= 2])[:8]

    summary = (
        f"Analyzed {len(rival_insights)} rival(s) against {len(account_posts)} of the account's posts. "
        + (f"Contested tags 2+ rivals own that the account ignores: {', '.join(contested)}. " if contested else "")
        + "Format and hashtag gaps below are the cheapest whitespace to enter."
    )

    return CompetitorContentAnalysis(
        summary=summary,
        rivals=overlaps,
        comparisons=comparisons,
        enough_data=True,
    )


# ---------------------------------------------------------------------------
# 4) Viral-hook suggestions — proven by the account's own top posts
# ---------------------------------------------------------------------------

_HOOK_PATTERNS: List[dict] = [
    {"match": ["how ", "how to"], "hook": "How to {goal} without {pain}", "why": "Promise + objection removal — the classic save-magnet opener."},
    {"match": ["stop ", "don't", "never "], "hook": "Stop doing {common_mistake} — do this instead", "why": "Pattern interrupt; negativity bias stops the scroll."},
    {"match": ["mistake", "mistakes"], "hook": "{n} mistakes making your {topic} harder than it should be", "why": "Loss aversion — viewers check whether they're guilty."},
    {"match": ["secret", "nobody", "no one tells"], "hook": "The {topic} secret nobody tells beginners", "why": "Curiosity gap; feels like insider knowledge."},
    {"match": ["before", "after", "transformation"], "hook": "From {start} to {result} — here's the exact timeline", "why": "Proof-first; concrete numbers earn saves and shares."},
    {"match": ["tip", "tips", "hack", "hacks"], "hook": "{n} {topic} hacks I wish I knew sooner", "why": "Regret framing + list promise — easy to consume."},
    {"match": ["why ", "reason"], "hook": "Why your {topic} isn't working (and the fix)", "why": "Names the viewer's problem in their own words."},
]

# Cheap n-gram fallback when no pattern matches: openers that read like hooks.
_HOOK_STARTERS = (
    "Nobody talks about this",
    "Save this before you",
    "I was wrong about",
    "3 things I'd do differently",
    "This took me 2 years to learn",
    "If you only watch one",
)


def suggest_viral_hooks(
    insight: ProfileInsight,
    rival_insights: Optional[List[ProfileInsight]] = None,
) -> ViralHooksResponse:
    """Hooks grounded in what already worked: the account's own top posts'
    real opening lines (proven winners), pattern-matched rewrites, and — when
    rivals are loaded — a rival hook that out-earned its account average."""
    posts = [p for p in (insight.profile.recent_posts or []) if (p.caption or "").strip()]
    overall = _overall_avg(insight)
    if not posts or overall <= 0:
        return ViralHooksResponse(
            summary="No captioned posts in the sample — hooks need real captions to learn from.",
            hooks=[],
            enough_data=False,
        )

    hooks: List[ViralHook] = []

    # 1) Proven: the account's own best posts' first lines.
    ranked = sorted(posts, key=_eng, reverse=True)
    for p in ranked[:3]:
        hooks.append(
            ViralHook(
                hook=_first_line(p.caption, limit=110),
                source="own",
                source_username=insight.profile.username,
                earned_engagement=_eng(p),
                engagement_index=_index(_eng(p), overall),
                why_it_works=(
                    f"Already proven on this account — earned {_index(_eng(p), overall)}% of your "
                    f"average engagement ({_eng(p):,} likes+comments)."
                ),
                ready_caption=_first_line(p.caption, limit=110),
            )
        )

    # 2) Pattern hooks matched to what the account's captions already do.
    blob = " ".join((p.caption or "").lower()[:120] for p in posts)
    topic = (insight.profile.category or (insight.metrics.top_hashtags[0].lstrip("#") if insight.metrics.top_hashtags else "your niche")).lower()
    matched = 0
    for pat in _HOOK_PATTERNS:
        if matched >= 2:
            break
        if any(m in blob for m in pat["match"]):
            hooks.append(
                ViralHook(
                    hook=pat["hook"].format(n="3", topic=topic, goal=topic, pain="guesswork", start="start", result="result", common_mistake="this"),
                    source="pattern",
                    earned_engagement=0,
                    engagement_index=100,
                    why_it_works=pat["why"],
                    ready_caption=pat["hook"].format(n="3", topic=topic, goal=topic, pain="guesswork", start="start", result="result", common_mistake="this"),
                )
            )
            matched += 1

    # 3) Generic high-performers if the pattern match came up short.
    while len(hooks) < 5:
        starter = _HOOK_STARTERS[len(hooks) % len(_HOOK_STARTERS)]
        hooks.append(
            ViralHook(
                hook=f"{starter} {topic}…",
                source="pattern",
                earned_engagement=0,
                engagement_index=100,
                why_it_works="Curiosity + specificity opener — reads like a story, not an ad.",
                ready_caption=f"{starter} {topic}…",
            )
        )
        if len(hooks) >= 5 + len(_HOOK_STARTERS):
            break

    # 4) One rival hook that out-earned its own account average.
    if rival_insights:
        best_rival = None
        best_idx = 100
        for r in rival_insights:
            rposts = [p for p in (r.profile.recent_posts or []) if (p.caption or "").strip()]
            if not rposts:
                continue
            ravg = sum(_eng(p) for p in rposts) / len(rposts)
            top = max(rposts, key=_eng)
            idx = _index(_eng(top), ravg)
            if idx > best_idx:
                best_idx = idx
                best_rival = (r, top, ravg)
        if best_rival:
            r, top, ravg = best_rival
            hooks.append(
                ViralHook(
                    hook=_first_line(top.caption, limit=110),
                    source="rival",
                    source_username=r.profile.username,
                    earned_engagement=_eng(top),
                    engagement_index=best_idx,
                    why_it_works=(
                        f"@{r.profile.username}'s best post ran {best_idx}% of their average — "
                        f"adapt the angle, not the words."
                    ),
                    ready_caption="",
                )
            )

    summary = (
        f"{len(hooks)} hook(s): the first ones are real openers from your own top-earning posts; "
        f"the rest are patterns matched to what your captions already do."
    )

    return ViralHooksResponse(summary=summary, hooks=hooks, enough_data=True)


# ---------------------------------------------------------------------------
# 5) Top-performing content — the account's own winners, ranked
# ---------------------------------------------------------------------------

def rank_top_content(insight: ProfileInsight, limit: int = 5) -> TopContentResponse:
    """The account's best recent posts by real engagement (likes+comments),
    both on absolute numbers and vs the account average — with a grounded
    'why it won' note per post."""
    posts = list(insight.profile.recent_posts or [])
    if not posts:
        return TopContentResponse(summary="No posts in the sample.", items=[], enough_data=False)

    overall = _overall_avg(insight)
    by_fmt: Dict[str, List[int]] = defaultdict(list)
    for p in posts:
        by_fmt[(p.media_type or "image").lower()].append(_eng(p))
    fmt_avgs = {f: sum(v) / len(v) for f, v in by_fmt.items() if v}
    best_format = max(fmt_avgs, key=fmt_avgs.get) if fmt_avgs else ""

    ranked = sorted(posts, key=_eng, reverse=True)[:max(1, limit)]

    items: List[TopContentItem] = []
    for i, p in enumerate(ranked, 1):
        idx = _index(_eng(p), overall)
        # Why it won: name the strongest correlated attribute with real data.
        reasons = []
        if p.media_type and best_format and p.media_type == best_format:
            reasons.append(f"{p.media_type} is the account's strongest format")
        if p.views and p.media_type in ("reel", "video"):
            reasons.append(f"{p.views:,} views")
        if (p.hashtags or []) and insight.metrics.top_hashtags and p.hashtags[0].lstrip("#").lower() == insight.metrics.top_hashtags[0].lstrip("#").lower():
            reasons.append(f"uses the account's core tag {p.hashtags[0]}")
        if idx >= 150:
            reasons.append(f"{idx - 100}% above the account's average engagement")
        why = "; ".join(reasons) if reasons else "solid performer within the recent sample"
        items.append(
            TopContentItem(
                rank=i,
                caption=_first_line(p.caption, limit=140) or "(no caption)",
                media_type=p.media_type or "image",
                likes=p.likes,
                comments=p.comments,
                views=p.views or 0,
                engagement=_eng(p),
                engagement_index=idx,
                posted_at=p.posted_at,
                posted_days_ago=p.posted_days_ago,
                hashtags=list(p.hashtags or [])[:6],
                why_it_won=why,
            )
        )

    summary = (
        f"Top {len(items)} of {len(posts)} sampled posts by engagement"
        + (f" — {best_format} content wins on this account right now." if best_format else ".")
    )

    return TopContentResponse(summary=summary, best_format=best_format, items=items, enough_data=True)


# ---------------------------------------------------------------------------
# 6) Competitor growth tracking — rivals' trajectories from scan history
# ---------------------------------------------------------------------------

def track_rival_growth(rival_insights: List[ProfileInsight]) -> RivalGrowthResponse:
    """Per-rival follower/ER trajectory from the agent's own stored scan
    history (storage.get_daily_snapshots — the same rows the dashboard
    history and monthly review use). Handles tracked for the first time
    honestly say so: tracking starts with this analysis."""
    if not rival_insights:
        return RivalGrowthResponse(
            summary="No researched rivals to track — run the full dashboard or competitor research first.",
            rivals=[],
            enough_data=False,
        )

    entries: List[RivalGrowthEntry] = []
    tracked_any = False
    for r in rival_insights:
        uname = r.profile.username
        try:
            import storage

            snaps = storage.get_daily_snapshots(uname)
        except Exception:
            snaps = []

        series = [
            RivalGrowthPoint(
                day=s.get("day", ""),
                followers=int(s.get("followers") or 0),
                engagement_rate=float(s.get("engagement_rate") or 0.0),
                avg_likes=float(s.get("avg_likes") or 0.0),
            )
            for s in snaps
            if s.get("followers") is not None
        ]

        if len(series) >= 2:
            tracked_any = True
            first, last = series[0], series[-1]
            fchange = last.followers - first.followers
            echange = round(last.engagement_rate - first.engagement_rate, 3)
            note = (
                f"Tracked over {len(series)} scan day(s) "
                f"({first.day} → {last.day})."
            )
            entries.append(
                RivalGrowthEntry(
                    username=uname,
                    scans=len(series),
                    first_seen=first.day,
                    last_seen=last.day,
                    followers_now=last.followers,
                    followers_change=fchange,
                    er_change=echange,
                    series=series[-30:],
                    note=note,
                )
            )
        else:
            # Single scan (or none beyond today): real current numbers,
            # honest note that the trajectory needs future re-scans.
            last = series[-1] if series else None
            entries.append(
                RivalGrowthEntry(
                    username=uname,
                    scans=len(series),
                    first_seen=last.day if last else "",
                    last_seen=last.day if last else "",
                    followers_now=last.followers if last else r.profile.followers,
                    followers_change=None,
                    er_change=None,
                    series=series[-30:],
                    note=(
                        "Tracking starts now — re-run the analysis on a later day "
                        "and the trajectory will fill in."
                    ),
                )
            )

    movers = [e for e in entries if e.followers_change is not None and e.followers_change > 0]
    summary = f"Tracking {len(entries)} rival(s) from the agent's stored scan history."
    if movers:
        best = max(movers, key=lambda e: e.followers_change)
        summary += f" Fastest mover so far: @{best.username} (+{best.followers_change:,} followers)."
    if not tracked_any:
        summary += " All rivals are on their first scan — deltas appear from the next re-scan onward."

    return RivalGrowthResponse(summary=summary, rivals=entries, enough_data=True)


# ---------------------------------------------------------------------------
# Bundle — one call the dashboard uses to fill all six sections
# ---------------------------------------------------------------------------

def build_intel_bundle(
    insight: ProfileInsight,
    rival_insights: Optional[List[ProfileInsight]] = None,
) -> IntelResponse:
    """All six deep-intel sections from one already-analyzed dataset.
    Every section is independently wrapped so a failure in one never
    blocks the rest (mirrors the dashboard's additive-section pattern)."""
    def _safe(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None

    return IntelResponse(
        audience=_safe(analyze_audience, insight),
        trending=_safe(detect_trending_topics, insight, rival_insights),
        rival_content=_safe(analyze_rival_content, insight, rival_insights or []),
        hooks=_safe(suggest_viral_hooks, insight, rival_insights),
        top_content=_safe(rank_top_content, insight),
        rival_growth=_safe(track_rival_growth, rival_insights or []),
    )
