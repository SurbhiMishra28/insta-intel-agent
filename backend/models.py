"""
Pydantic schemas shared across the API.
"""
from pydantic import BaseModel, Field
from typing import List, Optional


class Post(BaseModel):
    id: str
    caption: str
    likes: int
    comments: int
    posted_days_ago: int
    hashtags: List[str] = []
    media_type: str = "image"  # image | video | carousel | reel
    views: int = 0                     # video/reel view count (0 when not a video)
    posted_at: Optional[str] = None    # ISO timestamp when available


class ProfileData(BaseModel):
    username: str
    full_name: str
    bio: str
    followers: int
    following: int
    posts_count: int
    is_verified: bool
    is_business: bool
    category: Optional[str] = None
    recent_posts: List[Post]
    data_age_hours: Optional[float] = None  # set when serving stale cached data = []


class ProfileMetrics(BaseModel):
    engagement_rate: float
    avg_likes: float
    avg_comments: float
    posting_frequency_per_week: float
    follower_following_ratio: float
    top_hashtags: List[str]
    best_content_type: str
    avg_views: float = 0        # avg video/reel views (0 when none in sample)
    reels_count: int = 0        # reels/videos with real view data in the sample


class ProfileInsight(BaseModel):
    profile: ProfileData
    metrics: ProfileMetrics
    ai_summary: str
    strengths: List[str]
    weaknesses: List[str]
    recommendations: List[str]
    account_score: Optional[int] = None  # 0-100, computed from real data (size-aware)


class AnalyzeRequest(BaseModel):
    username: str = Field(..., min_length=1)


class CompareRequest(BaseModel):
    main_username: str = Field(..., min_length=1)
    # Empty list = auto-discover competitors instead of using manual handles.
    competitor_usernames: List[str] = Field(default_factory=list, max_items=10)


class CompareResponse(BaseModel):
    main: ProfileInsight
    competitors: List[ProfileInsight]
    market_summary: str
    competitive_gaps: List[str]
    content_gaps: List[str] = []
    opportunities: List[str] = []
    selection_rationale: str = ""  # why these competitors were chosen
    ranking: List[str]  # usernames ordered best -> worst on composite score
    warnings: List[str] = []  # handles that could not be fetched (live mode)


class DiscoveredCompetitor(BaseModel):
    username: str
    full_name: str = ""
    bio: str = ""
    followers: int = 0
    verified: bool = False
    private: bool = False


class CompetitorResearchResponse(BaseModel):
    """Result of finding + researching the 5-10 strongest competitors."""
    main: ProfileInsight
    competitors: List[ProfileInsight]
    market_summary: str
    competitive_gaps: List[str]
    content_gaps: List[str] = []
    opportunities: List[str] = []
    selection_rationale: str
    ranking: List[str]
    warnings: List[str] = []
    candidates_found: int = 0


class PostIdea(BaseModel):
    """One concrete, ready-to-make post suggestion."""
    title: str                      # e.g. "3 ceramics mistakes beginners make"
    format: str = "reel"            # reel | carousel | image | video
    why: str = ""                   # why this should drive likes/comments/follows
    caption_concept: str = ""       # what the caption should say / hook line
    hashtags: List[str] = []        # ready-to-paste hashtags


class GrowthPlan(BaseModel):
    """Actionable plan to grow followers, likes and comments."""
    summary: str
    content_pillars: List[str] = []          # 3-4 recurring themes to post around
    post_ideas: List[PostIdea] = []          # 6-8 concrete post suggestions
    weekly_schedule: List[str] = []          # Mon..Sun posting plan
    format_mix: str = ""                     # recommended reel/carousel/image ratio
    hashtag_sets: List[List[str]] = []       # rotating ready-to-paste sets
    engagement_tactics: List[str] = []       # daily actions that lift comments
    follower_growth_targets: List[str] = []  # honest 30/60/90-day estimates
    quick_wins: List[str] = []               # things to do today


