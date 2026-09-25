"""
Deterministic analytics layers built on the real fetched data:

- compute_best_times()   — which day/hour slots earn the most engagement
- compute_reels()        — reels/views deep-dive vs the researched niche
- optimize_bio()         — bio rewrite (LLM when configured, rules otherwise)
- build_monthly_review() — profile history review with engagement trajectory

Everything here is computed from Post/ProfileInsight objects the scraper
already fetched — no extra network calls except the optional LLM.
"""
from collections import defaultdict
from datetime import datetime
from typing import List, Optional

from models import (
    BestTimeSlot,
    BestTimes,
    CadenceCell,
    CadenceMap,
    BioOptimizer,
    HashtagSuggestion,
    HashtagSuggestionResult,
    ProfileInsight,
    ReelTiming,
    ReelTimingSlot,
    ReelsInsights,
)


# ---------------------------------------------------------------------------
# Best time to post (from real post timestamps vs their engagement)
# ---------------------------------------------------------------------------

_SLOT_LABELS = {0: "00-06 UTC", 6: "06-12 UTC", 12: "12-18 UTC", 18: "18-24 UTC"}


def compute_best_times(insight: ProfileInsight) -> BestTimes:
    posts = [p for p in insight.profile.recent_posts if p.posted_at]
    if len(posts) < 4:
        return BestTimes(
            slots=[],
            summary=(
                "Not enough timestamped posts yet for a reliable time analysis. "
                "Rescan after a few more posts - the engine needs at least 4."
            ),
            enough_data=False,
        )

    buckets = defaultdict(lambda: {"eng": [], "days": set()})
    for p in posts:
        try:
            dt = datetime.fromisoformat(p.posted_at)
        except (TypeError, ValueError):
            continue
        slot = (dt.hour // 6) * 6
        buckets[slot]["eng"].append(p.likes + p.comments)
        buckets[slot]["days"].add(dt.strftime("%a"))

    slots: List[BestTimeSlot] = []
    for hour, data in buckets.items():
        if len(data["eng"]) < 2:  # a single post proves nothing
            continue
        slots.append(BestTimeSlot(
            day="/".join(sorted(data["days"])) or "-",
            hour=hour,
            avg_engagement=round(sum(data["eng"]) / len(data["eng"]), 1),
            samples=len(data["eng"]),
        ))

    if not slots:
        return BestTimes(
            slots=[],
            summary="Timestamps exist but slots are too sparse - rescan after a few more posts.",
            enough_data=False,
        )

    slots.sort(key=lambda s: s.avg_engagement, reverse=True)
    best = slots[0]
    worst = slots[-1]
    summary = (
        f"Posts published in the {_SLOT_LABELS.get(best.hour, best.hour)} window earn the most "
        f"engagement ({best.avg_engagement:,.0f} avg likes+comments over {best.samples} posts, "
        f"mostly {best.day}); the {_SLOT_LABELS.get(worst.hour, worst.hour)} window performs worst "
        f"({worst.avg_engagement:,.0f}). Times are UTC - shift to your audience's timezone."
    )
    return BestTimes(slots=slots, summary=summary, enough_data=True)


# ---------------------------------------------------------------------------
# Posting cadence + weekday×hour timing map (from real timestamps)
# ---------------------------------------------------------------------------

_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def compute_cadence_map(insight: ProfileInsight) -> CadenceMap:
    """Deterministic cadence + timing map from the account's real posts.

    - heatmap: avg engagement per weekday × 3-hour UTC slot
    - cadence: posts/week over the sample span, active weekdays, and the
      longest silent stretch (a consistency signal)
    """
    posts = [p for p in insight.profile.recent_posts if p.posted_at]
    if len(posts) < 3:
        return CadenceMap(
            summary=(
                "Not enough timestamped posts for a cadence map — at least 3 "
                "posts with timestamps are needed (fetch again once more posts exist)."
            ),
            enough_data=False,
        )

    parsed: List[tuple] = []
    for p in posts:
        try:
            dt = datetime.fromisoformat(p.posted_at)
        except (TypeError, ValueError):
            continue
        parsed.append((dt, p.likes + p.comments))
    if len(parsed) < 3:
        return CadenceMap(
            summary="Post timestamps could not be parsed — cadence map unavailable.",
            enough_data=False,
        )

    # --- Cadence over the sample span (newest minus oldest post) ---
    times = [dt for dt, _ in parsed]
    span_days = max((max(times) - min(times)).total_seconds() / 86400, 0.5)
    posts_per_week = round(len(parsed) / span_days * 7, 1)

    active_days = sorted({_DAY_NAMES[dt.weekday()] for dt, _ in parsed}, key=_DAY_NAMES.index)

    # Longest gap between consecutive posts (in days), a consistency signal.
    gaps = [
        round((b - a).total_seconds() / 86400, 1)
        for a, b in zip(sorted(times), sorted(times)[1:])
    ]
    longest_gap = max(gaps) if gaps else 0.0

    # --- Weekday × 3-hour-slot heatmap ---
    buckets: dict = {}
    for dt, eng in parsed:
        day = _DAY_NAMES[dt.weekday()]
        slot = (dt.hour // 3) * 3
        buckets.setdefault((day, slot), []).append(eng)

    heatmap: List[CadenceCell] = []
    for (day, slot), engs in buckets.items():
        heatmap.append(CadenceCell(
            day=day, hour=slot,
            engagement=round(sum(engs) / len(engs), 1),
            samples=len(engs),
        ))

    strongest = max(heatmap, key=lambda c: c.engagement, default=None)

    # --- Summary ---
    freq_note = (
        f"Posts ~{posts_per_week}/week"
        if posts_per_week >= 1 else
        f"Posts ~{round(7 / max(posts_per_week, 0.1))}-day gaps"
    )
    gap_note = (
        f"; longest silent stretch {longest_gap:.0f} days" if longest_gap >= 2 else ""
    )
    best_note = (
        f" Strongest window: {strongest.day} {strongest.hour:02d}-{strongest.hour + 3:02d} UTC "
        f"({strongest.engagement:,.0f} avg engagement, {strongest.samples} post(s))."
        if strongest else ""
    )
    summary = (
        f"{freq_note} across {len(active_days)} of 7 weekdays{gap_note}. "
        "Times are UTC — shift to your audience's timezone."
        + best_note
    )
    heatmap.sort(key=lambda c: (-c.engagement, c.day, c.hour))
    return CadenceMap(
        posts_per_week=posts_per_week,
        sample_days=round(span_days, 1),
        active_days=active_days,
        longest_gap_days=longest_gap,
        heatmap=heatmap,
        strongest_cell=strongest,
        summary=summary,
        enough_data=True,
    )


# ---------------------------------------------------------------------------
# Account score explanation (deterministic, mirrors ai_engine's channels)
# ---------------------------------------------------------------------------

def explain_account_score(insight: ProfileInsight) -> dict:
    """Data-grounded explanation for the 0-100 account score. Recomputes the
    same per-channel subscores ai_engine uses (kept aligned by importing
    them) and returns the total with what each channel contributed."""
    try:
        from ai_engine import _score_channels
        c = _score_channels(insight.profile, insight.metrics)
    except Exception:
        c = {"er": 0, "cadence": 0, "comments": 0, "ffr": 0, "reels": 0, "verified": 0}

    import math
    p, m = insight.profile, insight.metrics
    expected_er = (
        max(0.4, 6.0 - 1.0 * (math.log10(max(p.followers, 10)) - 2.0))
        if p.followers > 0 else 0.0
    )

    drivers: List[str] = []
    drainers: List[str] = []

    er_pct = round(c["er"] * 100)
    if er_pct >= 70:
        drivers.append(
            f"Engagement rate {m.engagement_rate}% is strong for a {p.followers:,}-follower "
            f"account (peers average ~{expected_er:.1f}%)."
        )
    elif er_pct <= 30 and p.followers > 0:
        drainers.append(
            f"Engagement rate {m.engagement_rate}% is below the ~{expected_er:.1f}% typical "
            f"for accounts this size — content isn't reaching the follower base."
        )

    cad_pct = round(c["cadence"] * 100)
    if cad_pct >= 75:
        drivers.append(f"Posting cadence of {m.posting_frequency_per_week}/week keeps the account active in the algorithm.")
    elif cad_pct <= 30:
        drainers.append(f"Only {m.posting_frequency_per_week} posts/week — cadence below ~4/week leaves reach on the table.")

    com_pct = round(c["comments"] * 100)
    if com_pct >= 60:
        drivers.append(f"Comments average {m.avg_comments:,.1f}/post — a real community signal that likes alone don't prove.")
    elif com_pct <= 25:
        drainers.append(f"Comments average only {m.avg_comments:,.1f}/post — add questions/CTAs to convert viewers into commenters.")

    if m.reels_count == 0:
        drainers.append("No reels with view data in the recent sample — the strongest reach format is unused.")
    elif c["reels"] >= 0.6:
        drivers.append(f"Reels average {m.avg_views:,.0f} views — video reach is compounding.")

    if m.follower_following_ratio < 1:
        drainers.append(f"Follows more accounts ({p.following:,}) than it has followers ({p.followers:,}) — reads as low authority.")

    if not drivers:
        drivers.append("Baseline is workable — the fixes above compound fastest on an active account.")
    if not drainers:
        drainers.append("No major drainers in this sample — scale what is already working.")

    total = (
        40 * c["er"] + 20 * c["cadence"] + 15 * c["comments"]
        + 10 * c["ffr"] + 10 * c["reels"] + 5 * c["verified"]
    )
    return {
        "total": int(round(max(0.0, min(100.0, total)))),
        "drivers": drivers,
        "drainers": drainers,
    }


# ---------------------------------------------------------------------------
# Reels deep-dive (views + like-rate per view vs niche)
# ---------------------------------------------------------------------------


def compute_reels(insight: ProfileInsight, rivals: Optional[List[ProfileInsight]] = None) -> ReelsInsights:
    """Reels performance from the account's real recent posts.

    View counts are only exposed by some data sources (the permalink
    fallback never carries them), so "no views" must not be read as "no
    reels": reels without view data still have real likes/comments and are
    analyzed on engagement, with an honest note that views are hidden."""
    all_reels = [
        p for p in insight.profile.recent_posts
        if p.media_type in ("reel", "video")
    ]
    reels = [p for p in all_reels if p.views > 0]  # subset with real view data
    account_views = sum(p.views for p in reels)
    account_likes = sum(p.likes for p in all_reels)
    account_rate = (account_likes / account_views * 100) if account_views else 0.0

    niche_likes, niche_views = 0, 0
    for r in (rivals or []):
        for p in r.profile.recent_posts:
            if p.media_type in ("reel", "video") and p.views > 0:
                niche_likes += p.likes
                niche_views += p.views
    niche_rate = (niche_likes / niche_views * 100) if niche_views else 0.0

    tips: List[str] = []
    if not all_reels:
        # Genuinely no reels in the sample — the reach-lever advice stands.
        verdict = (
            "No reels in the recent posts — this account is leaving "
            "the strongest reach lever unused."
        )
        tips = [
            "Publish 2-3 reels per week; reels are Instagram's most-pushed format for non-follower reach.",
            "Reuse the account's best-performing static concepts as short vertical videos.",
        ]
        return ReelsInsights(
            reels_count=0, videos_with_views=0, avg_views=0, avg_likes_per_reel=0,
            like_rate_per_view=0, niche_like_rate_per_view=round(niche_rate, 3),
            verdict=verdict, tips=tips,
        )

    avg_likes = account_likes / len(all_reels)

    if not reels:
        # Reels exist but the source hid view counts (Instagram hides views
        # on some content, and permalink metadata never carries them).
        # Engagement analysis stays fully real; only view-rate is unknown.
        top = max(all_reels, key=lambda p: p.likes)
        verdict = (
            f"{len(all_reels)} reel(s) in the recent sample averaging "
            f"{avg_likes:,.0f} likes and "
            f"{sum(p.comments for p in all_reels) / len(all_reels):,.0f} comments. "
            "View counts are hidden for this content, so like-rate-per-view "
            "can't be computed — engagement analysis below uses real likes/comments only."
        )
        tips = [
            f"Top reel pulled {top.likes:,} likes — reuse its concept as a repeatable format.",
            "Views are hidden on these reels; track them in the Instagram app to compare reach vs engagement.",
            "Add on-screen text: most feed viewers watch reels muted.",
        ]
        return ReelsInsights(
            reels_count=len(all_reels), videos_with_views=0,
            avg_views=0, avg_likes_per_reel=round(avg_likes, 1),
            like_rate_per_view=0, niche_like_rate_per_view=round(niche_rate, 3),
            verdict=verdict, tips=tips[:5],
        )

    avg_views = account_views / len(reels)
    avg_likes = account_likes / len(reels)

    if niche_rate and account_rate < niche_rate * 0.6:
        verdict = (
            f"Reels underperform the niche: {account_rate:.2f}% like-rate per view vs "
            f"{niche_rate:.2f}% across researched rivals. The hook (first 2 seconds) or "
            f"watch-through is the likely leak."
        )
        tips.append("Front-load the payoff: put the most interesting moment in the first 2 seconds.")
        tips.append("Cut length - if retention is the leak, 7-15s reels beat 30s+ ones.")
    elif niche_rate and account_rate >= niche_rate:
        verdict = (
            f"Reels match or beat the niche: {account_rate:.2f}% like-rate per view vs "
            f"{niche_rate:.2f}% for researched rivals. Scale volume - this format is working."
        )
        tips.append("Increase reels cadence while the like-rate holds - the format is converting.")
    else:
        verdict = (
            f"Reels perform near the niche average ({account_rate:.2f}% like-rate per view). "
            f"Stronger hooks and clearer topics are the next lever."
        )

    tips.append("Add on-screen text: most feed viewers watch reels muted.")
    tips.append("End with a question or 'comment X for the guide' to convert views into comments.")
    return ReelsInsights(
        reels_count=len(all_reels),
        videos_with_views=len(reels),
        avg_views=round(avg_views, 1),
        avg_likes_per_reel=round(avg_likes, 1),
        like_rate_per_view=round(account_rate, 3),
        niche_like_rate_per_view=round(niche_rate, 3),
        verdict=verdict,
        tips=tips[:5],
    )


# ---------------------------------------------------------------------------
# Bio optimizer
# ---------------------------------------------------------------------------

def _rule_based_bio(insight: ProfileInsight) -> BioOptimizer:
    p = insight.profile
    current = p.bio or ""
    notes: List[str] = []

    if len(current) < 40:
        notes.append("Bio is short - wasted search real estate; Instagram indexes bio text.")
    if "dm" not in current.lower() and "👇" not in current and "http" not in current.lower():
        notes.append("No call-to-action (DM / link / button) telling visitors what to do next.")
    if "|" not in current and "\n" not in current:
        notes.append("No structure - use 'who you help | what you deliver | proof' lines.")
    if p.category and p.category.lower() not in current.lower():
        notes.append(f"Category keyword '{p.category}' missing - add it for search visibility.")

    who = p.full_name or p.username.replace("_", " ").replace(".", " ").title()
    niche = p.category or "your niche"
    er = insight.metrics.engagement_rate
    proof = (
        f"{p.followers:,} followers" if p.followers >= 10000
        else f"{er}% engagement community"
    )
    suggested = (
        f"{who} | {niche}\n"
        f"Helping you get better at {niche.lower()} - one post at a time\n"
        f"{proof} · New drops weekly\n"
        f"DM 'START' to collaborate 👇"
    )
    if not notes:
        notes.append("Bio already covers structure, keywords and CTA - minor wording polish only.")
    return BioOptimizer(current_bio=current, suggested_bio=suggested, notes=notes[:5])


def compute_reel_timing(insight: ProfileInsight, rivals: Optional[List[ProfileInsight]] = None) -> ReelTiming:
    """Find whitespace opportunities for reel posting times.

    Analyzes when the account currently posts reels and when rivals post,
    then identifies under-served day/hour slots where a reel could capture
    untapped audience attention.
    """
    posts = insight.profile.recent_posts
    reels = [p for p in posts if p.media_type in ("reel", "video") and p.posted_at]

    if not reels:
        # No reel data — say so honestly instead of inventing "high-traffic"
        # windows we have no evidence for. Empty slots = the UI shows its
        # insufficient-data state.
        return ReelTiming(
            slots=[],
            summary=(
                f"No reels with timestamps found in @{insight.profile.username}'s recent "
                "posts, so there is no real data to find timing whitespace from yet. "
                "Post a few reels and re-scan — this analysis is computed from actual "
                "posting times and engagement, never estimated."
            ),
            enough_data=False,
            current_reel_cadence="0 reels in sample",
        )

    # Map each reel to day-of-week + 6-hour slot
    from collections import defaultdict
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    reel_slots = defaultdict(list)  # (day, slot_hour) -> [engagement]
    slot_days = defaultdict(set)

    for p in reels:
        try:
            dt = datetime.fromisoformat(p.posted_at)
        except (TypeError, ValueError):
            continue
        day_idx = dt.weekday()
        slot_hour = (dt.hour // 3) * 3  # 3-hour blocks for finer granularity
        engagement = p.likes + p.comments
        reel_slots[(day_idx, slot_hour)].append(engagement)
        slot_days[(day_idx, slot_hour)].add(day_names[day_idx])

    # Compute competitor activity per slot (if rivals available)
    comp_slot_activity: dict = {}
    if rivals:
        comp_reels = [
            p for r in rivals
            for p in r.profile.recent_posts
            if p.media_type in ("reel", "video") and p.posted_at
        ]
        comp_slot_counts = defaultdict(int)
        for p in comp_reels:
            try:
                dt = datetime.fromisoformat(p.posted_at)
            except (TypeError, ValueError):
                continue
            slot_hour = (dt.hour // 3) * 3
            comp_slot_counts[slot_hour] += 1
        total_comp_reels = max(1, len(comp_reels))
        for key, _ in reel_slots.items():
            day_idx, sh = key
            # How many competitors posted in this 3-hour window (normalized)
            comp_count = comp_slot_counts.get(sh, 0)
            ratio = comp_count / total_comp_reels
            if ratio < 0.15:
                activity = "low"
            elif ratio < 0.35:
                activity = "medium"
            else:
                activity = "high"
            comp_slot_activity[key] = activity

    # Find whitespace: slots where account has posted 0-1 reels AND
    # competitor activity is low-to-medium.
    # Also compute which slots the account already uses.
    used_slots = set(reel_slots.keys())
    all_days = list(range(7))
    all_slots = [0, 3, 6, 9, 12, 15, 18, 21]

    # Account's current reel cadence
    span_days = max((p.posted_days_ago for p in posts), default=1) or 1
    reel_cadence = round(len(reels) / (span_days / 7), 1)
    cadence_str = f"{reel_cadence}/week" if reel_cadence > 0 else "<1/week"

    whitespace_slots: List[ReelTimingSlot] = []
    for day_idx in all_days:
        for sh in all_slots:
            key = (day_idx, sh)
            if key in used_slots:
                continue  # already posting here
            activity = comp_slot_activity.get(key, "unknown" if not rivals else "medium")
            # Without researched rivals we cannot claim anything about
            # competition — the slot is ranked purely by the account's own
            # unused windows, and the UI labels it "competition unknown".
            if activity == "high":
                continue
            day_name = day_names[day_idx]
            hour_range = f"{sh:02d}:00-{sh+3:02d}:00 UTC"
            if activity == "low":
                rationale = (
                    f"{day_name} {hour_range}: no reels posted here by @{insight.profile.username} "
                    f"and low competitor activity — a clear whitespace to capture undivided attention."
                )
            elif activity == "medium":
                rationale = (
                    f"{day_name} {hour_range}: not used by @{insight.profile.username} yet; "
                    f"moderate competitor presence means room to stand out with strong hooks."
                )
            else:
                rationale = (
                    f"{day_name} {hour_range}: not used by @{insight.profile.username} in the "
                    f"recent sample — a window their audience is not being served at yet."
                )
            whitespace_slots.append(ReelTimingSlot(
                day=day_name,
                hour=sh,
                rationale=rationale,
                competitor_activity=activity,
            ))

    # Sort: known low-activity first, then unknown, then day order
    activity_rank = {"low": 0, "medium": 1, "unknown": 2}
    whitespace_slots.sort(key=lambda s: (activity_rank.get(s.competitor_activity, 2), day_names.index(s.day), s.hour))

    top_slots = whitespace_slots[:6]

    summary = (
        f"@{insight.profile.username} posts {cadence_str} reels. "
        f"Found {len(whitespace_slots)} unused day/hour windows"
        + (" where researched competitors are quiet." if rivals else ". "
           "Competition levels are unknown (no rivals analyzed) — ranking is by "
           "the account's own unused windows.")
        + " Top picks below — test 2-3 of these slots for 2 weeks and measure the like/comment lift."
    )
    if not top_slots:
        summary = (
            f"@{insight.profile.username} already posts reels across most high-traffic windows "
            f"({cadence_str}). To find more whitespace, post at off-peak hours (early morning or late night UTC) "
            f"and re-scan — the engine will flag newly opened gaps."
        )

    return ReelTiming(
        slots=top_slots,
        summary=summary,
        enough_data=True,
        current_reel_cadence=cadence_str,
    )


def suggest_hashtags(insight: ProfileInsight, rivals: Optional[List[ProfileInsight]] = None) -> HashtagSuggestionResult:
    """Suggest hashtags optimized for more likes and comments on posts and reels.

    Strategy:
      1. Start from the account's own top-performing hashtags (what already works).
      2. Add niche-relevant tags the rivals use but the account doesn't (untapped reach).
      3. Mix in engagement-optimized tags: question-driven tags for comments,
         broad discovery tags for likes, and community tags for both.
      4. Separate sets for posts (carousel/image) vs reels.
    """
    p = insight.profile
    m = insight.metrics
    niche = (p.category or p.full_name or "your niche").strip().lower()
    niche_tag = niche.replace(" ", "")

    # Account's existing top tags (already proven for this account)
    my_tags = [t.lstrip("#") for t in m.top_hashtags]

    # Rivals' tags the account isn't using yet
    rival_tags: dict = {}
    if rivals:
        for r in rivals:
            for t in r.metrics.top_hashtags:
                clean = t.lstrip("#").lower()
                if clean and clean not in {x.lower() for x in my_tags}:
                    rival_tags[clean] = rival_tags.get(clean, 0) + 1
        # Sort by how many rivals use it
        rival_tags = dict(sorted(rival_tags.items(), key=lambda x: -x[1]))

    # Engagement-boosting tag families
    comment_drivers = [
        "dropdown", "tellme", "thisorthat", "chooseone", "hottake",
        "debate", "evenifyou", "truthbomb", "realtalk", "discuss",
    ]
    like_drivers = [
        "aesthetics", "inspo", "mood", "vibes", "curation",
        "bestoftheday", "weeklyfeed", "feed", "explore",
    ]
    community_tags = [
        "community", "creatorsofinstagram", "supportsmallbusiness",
        "instacommunity", "featureme", "fyp", "viral",
    ]

    def pick_tags(candidates, count, avoid=set()):
        out = []
        seen = set()
        for t in candidates:
            low = t.lower().replace(" ", "")
            if low in seen or low in avoid:
                continue
            seen.add(low)
            out.append(f"#{t}")
            if len(out) >= count:
                break
        return out

    avoid_set = {t.lower().replace(" ", "") for t in my_tags}

    # Build post hashtag set (carousel/image — likes + saves driven)
    post_tags = []
    # Own working tags first
    post_tags += pick_tags(my_tags, 4)
    # Niche + community
    post_tags += pick_tags([niche_tag, f"{niche_tag}tips", f"{niche_tag}care", f"{niche_tag}community", "smallbusiness"], 3, avoid_set)
    # Comment drivers (ask questions)
    post_tags += pick_tags(comment_drivers, 2, avoid_set)
    # Like drivers
    post_tags += pick_tags(like_drivers, 2, avoid_set)
    post_tags = post_tags[:12]

    # Build reel hashtag set (discovery + views driven)
    reel_tags = []
    reel_tags += pick_tags(my_tags[:3], 3)  # top working tags
    reel_tags += pick_tags(["reelsinstagram", "reelsofinstagram", "reelstagram", "reel", "reelsvideo", f"{niche_tag}reel", f"reels{niche_tag}"], 3, avoid_set)
    reel_tags += pick_tags(["trending", "viral", "fyp", "explorepage", "trendingreels"], 2, avoid_set)
    reel_tags += pick_tags(comment_drivers, 2, avoid_set)
    reel_tags = reel_tags[:12]

    # Build structured suggestions with impact estimates
    suggestions: List[HashtagSuggestion] = []

    # Own top tags — high impact, proven
    for t in my_tags[:4]:
        suggestions.append(HashtagSuggestion(
            hashtag=f"#{t}",
            estimated_impact="high",
            reason=f"Already in your top-performing tags — @{p.username}'s posts using this tag earn above-average engagement.",
            best_for="both",
            tier="mid" if m.top_hashtags.index(f"#{t}") < 2 else "rare",
        ))

    # Rival tags — medium impact, untapped
    for tag, count in list(rival_tags.items())[:3]:
        suggestions.append(HashtagSuggestion(
            hashtag=f"#{tag}",
            estimated_impact="medium",
            reason=f"Used by {count} researched rivals in your niche — this tag is clearly working for similar accounts; you're not using it yet.",
            best_for="likes",
            tier="rare",
        ))

    # Comment drivers
    for tag in comment_drivers[:2]:
        suggestions.append(HashtagSuggestion(
            hashtag=f"#{tag}",
            estimated_impact="high",
            reason=f"Question/decision-driving tags like #{tag} invite comments — the strongest signal for algorithmic reach.",
            best_for="comments",
            tier="broad",
        ))

    # Like drivers
    for tag in like_drivers[:2]:
        suggestions.append(HashtagSuggestion(
            hashtag=f"#{tag}",
            estimated_impact="medium",
            reason=f"Discovery-oriented tag #{tag} surfaces your content to new viewers who like and follow.",
            best_for="likes",
            tier="broad",
        ))

    summary = (
        f"Suggested tags for @{p.username} target {m.engagement_rate}% engagement baseline. "
        f"Mix your proven tags (high impact) with {len(rival_tags)} untapped niche tags rivals use and "
        f"engagement-driving tags that explicitly ask for comments. Rotate the post and reel sets — "
        f"never reuse the exact same set twice in a row."
    )

    notes = [
        "Put 3-5 proven/high-impact tags first in the caption (Instagram weights early tags more).",
        "Use comment-driving tags on posts that end with a question — the tag + question combo compounds.",
        "For reels, lead with discovery tags (#reelsinstagram, #fyp) so the algorithm categorizes the format correctly.",
        "Avoid over-used broad tags (#love, #instagood) — they drown small accounts in noise.",
    ]

    return HashtagSuggestionResult(
        summary=summary,
        suggestions=suggestions,
        post_set=post_tags,
        reel_set=reel_tags,
        notes=notes,
    )


def optimize_bio(insight: ProfileInsight) -> BioOptimizer:
    """LLM rewrite when a key is configured; deterministic rewrite otherwise."""
    fallback = _rule_based_bio(insight)

    try:
        from ai_engine import _profile_facts, _llm_available, _invoke_llm_sync, _structured
        if not _llm_available():
            return fallback
        from langchain_core.prompts import ChatPromptTemplate

        prompt = ChatPromptTemplate.from_messages([
            ("system",
             "You are an Instagram bio conversion specialist. Rewrite the bio to be "
             "specific, searchable and action-driving. Ground every claim in the given "
             "data - never invent follower counts or claims."),
            ("human",
             "Account data:\n{facts}\n\nCurrent bio:\n{bio}\n\n"
             "Rewrite it: line 1 = who/what, line 2 = value for the follower, "
             "line 3 = proof, line 4 = CTA. Keep it honest and specific to this account."),
        ])

        def _run(client):
            chain = prompt | _structured(client, BioOptimizer)
            return chain.invoke({
                "facts": _profile_facts(insight.profile, insight.metrics),
                "bio": insight.profile.bio or "(empty)",
            })

        result: BioOptimizer = _invoke_llm_sync(_run)
        if result.suggested_bio:
            result.current_bio = insight.profile.bio or ""
            if not result.notes:
                result.notes = fallback.notes
            return result
        return fallback
    except Exception as e:
        print(f"[ai] bio optimizer: rule-based fallback ({str(e)[:110]})", flush=True)
        return fallback


# ---------------------------------------------------------------------------
# Monthly profile review (from scan history)
# ---------------------------------------------------------------------------

def build_monthly_review(
    username: str,
    records: List,
    latest_insight,
) -> "MonthlyReviewResponse":
    """Build a monthly review from scan history records.

    Works entirely from stored scan data — no extra API calls needed.
    Produces per-metric trajectory analysis with recommendations.
    """
    from models import MonthlyReviewMetric, MonthlyReviewResponse

    if not records:
        return MonthlyReviewResponse(
            username=username,
            period_start="",
            period_end="",
            scan_count=0,
            span_days=0,
            metrics=[],
            summary=(
                "No scan history found for this account. "
                "Run an analysis to start tracking trends."
            ),
            recommendations=[
                "Run an analysis on this account — the scan will be recorded and visible here on your next review.",
            ],
            warnings=[],
        )

    if len(records) < 2:
        # Single scan: show current values with a "first scan" note.
        only = records[0]
        return MonthlyReviewResponse(
            username=username,
            period_start=only.scanned_at,
            period_end=only.scanned_at,
            scan_count=1,
            span_days=0,
            metrics=[
                MonthlyReviewMetric(
                    label="Followers", unit="count",
                    first_value=float(only.followers), last_value=float(only.followers),
                    change=0.0, change_pct=0.0, trend="flat", samples=1,
                ),
                MonthlyReviewMetric(
                    label="Engagement rate", unit="%",
                    first_value=only.engagement_rate, last_value=only.engagement_rate,
                    change=0.0, change_pct=0.0, trend="flat", samples=1,
                ),
                MonthlyReviewMetric(
                    label="Avg likes per post", unit="count",
                    first_value=only.avg_likes, last_value=only.avg_likes,
                    change=0.0, change_pct=0.0, trend="flat", samples=1,
                ),
                MonthlyReviewMetric(
                    label="Posting frequency", unit="posts/week",
                    first_value=only.posting_frequency_per_week,
                    last_value=only.posting_frequency_per_week,
                    change=0.0, change_pct=0.0, trend="flat", samples=1,
                ),
            ],
            summary=(
                f"First scan recorded for @{username} on {only.scanned_at[:10]}. "
                f"This is your baseline: {int(only.followers):,} followers, "
                f"{only.engagement_rate}% engagement, {only.avg_likes:,.0f} avg likes/post. "
                f"Run another analysis in a week or two to start seeing trend lines."
            ),
            recommendations=[
                "This is your first scan — it's now your baseline. Come back in 7-14 days and run another analysis to see how your engagement and followers are moving.",
                "In the meantime, the AI report and timing sections below list concrete steps to improve these numbers before your next scan.",
            ],
            warnings=[
                "Only 1 scan on record — trend lines will appear after your second scan.",
            ],
        )

    # Unique dates span
    dates = sorted(set(r.scanned_at[:10] for r in records))
    span_days = (len(dates) - 1) if len(dates) > 1 else 0

    first = records[0]
    last = records[-1]

    def _metric(label, unit, first_val, last_val, samples):
        change = last_val - first_val
        if first_val != 0:
            change_pct = round((change / abs(first_val)) * 100, 2)
        else:
            change_pct = 0.0
        if change > 0.01:
            trend = "up"
        elif change < -0.01:
            trend = "down"
        else:
            trend = "flat"
        return MonthlyReviewMetric(
            label=label,
            unit=unit,
            first_value=round(first_val, 3),
            last_value=round(last_val, 3),
            change=round(change, 3),
            change_pct=change_pct,
            trend=trend,
            samples=samples,
        )

    metrics = [
        _metric(
            "Followers", "count",
            first.followers, last.followers, len(records),
        ),
        _metric(
            "Engagement rate", "%",
            first.engagement_rate, last.engagement_rate, len(records),
        ),
        _metric(
            "Avg likes per post", "count",
            first.avg_likes, last.avg_likes, len(records),
        ),
        _metric(
            "Posting frequency", "posts/week",
            first.posting_frequency_per_week,
            last.posting_frequency_per_week,
            len(records),
        ),
    ]

    # Build narrative summary from the metrics
    up_count = sum(1 for m in metrics if m.trend == "up")
    down_count = sum(1 for m in metrics if m.trend == "down")

    parts = []
    parts.append(
        f"@{username} was scanned {len(records)} times over the last "
        f"{span_days} day{'s' if span_days != 1 else ''} (from "
        f"{first.scanned_at[:10]} to {last.scanned_at[:10]})."
    )

    for m in metrics:
        sign = "+" if m.change > 0 else ""
        if m.label == "Followers":
            parts.append(
                f"Followers went from {int(m.first_value):,} to {int(m.last_value):,} "
                f"({sign}{m.change:,.0f}, {sign}{m.change_pct}%) — "
                f"{'growth' if m.trend == 'up' else 'decline' if m.trend == 'down' else 'stable'}."
            )
        elif m.label == "Engagement rate":
            parts.append(
                f"Engagement rate moved from {m.first_value}% to {m.last_value}% "
                f"({sign}{m.change} pp, {sign}{m.change_pct}%) — "
                f"{'improving' if m.trend == 'up' else 'declining' if m.trend == 'down' else 'holding steady'}."
            )
        elif m.label == "Avg likes per post":
            parts.append(
                f"Average likes per post shifted from {m.first_value:,.0f} to {m.last_value:,.0f} "
                f"({sign}{m.change:,.0f}, {sign}{m.change_pct}%)."
            )
        elif m.label == "Posting frequency":
            parts.append(
                f"Posting frequency changed from {m.first_value}/week to {m.last_value}/week "
                f"({sign}{m.change:+.1f}, {sign}{m.change_pct}%)."
            )

    if up_count > down_count:
        parts.append(
            f"Overall trajectory is positive — {up_count} of {len(metrics)} tracked metrics are trending up."
        )
    elif down_count > up_count:
        parts.append(
            f"Overall trajectory needs attention — {down_count} of {len(metrics)} tracked metrics are trending down."
        )
    else:
        parts.append(
            "Metrics are mixed — some up, some down; focus on the specific levers below."
        )

    # Recommendations based on trajectory
    recs = []
    er_metric = next((m for m in metrics if m.label == "Engagement rate"), None)
    followers_metric = next((m for m in metrics if m.label == "Followers"), None)
    freq_metric = next((m for m in metrics if m.label == "Posting frequency"), None)

    if er_metric and er_metric.trend == "down":
        recs.append(
            "Engagement rate is declining — audit your last 5 posts: are captions weaker, "
            "formats less engaging, or posting times off? Re-engage with comments in the first hour."
        )
    elif er_metric and er_metric.trend == "up":
        recs.append(
            "Engagement rate is climbing — double down on whatever content types and captions "
            "are driving it. Study your top 3 posts and replicate their structure."
        )

    if followers_metric and followers_metric.trend == "down":
        recs.append(
            "Follower count is slipping — this usually follows an engagement drop. "
            "Refresh your content mix, increase posting cadence, and reply to every comment within the first hour."
        )
    elif followers_metric and followers_metric.trend == "up":
        recs.append(
            f"You've gained {followers_metric.change:+,.0f} followers over this period — "
            "sustain momentum by keeping posting frequency at or above current levels."
        )

    if freq_metric and freq_metric.trend == "down":
        recs.append(
            "Posting frequency has dropped — consistency is the #1 driver of algorithmic reach. "
            "Get back to at least 3 posts/week, ideally 4-5."
        )
    elif freq_metric and freq_metric.trend == "up":
        recs.append(
            "Posting frequency is up — that's the right direction. "
            "Watch whether engagement keeps pace; more posts only help if each one still performs."
        )

    if not recs:
        recs.append(
            "Metrics are stable — maintain current strategy and watch for shifts in the next scan cycle."
        )

    # Data quality warnings
    warnings = []
    if len(records) < 3:
        warnings.append(
            "Only a few scans on record — trend lines are preliminary. "
            "More scans will sharpen the picture."
        )
    if span_days < 7:
        warnings.append(
            "Scans span less than a week — monthly patterns aren't visible yet. "
            "Keep scanning weekly to build a meaningful trend."
        )
    # Check if all scans have identical values (no real change between scans)
    if all(r.followers == first.followers for r in records):
        warnings.append(
            "Follower count is identical across all scans — no measurable change "
            "in this period. Keep scanning to build a meaningful trend."
        )

    return MonthlyReviewResponse(
        username=username,
        period_start=first.scanned_at,
        period_end=last.scanned_at,
        scan_count=len(records),
        span_days=span_days,
        metrics=metrics,
        summary=" ".join(parts),
        recommendations=recs,
        warnings=warnings,
    )