class GrowthPlanResponse(BaseModel):
    main: ProfileInsight
    plan: GrowthPlan
    rivals: List[ProfileInsight] = []  # researched competitors used as grounding (if any)
    warnings: List[str] = []
    score_explanation: Optional["ScoreExplanation"] = None
    best_times: Optional["BestTimes"] = None
    cadence_map: Optional["CadenceMap"] = None
    reels: Optional["ReelsInsights"] = None
    bio: Optional["BioOptimizer"] = None
    hashtags: Optional["HashtagResearch"] = None
    hashtag_suggestions: Optional["HashtagSuggestionResult"] = None
    reel_timing: Optional["ReelTiming"] = None
    trends_result: Optional["TrendsResponse"] = None
    review: Optional["MonthlyReviewResponse"] = None
    history: List["ScanRecord"] = []


class BestTimeSlot(BaseModel):
    day: str            # Mon..Sun
    hour: int           # 0-23, start of the slot
    avg_engagement: float  # avg likes+comments for posts in this slot
    samples: int        # how many posts backed this slot


class BestTimes(BaseModel):
    slots: List[BestTimeSlot] = []
    summary: str
    enough_data: bool = True


class CadenceCell(BaseModel):
    day: str            # Mon..Sun
    hour: int           # 0-21, start of a 3-hour UTC slot
    engagement: float   # avg likes+comments of posts in this cell
    samples: int


class CadenceMap(BaseModel):
    """Posting cadence + weekday×hour timing map built from real timestamps."""
    posts_per_week: float = 0
    sample_days: float = 0          # days covered by the fetched posts
    active_days: List[str] = []     # weekdays with at least one post
    longest_gap_days: float = 0     # biggest silent stretch in the sample
    heatmap: List[CadenceCell] = []
    strongest_cell: Optional[CadenceCell] = None
    summary: str = ""
    enough_data: bool = True


class ReelsInsights(BaseModel):
    reels_count: int = 0               # reels in the recent sample (regardless of view data)
    videos_with_views: int = 0         # subset that exposed real view counts
    avg_views: float = 0
    avg_likes_per_reel: float = 0
    like_rate_per_view: float = 0      # account's reels like-rate, % (0 when views hidden)
    niche_like_rate_per_view: float = 0  # pooled rivals' reels like-rate, %
    verdict: str
    tips: List[str] = []


class BioOptimizer(BaseModel):
    current_bio: str
    suggested_bio: str
    notes: List[str] = []


class HashtagStat(BaseModel):
    name: str
    posts_count: int = 0
    tier: str = "mid"  # rare | mid | broad


class HashtagResearch(BaseModel):
    summary: str
    tiered: List[HashtagStat] = []
    recommended_sets: List[List[str]] = []
    notes: List[str] = []


# ---------- Reel Timing Whitespace Finder ----------

class ReelTimingSlot(BaseModel):
    day: str                # Mon..Sun
    hour: int               # 0-23, suggested start hour
    rationale: str          # why this slot is a whitespace opportunity
    competitor_activity: str  # low/medium/high (from researched rivals) or unknown (no rivals analyzed)


class ReelTiming(BaseModel):
    slots: List[ReelTimingSlot] = []
    summary: str
    enough_data: bool = True
    current_reel_cadence: str = ""


# ---------- Hashtag Suggestion for Likes & Comments ----------

class HashtagSuggestion(BaseModel):
    hashtag: str
    estimated_impact: str   # 'high' | 'medium' | 'low' — likely lift for likes/comments
    reason: str             # why this tag should help engagement
    best_for: str           # 'likes' | 'comments' | 'both'
    tier: str = "mid"       # rare | mid | broad


class HashtagSuggestionResult(BaseModel):
    summary: str
    suggestions: List[HashtagSuggestion] = []
    post_set: List[str] = []        # ready-to-paste set for static posts
    reel_set: List[str] = []        # ready-to-paste set for reels
    notes: List[str] = []


class ScanRecord(BaseModel):
    scanned_at: str
    followers: int
    engagement_rate: float
    avg_likes: float
    posting_frequency_per_week: float
    followers_delta: int = 0        # vs previous scan
    er_delta: float = 0


# ---------- Instagram Trends ----------

class TrendInfo(BaseModel):
    """A detected trending topic/challenge on Instagram."""
    name: str                          # short label, e.g. "80s Photo Filter"
    description: str                   # what the trend is
    category: str                      # visual | audio | challenge | format | filter
    hashtags: List[str] = []           # primary hashtags to use
    started_days_ago: int = 0          # how fresh the trend is
    is_rising: bool = True             # still climbing vs plateaued


class TrendAlert(BaseModel):
    """A notification when a trend is detected."""
    trend: TrendInfo
    why_relevant: str                  # why this trend matters for the account
    suggested_post_idea: str           # concrete post concept to ride the trend
    suggested_reel_idea: str           # concrete reel concept to ride the trend
    caption_hook: str                  # opening line for the caption
    recommended_hashtags: List[str]    # ready-to-paste hashtag set
    posting_tip: str                   # format/timing tip for maximum reach


class TrendSuggestion(BaseModel):
    """A content idea derived from a trend, ready to make."""
    title: str
    format: str = "reel"              # reel | carousel | image
    concept: str                       # what to create
    caption: str                       # suggested caption text
    hashtags: List[str] = []
    why: str = ""                     # why this trend is worth riding


class TrendsResponse(BaseModel):
    trending: List[TrendInfo] = []     # currently active trends
    alerts: List[TrendAlert] = []      # alerts relevant to the account
    suggestions: List[TrendSuggestion] = []  # ready-to-make ideas


# ---------- Monthly Profile Review ----------

class MonthlyReviewMetric(BaseModel):
    label: str                    # e.g. "Followers", "Engagement rate"
    unit: str                     # e.g. "count", "%"
    first_value: float            # value at start of period
    last_value: float             # value at end of period
    change: float                 # last - first
    change_pct: float             # percent change
    trend: str                    # 'up' | 'down' | 'flat'
    samples: int                  # number of scans in period


class MonthlyReviewResponse(BaseModel):
    username: str
    period_start: str             # ISO timestamp of oldest scan in window
    period_end: str               # ISO timestamp of newest scan in window
    scan_count: int               # total scans in window
    span_days: int                # days between first and last scan
    metrics: List[MonthlyReviewMetric]
    summary: str                  # AI or rule-based narrative summary
    recommendations: List[str]    # actionable recommendations based on trajectory
    warnings: List[str] = []      # data quality notes


# ---------- Whitespace Finder + Caption Suggestions ----------

class ThemeCoverage(BaseModel):
    """How much the account posts within one of the standard content themes."""
    theme: str
    post_count: int                # how many of the recent posts match this theme
    share_pct: float               # share of recent posts, 0-100
    avg_engagement: float          # avg likes+comments for posts in this theme
    avg_engagement_all: float      # account-wide avg for comparison
    verdict: str                   # 'underused' | 'balanced' | 'overused' | 'untouched'
    example_caption: str = ""      # a real caption excerpt from the account in this theme


class WhitespaceArea(BaseModel):
    """A content whitespace: a theme the account underuses or ignores."""
    theme: str
    gap_type: str                  # 'untouched' | 'underused' | 'overused'
    description: str               # what the gap is and why it matters
    opportunity: str               # what to post to close the gap
    suggested_format: str = "reel" # reel | carousel | image
    hashtags: List[str] = []       # starter hashtags for the whitespace
    rivals_own_it: bool = False    # True when researched rivals post this but the account doesn't


class CaptionSuggestion(BaseModel):
    """A ready-to-post caption, grounded in the account's real data."""
    title: str                     # short label, e.g. "Before/after transformation"
    caption: str                   # the full suggested caption text
    hashtags: List[str] = []       # ready-to-paste hashtags
    format: str = "reel"           # reel | carousel | image
    best_time_hint: str = ""       # when to post it (from best-times analysis)
    why: str = ""                  # why this caption suits the account right now


class WhitespaceResponse(BaseModel):
    main: ProfileInsight
    coverage: List[ThemeCoverage] = []
    whitespace: List[WhitespaceArea] = []
    captions: List[CaptionSuggestion] = []
    summary: str = ""
    warnings: List[str] = []


class ScoreExplanation(BaseModel):
    """What lifted or dragged the account's 0-100 score, from real data."""
    total: int
    drivers: List[str] = []
    drainers: List[str] = []


# Resolve forward references (BestTimes etc. are defined from here on).
GrowthPlanResponse.model_rebuild()
MonthlyReviewResponse.model_rebuild()
TrendsResponse.model_rebuild()
TrendAlert.model_rebuild()
TrendSuggestion.model_rebuild()
ReelTiming.model_rebuild()
HashtagSuggestionResult.model_rebuild()
WhitespaceResponse.model_rebuild()
