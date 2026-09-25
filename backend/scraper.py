"""
Data acquisition layer for Instagram profile data — REAL DATA ONLY.

Every number served comes from a real source:

  - Provider #0: official Instagram Graph API (business_discovery) when
    Graph credentials are configured.
  - Provider #1: Apify's Instagram Scraper actor when APIFY_TOKEN(s) are
    configured (profile fields + ~12 latest posts with real likes,
    comments, timestamps and media types).
  - Provider #2: keyless HTTP ladder (web_profile_info + GraphQL feed + HTML)
    opens instagram.com and reads Instagram's own web_profile_info JSON
    from inside the page (same origin, cookies and TLS fingerprint as the
    site's frontend). No token, no login, no relay.
  - Provider #3: keyless HTTP ladder (direct web_profile_info GET with
    bootstrap cookies + CDP-rendered page GraphQL capture).

All successful fetches are persisted to the local SQLite cache, so
repeats are instant and free.

Latency layers, per handle:

  1. in-memory TTL cache (instant, per-process)
  2. persistent SQLite disk cache (instant, survives restarts)
  3. live providers in order (Graph → Apify → keyless)

Failures fail loudly and honestly: ValueError → HTTP 400 (handle does not
exist), RuntimeError → HTTP 503 (every provider blocked). Simulated/demo
data is never generated and never served.

Competitor discovery: local cache mining (mentions/hashtag overlap —
free), Instagram's related-accounts signal, and a keyword search over
Instagram users as fallback.
"""
import asyncio
import json
import os
import re
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import sys as _sys

# Provider diagnostics can carry characters the Windows cp1252 console codec
# cannot encode (bios, captions) — a crashing print must never kill a fetch.
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

# Load backend/.env (if present) BEFORE reading env vars below, so this
# module works regardless of import order.
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from models import Post, ProfileData

# Live real-data mode is the only mode: every fetch goes to a real
# provider (Graph API / Apify / keyless HTTP ladder) and
# failures surface honestly. Simulated/demo data was removed by design.
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "").strip()
APIFY_ACTOR_ID = os.getenv("APIFY_ACTOR_ID", "apify/instagram-scraper")
APIFY_SEARCH_ACTOR_ID = os.getenv("APIFY_SEARCH_ACTOR_ID", "apify/instagram-search-scraper")
APIFY_RUN_TIMEOUT = int(os.getenv("APIFY_RUN_TIMEOUT", "300"))  # seconds
# A failed fetch must fail loudly (503/400) rather than ever serve fake
# data — the demo fallback that used to exist here was removed by design.

# --- Official Instagram Graph API (business_discovery) — provider #0 ------
# When configured, real data comes from Meta's ToS-compliant API instead of
# any scraping path. IG_ACCESS_TOKEN: long-lived Facebook User access token
# with instagram_basic + instagram_manage_insights + pages_read_engagement.
# IG_BUSINESS_ACCOUNT_ID: the numeric ID of YOUR OWN Instagram professional
# account (the "app user" the discovery query is performed on).
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "").strip()
IG_BUSINESS_ACCOUNT_ID = os.getenv("IG_BUSINESS_ACCOUNT_ID", "").strip()
IG_GRAPH_VERSION = os.getenv("IG_GRAPH_VERSION", "v25.0").strip() or "v25.0"
IG_GRAPH_TIMEOUT = float(os.getenv("IG_GRAPH_TIMEOUT", "20"))


def _has_graph_credentials() -> bool:
    return bool(IG_ACCESS_TOKEN and IG_BUSINESS_ACCOUNT_ID)


class _Perf:
    """Stage stopwatch for one profile fetch: prints elapsed time per stage
    to the server log ([perf] prefix) so slow cold fetches can be attributed
    to a layer instead of guessed at. Zero cost when disabled."""

    ENABLED = os.getenv("IG_FETCH_DEBUG", "0").lower() in ("1", "true", "yes")

    def __init__(self, label: str):
        self.label = label
        self.t0 = time.monotonic()
        self.t_last = self.t0

    def stage(self, name: str) -> None:
        if not self.ENABLED:
            return
        now = time.monotonic()
        print(
            f"[perf] {self.label}: {name} +{now - self.t_last:.2f}s (total {now - self.t0:.2f}s)",
            flush=True,
        )
        self.t_last = now

# ---------------------------------------------------------------------------
# Apify token pool — multi-account failover
#
# Each free Apify account gets $5 of platform credit per billing cycle.
# When the primary account is exhausted (or its token is revoked), live
# fetches used to hard-fail until the cycle reset. The pool fixes that:
# tokens are tried in order and a token-level failure (credit exhausted,
# 401/403 invalid token, 402 billing) benches that token for
# TOKEN_COOLDOWN_SECS and moves to the next one. The request only fails
# when every token is unavailable, with one aggregated error listing each
# cause. Non-token failures (actor 404, run timeout, bad shape) abort
# immediately — they would fail identically on every token.
# ---------------------------------------------------------------------------

def _build_token_pool() -> List[str]:
    """Ordered, deduplicated pool: APIFY_TOKEN, then APIFY_TOKEN_2..9
    (gaps skipped), then any extras from comma-separated APIFY_TOKENS."""
    raw: List[str] = []
    primary = os.getenv("APIFY_TOKEN", "").strip()
    if primary:
        raw.append(primary)
    for _i in range(2, 10):
        t = os.getenv(f"APIFY_TOKEN_{_i}", "").strip()
        if t:
            raw.append(t)
    multi = os.getenv("APIFY_TOKENS", "").strip()
    if multi:
        raw.extend(t.strip() for t in multi.split(","))
    pool: List[str] = []
    for t in raw:
        if t and t not in pool:
            pool.append(t)
    return pool


class ApifyTokenError(RuntimeError):
    """Token-level Apify failure (exhausted monthly credit, invalid/revoked
    token, billing block) — the pool should fail over to the next token."""


APIFY_TOKENS: List[str] = _build_token_pool()
TOKEN_COOLDOWN_SECS = int(os.getenv("APIFY_TOKEN_COOLDOWN", "1800"))

# token -> {"status": exhausted|invalid|ready, "detail": str, "until": epoch}
_token_state: Dict[str, Dict[str, Any]] = {}


def _token_label(idx: int) -> str:
    return "APIFY_TOKEN (primary)" if idx == 0 else f"backup #{idx} (APIFY_TOKEN_{idx + 1})"


def _bench_token(token: str, status: str, detail: str) -> None:
    """Take a failing token out of rotation for TOKEN_COOLDOWN_SECS."""
    _token_state[token] = {
        "status": status,
        "detail": detail,
        "until": time.time() + TOKEN_COOLDOWN_SECS,
    }


def _eligible_tokens() -> List[str]:
    now = time.time()
    return [t for t in APIFY_TOKENS if _token_state.get(t, {}).get("until", 0) <= now]


def _aggregate_token_errors(errors: List[str]) -> str:
    return (
        "No Apify token in the pool could serve this request. Add a fresh "
        "free-account token (APIFY_TOKEN_2, APIFY_TOKEN_3, … — every free "
        "Apify account gets $5/month) or raise a limit in Apify Console → "
        "Settings → Usage & Billing. Per-token causes:\n"
        + "\n".join(f"  • {e}" for e in errors)
    )


def _all_benched_message() -> str:
    now = time.time()
    rows = []
    for idx, t in enumerate(APIFY_TOKENS):
        st = _token_state.get(t) or {}
        mins = max(0, round(((st.get("until") or 0) - now) / 60))
        rows.append(
            f"  • {_token_label(idx)} benched ({st.get('status', 'error')}) — "
            f"retried automatically in ~{mins} min"
        )
    return (
        "All configured Apify tokens are benched after earlier failures; "
        "they are retried automatically. Per-token state:\n" + "\n".join(rows)
    )


def apify_token_pool_status() -> List[Dict[str, Any]]:
    """Per-token pool view for the /api/usage transparency endpoint.
    Pure in-process state — no network calls, always safe to call."""
    now = time.time()
    out: List[Dict[str, Any]] = []
    for idx, t in enumerate(APIFY_TOKENS):
        st = _token_state.get(t) or {}
        until = st.get("until") or 0
        out.append({
            "name": "primary" if idx == 0 else f"backup #{idx}",
            "env_var": "APIFY_TOKEN" if idx == 0 else f"APIFY_TOKEN_{idx + 1}",
            "token_tail": f"…{t[-6:]}" if len(t) > 6 else "…",
            "status": st.get("status", "ready"),
            "benched": until > now,
            "retry_in_mins": max(0, round((until - now) / 60)) if until > now else 0,
            "detail": st.get("detail", ""),
        })
    return out

# Simple in-process TTL cache so repeated handles (e.g. compare mode re-fetching
# the main account) don't re-trigger a paid actor run within the TTL window.
_CACHE_TTL = int(os.getenv("PROFILE_CACHE_TTL", "1800"))  # seconds
_profile_cache: Dict[str, tuple] = {}  # username -> (monotonic_ts, ProfileData)
# Stale-while-revalidate bookkeeping: handles with a background refresh in
# flight, and the cooldown after a 401/403/429 before retrying the plain-HTTP
# API (the keyless HTTP ladder records and honors this).
_refresh_inflight: set = set()
_API_BLOCK_COOLDOWN = float(os.getenv("IG_API_BLOCK_COOLDOWN", "600"))
_related_cache: Dict[str, tuple] = {}  # username -> (monotonic_ts, List[dict])
_selfheal_attempted: set = set()  # handles whose stats-only cache row got one refetch try
_CACHE_LOCK = asyncio.Lock()

# ---------------------------------------------------------------------------
# Persistent disk cache (SQLite) — real data survives restarts, so repeat
# analyses and compare-mode refetches never re-trigger a paid actor run.
# ---------------------------------------------------------------------------

_CACHE_DB = os.getenv("PROFILE_CACHE_DB", os.path.join(os.path.dirname(__file__), "profile_cache.db"))

# Bump when the mapping/fetch pipeline changes in a way that changes the
# DATA content of cached rows. Rows written by an older pipeline version are
# ignored (treated as expired), so pre-fix snapshots with broken comment
# counts are never served again — a fresh real fetch happens instead.
_CACHE_SCHEMA_VERSION = int(os.getenv("PROFILE_CACHE_SCHEMA", "3"))

_DISK_TTL_PROFILE = int(os.getenv("PROFILE_DISK_TTL", str(7 * 86400)))   # fresh enough for metrics
_DISK_TTL_DISCOVERY = int(os.getenv("DISCOVERY_DISK_TTL", str(24 * 3600)))  # competitor lists drift slowly


def _cache_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_CACHE_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache ("
        " key TEXT PRIMARY KEY, value TEXT NOT NULL, cached_at REAL NOT NULL)"
    )
    return conn


def _cache_get(key: str, ttl: int) -> Optional[Any]:
    try:
        with _cache_conn() as conn:
            row = conn.execute(
                "SELECT value, cached_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
    except Exception:
        return None
    if not row or (time.time() - row[1]) > ttl:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def _cache_set(key: str, value: Any) -> None:
    try:
        with _cache_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, cached_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )
    except Exception:
        pass  # cache must never break the data path


def _profile_to_json(p: ProfileData) -> dict:
    d = p.model_dump()
    d["cache_schema"] = _CACHE_SCHEMA_VERSION
    # Remember how wide the sample was when this row was written: if the
    # configured sample grows, cached rows with a narrower sample are
    # re-fetched so the comment average always reflects the intended width.
    try:
        d["sample_size_cached"] = len(d.get("recent_posts") or [])
        d["sample_width_cached"] = int(os.getenv("IG_PERMALINK_POSTS", "12"))
    except Exception:
        pass
    for post in d["recent_posts"]:
        for k in ("likes", "comments", "views", "posted_days_ago"):
            post[k] = int(post.get(k) or 0)
        post["caption"] = str(post.get("caption") or "")
        post["hashtags"] = list(post.get("hashtags") or [])
    return d


def _profile_from_json(d: dict) -> ProfileData:
    d = dict(d)
    # Rows written by an older pipeline carry a missing/older schema stamp.
    # Their comment counts may predate the mapping fixes, so refuse to serve
    # them as fresh data — the caller re-fetches instead.
    if int(d.get("cache_schema") or 0) < _CACHE_SCHEMA_VERSION:
        raise ValueError("stale cache schema")
    # A row written when the configured sample width was smaller (e.g. the
    # old 6-item sample) under-samples comment data — refuse it so a fresh
    # full-width fetch replaces it. Rows written by providers that simply
    # RETURN fewer posts are fine: the stamp records the configured width
    # at write time, not the actual post count.
    try:
        written_width = int(d.get("sample_width_cached") or 0)
    except Exception:
        written_width = 0
    if written_width < int(os.getenv("IG_PERMALINK_POSTS", "12")):
        raise ValueError("stale narrow sample")
    d["recent_posts"] = d.get("recent_posts") or []
    return ProfileData(**d)


def _disk_profile_get(username: str) -> Optional[ProfileData]:
    raw = _cache_get(f"profile:{username}", _DISK_TTL_PROFILE)
    try:
        return _profile_from_json(raw) if isinstance(raw, dict) else None
    except Exception:
        return None


def _disk_profile_get_any(username: str) -> Optional[ProfileData]:
    """Last-resort lookup: return the cached profile regardless of TTL (up to
    30 days) together with its age. Real data, just possibly stale."""
    try:
        with _cache_conn() as conn:
            row = conn.execute(
                "SELECT value, cached_at FROM cache WHERE key = ?",
                (f"profile:{username}",),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    age_h = (time.time() - row[1]) / 3600
    if age_h > 24 * 30:
        return None
    try:
        p = _profile_from_json(json.loads(row[0]))
    except Exception:
        return None
    if p is not None:
        p.data_age_hours = round(age_h, 1)
    return p


def purge_account(username: str) -> dict:
    """Cut one handle out of the agent entirely: drop its disk-cached profile
    snapshot, its related-profiles discovery caches and its in-memory entry.
    (Scan-history rows are cleared separately by storage.clear_history.)
    Returns how many cache keys/entries were removed."""
    uname = normalize_username(username)
    removed = {"profile_snapshot": 0, "discovery_caches": 0, "memory": 0}
    try:
        with _cache_conn() as conn:
            cur = conn.execute("DELETE FROM cache WHERE key = ?", (f"profile:{uname}",))
            removed["profile_snapshot"] = cur.rowcount or 0
            cur = conn.execute(
                "DELETE FROM cache WHERE key LIKE ?", (f"related:{uname}:%",)
            )
            removed["discovery_caches"] = cur.rowcount or 0
    except Exception:
        pass
    if _profile_cache.pop(uname, None) is not None:
        removed["memory"] = 1
    return removed


def _disk_profile_set(username: str, p: ProfileData) -> None:
    _cache_set(f"profile:{username}", _profile_to_json(p))


# ---------------------------------------------------------------------------
# Username / URL normalization
# ---------------------------------------------------------------------------

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")

# Shared media-type mapping (kept so cached legacy JSON keeps mapping cleanly).
_MEDIA_TYPE_MAP = {
    "Image": "image", "GraphImage": "image", "image": "image", "IMAGE": "image",
    "Video": "video", "GraphVideo": "video", "video": "video", "VIDEO": "video",
    "Sidecar": "carousel", "GraphSidecar": "carousel", "Carousel": "carousel",
    "carousel": "carousel", "XDTMediaCarousel": "carousel", "album": "carousel",
    "Clip": "reel", "Reel": "reel", "reel": "reel", "REEL": "reel",
    "Clips": "reel", "GraphStoryVideo": "reel",
    # web_profile_info nodes use the lowercase product_type value "clips"
    "clips": "reel", "clip": "reel",
    # xdt/GraphQL feed nodes use numeric media_type: 1=image, 2=video, 8=carousel
    "1": "image", "2": "video", "8": "carousel",
    "GraphImages": "image", "GraphVideos": "video", "feed": "video",
}


def normalize_username(raw: str) -> str:
    """Accepts a bare handle, an @handle, or any Instagram profile URL
    (instagram.com/username, with or without trailing slash / query string)
    and returns the canonical lowercase handle."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("Username is required.")

    lower = text.lower()
    if "instagram.com" in lower or "instagr.am" in lower:
        m = re.search(r"(?:instagram\.com|instagr\.am)/([^/?&#]+)", text, re.IGNORECASE)
        if not m:
            raise ValueError("Could not find a username in that Instagram URL.")
        username = m.group(1)
    elif "ig.me" in lower:
        m = re.search(r"ig\.me/(?:m/)?([^/?&#]+)", text, re.IGNORECASE)
        if not m:
            raise ValueError("Could not find a username in that Instagram URL.")
        username = m.group(1)
    else:
        username = text

    username = username.strip().strip("/").lstrip("@")
    username = username.split("?")[0].split("#")[0]

    if username.lower() in {"p", "reel", "reels", "explore", "stories", "tv", "about", "accounts"}:
        raise ValueError(f"'{username}' is not an Instagram profile URL.")

    if not _USERNAME_RE.match(username):
        raise ValueError(
            f"'{username}' doesn't look like a valid Instagram handle "
            "(letters, numbers, dots and underscores only)."
        )
    return username.lower()


# ---------------------------------------------------------------------------
# Apify live fetch
# ---------------------------------------------------------------------------

# Apify Instagram actor field names vary slightly by resultsType / version;
# normalize the common variants here.
_PROFILE_FIELDS = {
    "username": ("username", "handle", "ownerUsername"),
    "full_name": ("fullName", "full_name", "name"),
    "biography": ("biography", "bio", "description"),
    "followers": ("followersCount", "followerCount", "edge_followed_by.count", "followers"),
    "following": ("followsCount", "followingCount", "edge_follow.count", "following", "follows"),
    "posts_count": ("postsCount", "mediaCount", "edge_owner_to_timeline_media.count", "postsCountNumber"),
    "is_verified": ("verified", "isVerified"),
    "is_business": ("businessAccount", "isBusinessAccount"),
    "category": ("category", "businessCategoryName", "categoryName"),
}


def _dig(item: Any, dotted_key: str) -> Any:
    """Walks nested dicts/lists, e.g. 'edge_followed_by.count' or
    'edge_owner_to_timeline_media.edges.0.node'."""
    cur = item
    for part in dotted_key.split("."):
        if isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _pick(item: Dict[str, Any], keys) -> Any:
    for k in keys:
        v = _dig(item, k)
        if v is not None:
            return v
    return None


def _clean_str(v: Any) -> Optional[str]:
    """Normalize actor string fields. The Instagram actor serializes JSON
    nulls as the literal string 'None'/'null' (seen in businessCategoryName
    and similar fields), so those and blanks count as missing."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("none", "null", "nil"):
        return None
    return s


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("1", "true", "yes")
    return bool(v) if v is not None else False


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


_POST_COUNT_MULT_STR = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def _parse_count_string(raw: str) -> int:
    """Parse humanized count strings Instagram sometimes emits:
    '1,944' -> 1944; '1.9K' -> 1900; '352K' -> 352000; '2M' -> 2000000.
    Anything unparseable becomes 0."""
    text = (raw or "").strip().replace(",", "")
    if not text:
        return 0
    m = re.fullmatch(r"([\d.]+)\s*([KMBkmb])", text)
    if m:
        try:
            return int(float(m.group(1)) * _POST_COUNT_MULT_STR[m.group(2).lower()])
        except (ValueError, OverflowError):
            return 0
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _posted_days_ago(item: Dict[str, Any]) -> int:
    ts_raw = _pick(item, ("timestamp", "takenAtTimestamp", "taken_at_timestamp"))
    if ts_raw is None:
        return 0
    try:
        if isinstance(ts_raw, (int, float)) or (isinstance(ts_raw, str) and ts_raw.replace(".", "").isdigit()):
            ts = float(ts_raw)
            if ts > 1e12:  # epoch milliseconds
                ts /= 1000.0
        else:
            dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
        return max(0, int((time.time() - ts) / 86400))
    except (TypeError, ValueError, OverflowError):
        return 0


def _map_post(p: Dict[str, Any], idx: int, owner: str) -> Optional[Post]:
    """Normalize one post/reel item from any of the actor's result shapes."""
    if not isinstance(p, dict):
        return None

    # Field names vary by source: Apify actors use camelCase (likesCount),
    # Instagram's xdt/GraphQL feed nodes use snake_case (like_count) and
    # legacy web nodes use edge objects (edge_media_preview_like.count).
    # All three families must be read or real engagement silently maps to 0.
    likes_value = _pick(p, (
        "likesCount", "likeCount", "like_count",
        "edge_media_preview_like.count", "edge_liked_by.count",
        # modern xdt nodes expose likes only through preview edge objects
        "preview_likes.count",
    ))
    likes = _to_int(likes_value)
    # Comment counts live under different names per node family: Apify
    # camelCase (commentsCount), web API snake_case (comment_count), legacy
    # edges (edge_media_to_comment.count), and modern xdt feed nodes
    # (edge_media_to_parent_comment.count / preview_comments.count). Missing
    # any of these silently mapped REAL comments to 0.
    comment_value = _pick(p, (
        "commentsCount", "commentCount", "comment_count",
        "edge_media_to_comment.count",
        "edge_media_to_parent_comment.count",
        "preview_comments.count",
    ))
    comments = _to_int(comment_value)
    views = _to_int(_pick(p, (
        "videoViewCount", "playCount", "videoPlayCount",
        "video_view_count", "view_count", "ig_play_count",
    )))

    # Counts sometimes arrive as strings ("1,944", "1.9K", "352K"). Parse
    # them BEFORE the shell-item guard below — otherwise a real post with
    # humanized counts parses as all-zero and is dropped entirely.
    if isinstance(likes_value, str):
        likes = _parse_count_string(likes_value)
    if isinstance(comment_value, str):
        comments = _parse_count_string(comment_value)

    # Exact ISO timestamp (kept for best-time analytics; falls back to None).
    posted_at_iso: Optional[str] = None
    ts_raw = _pick(p, ("timestamp", "takenAtTimestamp", "taken_at_timestamp"))
    if ts_raw is not None:
        try:
            if isinstance(ts_raw, (int, float)) or (isinstance(ts_raw, str) and ts_raw.replace(".", "").isdigit()):
                ts = float(ts_raw)
                if ts > 1e12:
                    ts /= 1000.0
                posted_at_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            else:
                dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                posted_at_iso = dt.isoformat()
        except (TypeError, ValueError, OverflowError):
            posted_at_iso = None
    if likes == 0 and comments == 0 and views == 0:
        # Shell/placeholder items sometimes appear in results; skip them.
        return None

    caption = _pick(p, ("caption", "edge_media_to_caption.edges.0.node.text")) or ""
    if isinstance(caption, list):
        caption = " ".join(
            (e.get("node", {}).get("text", "") if isinstance(e, dict) else str(e))
            for e in caption
        )
    elif isinstance(caption, dict):
        # xdt feed nodes: caption as {text: ...} / {edge_media_to_caption: ...}
        caption = caption.get("text") or _dig(
            caption, "edge_media_to_caption.edges.0.node.text"
        ) or ""
    caption = _clean_str(caption) or ""

    raw_type = str(_pick(p, ("type", "media_type", "productType", "product_type", "__typename")) or "")
    media_type = _MEDIA_TYPE_MAP.get(raw_type)
    if media_type is None:
        # Unmapped label (product_type "feed", unknown versions): decide from
        # shape — video/view markers mean video, multi-item means carousel.
        has_views = _to_int(_pick(p, ("videoViewCount", "video_view_count", "view_count", "ig_play_count", "playCount"))) > 0
        is_video = _as_bool(p.get("is_video")) or _as_bool(p.get("has_audio"))
        if is_video or has_views:
            media_type = "reel" if "clip" in raw_type.lower() else "video"
        elif isinstance(p.get("carousel_media"), list) or _to_int(p.get("media_count")) > 1:
            media_type = "carousel"
        else:
            media_type = "image"
    if media_type == "video" and "clip" in raw_type.lower():
        media_type = "reel"

    hashtags = sorted({f"#{h.lower()}" for h in re.findall(r"#(\w+)", caption)})[:10]

    # "Omitted" means the source feed carried NO comment field under any of
    # the known names (checked at the top of this function) — a backfill
    # candidate. An explicit 0 is a genuine zero.
    comment_omitted = comment_value is None

    return Post(
        # Prefer the shortcode: it is stable across providers and is what the
        # permalink backfill needs to re-address a post.
        id=str(_pick(p, ("shortCode", "shortcode", "code", "id", "url"))) or f"{owner}_{idx}",
        caption=caption[:600],
        likes=likes,
        comments=comments,
        posted_days_ago=_posted_days_ago(p),
        hashtags=hashtags,
        media_type=media_type,
        views=views,
        posted_at=posted_at_iso,
        comment_count_omitted=comment_omitted,
    )


def _map_profile(items: List[Dict[str, Any]], requested: str) -> ProfileData:
    """Turn the actor's dataset items into a ProfileData.

    Handles resultsType=details (one profile item, possibly with embedded
    latestPosts), resultsType=posts (post items, no profile fields), and
    mixed output where both appear."""
    profile_item: Optional[Dict[str, Any]] = None
    post_items: List[Dict[str, Any]] = []

    for it in items:
        if not isinstance(it, dict):
            continue
        if profile_item is None and (
            it.get("followersCount") is not None or it.get("biography") is not None
        ):
            profile_item = it
        elif it.get("likesCount") is not None or it.get("shortCode") is not None:
            post_items.append(it)

    if profile_item is not None:
        for key in ("latestPosts", "posts", "edge_owner_to_timeline_media.edges"):
            embedded = profile_item.get(key)
            if isinstance(embedded, list) and embedded:
                unwrapped = [
                    e.get("node") if isinstance(e, dict) and isinstance(e.get("node"), dict) else e
                    for e in embedded
                ]
                post_items = [p for p in unwrapped if isinstance(p, dict)] or post_items
                break

    if profile_item is None and not post_items:
        raise ValueError(
            f"Instagram profile '@{requested}' not found or returned no data. "
            "Check the spelling of the handle."
        )

    if profile_item is not None:
        pi = profile_item
        username = str(_pick(pi, _PROFILE_FIELDS["username"]) or requested)
        recent_posts = [p for p in (_map_post(x, i, username) for i, x in enumerate(post_items)) if p]
        return ProfileData(
            username=username.lower(),
            full_name=_clean_str(_pick(pi, _PROFILE_FIELDS["full_name"]))
            or username.replace("_", " ").replace(".", " ").title(),
            bio=_clean_str(_pick(pi, _PROFILE_FIELDS["biography"])) or "",
            followers=_to_int(_pick(pi, _PROFILE_FIELDS["followers"])),
            following=_to_int(_pick(pi, _PROFILE_FIELDS["following"])),
            posts_count=_to_int(_pick(pi, _PROFILE_FIELDS["posts_count"])),
            is_verified=_as_bool(_pick(pi, _PROFILE_FIELDS["is_verified"])),
            is_business=_as_bool(_pick(pi, _PROFILE_FIELDS["is_business"])),
            category=_clean_str(_pick(pi, _PROFILE_FIELDS["category"])),
            recent_posts=recent_posts,
        )

    # Posts-only output (no profile fields): derive what we can from posts.
    recent_posts = [p for p in (_map_post(x, i, requested) for i, x in enumerate(post_items)) if p]
    if not recent_posts:
        raise ValueError(f"No usable posts found for '@{requested}'.")
    return ProfileData(
        username=requested.lower(),
        full_name=requested.replace("_", " ").replace(".", " ").title(),
        bio="",
        followers=0,
        following=0,
        posts_count=len(recent_posts),
        is_verified=False,
        is_business=False,
        category=None,
        recent_posts=recent_posts,
    )


def _actor_url(token: str, actor_path: str) -> str:
    # "user/name" → "user~name"; numeric actor ids pass through untouched.
    path = actor_path.replace("/", "~") if "~" not in actor_path else actor_path
    return (
        f"https://api.apify.com/v2/acts/{quote(path, safe='~')}"
        f"/run-sync-get-dataset-items?token={quote(token, safe='')}"
    )


_apify_preflight_ok_until: Dict[str, float] = {}  # token -> ts until which credit was confirmed
PREFLIGHT_RECHECK_SECS = 300  # re-probe free limits API at most every 5 min / token


def _apify_preflight_check(token: str) -> None:
    """Free, unmetered quota gate BEFORE starting a paid actor run.

    GET /users/me/limits is an account-management endpoint — it works even
    when platform credit is spent. Checking it here means a fresh-handle
    request fails in ~1s with a precise message instead of hanging ~15s on
    a doomed actor run. Raises RuntimeError when the account's monthly
    credit is exhausted; never blocks a run when the check itself fails."""
    now = time.time()
    if now < _apify_preflight_ok_until.get(token, 0):
        return  # credit confirmed recently; proceed straight to the run
    try:
        r = httpx.get(
            "https://api.apify.com/v2/users/me/limits",
            headers={"Authorization": f"Bearer {token}"},
            timeout=8,
        )
        d = r.json().get("data", {})
        used = float((d.get("current") or {}).get("monthlyUsageUsd") or 0)
        cap = float((d.get("limits") or {}).get("maxMonthlyUsageUsd") or 0)
        if cap > 0 and used >= cap:
            end = str((d.get("monthlyUsageCycle") or {}).get("endAt") or "")
            reset = end[:10] or "the next billing cycle"
            msg = (
                f"Apify monthly free credit exhausted (${used:.2f} of ${cap:.2f} used). "
                f"Actor runs resume automatically when the cycle resets on {reset} — "
                "or raise the limit now: Apify Console → Settings → Usage & Billing."
            )
            _bench_token(token, "exhausted", msg)
            raise ApifyTokenError(msg)
        _apify_preflight_ok_until[token] = now + PREFLIGHT_RECHECK_SECS
    except RuntimeError:
        raise
    except Exception:
        pass  # preflight must never block a legitimate run


def _usage_limit_message(token: str) -> str:
    """Best-effort dynamic message for the usage-limit block: pull the real
    spend, cap and reset date from Apify's limits API so the error says
    exactly when runs resume. Falls back to a static hint if the API call
    fails (this path must never mask the original error)."""
    try:
        r = httpx.get(
            "https://api.apify.com/v2/users/me/limits",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        d = r.json().get("data", {})
        used = float((d.get("current") or {}).get("monthlyUsageUsd") or 0)
        cap = float((d.get("limits") or {}).get("maxMonthlyUsageUsd") or 0)
        end = str((d.get("monthlyUsageCycle") or {}).get("endAt") or "")
        reset = end[:10] or "the next billing cycle"
        return (
            f"Apify monthly free credit exhausted (${used:.2f} of ${cap:.2f} used). "
            f"Actor runs resume automatically when the cycle resets on {reset} — "
            "or raise the limit now: Apify Console → Settings → Usage & Billing."
        )
    except Exception:
        return (
            "Apify account blocked this actor run: monthly usage hard limit "
            "exceeded. Raise/remove the limit or wait for the billing reset "
            "(Apify console → Settings → Usage & Billing), then retry."
        )


def _require_items(resp: httpx.Response, actor_label: str, token: str) -> List[Dict[str, Any]]:
    """Shared status-code handling + JSON parsing for actor run endpoints.
    Token-level failures (invalid/revoked token, exhausted credit) raise
    ApifyTokenError so the pool fails over to the next token; the failing
    token is benched here with a precise status for /api/usage."""
    if resp.status_code in (401, 403):
        detail = ""
        try:
            err = resp.json().get("error", {})
            detail = f"{err.get('type', '')}: {err.get('message', '')}".strip(": ")
        except Exception:
            detail = (resp.text or "")[:200]
        low = detail.lower()
        if "usage" in low or "limit" in low or "platform-feature-disabled" in low:
            msg = _usage_limit_message(token)
            _bench_token(token, "exhausted", msg)
            raise ApifyTokenError(msg)
        msg = (
            "Apify rejected the request (401/403) — this token looks "
            f"invalid or revoked. {detail or 'Double-check the token value.'}"
        )
        _bench_token(token, "invalid", msg)
        raise ApifyTokenError(msg)
    if resp.status_code == 402:
        msg = _usage_limit_message(token)
        _bench_token(token, "exhausted", msg)
        raise ApifyTokenError(msg)
    if resp.status_code == 404:
        raise RuntimeError(
            f"Apify actor '{actor_label}' not found (404). Check the actor id env var."
        )
    if resp.status_code in (408, 504):
        raise RuntimeError("Apify actor run timed out. Try again in a moment.")
    resp.raise_for_status()
    items = resp.json()
    if not isinstance(items, list):
        raise RuntimeError("Apify returned an unexpected response shape.")
    return items


def _require_apify_token() -> None:
    """Fail FAST (no network) when a live fetch is attempted without a token."""
    if not APIFY_TOKENS:
        raise RuntimeError(
            "No Apify token configured — set APIFY_TOKEN in backend/.env "
            "(the keyless HTTP fetch path works without one)."
        )


async def _actor_call(token: str, run_input: Dict[str, Any], actor_path: str) -> List[Dict[str, Any]]:
    """One run-sync actor call with a SPECIFIC pool token."""
    async with httpx.AsyncClient(timeout=APIFY_RUN_TIMEOUT + 30) as client:
        resp = await client.post(_actor_url(token, actor_path), json=run_input)
    return _require_items(resp, actor_path, token)


async def _run_with_failover(run_input: Dict[str, Any], actor_path: str) -> List[Dict[str, Any]]:
    """Run an actor via the token pool: try each eligible token in order,
    benching tokens that fail at the TOKEN level (monthly credit exhausted,
    invalid/revoked token, billing block) and moving to the next one. Any
    other failure (actor 404, run timeout, bad response shape, network
    error) would fail identically on every token, so it aborts immediately.

    Raises RuntimeError whose message aggregates every token's cause when
    the whole pool is unavailable."""
    _require_apify_token()
    tokens = _eligible_tokens()
    if not tokens:
        raise RuntimeError(_all_benched_message())
    errors: List[str] = []
    for token in tokens:
        idx = APIFY_TOKENS.index(token)  # label by real pool position
        try:
            await asyncio.to_thread(_apify_preflight_check, token)
            items = await _actor_call(token, run_input, actor_path)
            _token_state[token] = {"status": "ready", "detail": "", "until": 0}
            record_fetch_event("ok", f"Apify actor run OK ({_token_label(idx)}, {len(items)} items)")
            return items
        except ApifyTokenError as e:
            record_fetch_event("fail", f"Apify {_token_label(idx)}: {str(e)[:120]}")
            errors.append(f"{_token_label(idx)}: {e}")
            continue
    raise RuntimeError(_aggregate_token_errors(errors))


async def _run_actor(results_type: str, username: str, results_limit: int) -> List[Dict[str, Any]]:
    run_input = {
        "directUrls": [f"https://www.instagram.com/{username}/"],
        "resultsType": results_type,
        "resultsLimit": results_limit,
        "addParentData": True,
    }
    return await _run_with_failover(run_input, APIFY_ACTOR_ID)


async def _run_actor_multi(usernames: List[str], results_type: str = "details", results_limit: int = 13) -> List[Dict[str, Any]]:
    """One actor run for SEVERAL profiles (the actor accepts multiple
    directUrls). This is the key latency win: N rivals cost one run instead
    of N sequential 20-60s fetches."""
    run_input = {
        "directUrls": [f"https://www.instagram.com/{u}/" for u in usernames],
        "resultsType": results_type,
        "resultsLimit": results_limit,
        "addParentData": True,
    }
    return await _run_with_failover(run_input, APIFY_ACTOR_ID)


async def _run_search_actor(queries: List[str], limit: int) -> List[Dict[str, Any]]:
    """Run the Instagram search scraper for user accounts matching the query
    terms. Returns normalized candidate rows (username/full_name/bio/
    followers/verified/private)."""
    run_input = {
        # NOTE: this actor takes ALL keywords in one 'search' field,
        # comma-separated (searchQueries is silently ignored).
        "search": ", ".join(queries[:3]),
        "searchType": "user",
        "searchLimit": max(5, min(limit, 15)),
    }
    items = await _run_with_failover(run_input, APIFY_SEARCH_ACTOR_ID)

    rows: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        username = str(it.get("username") or "").strip()
        if not username or not _USERNAME_RE.match(username):
            continue  # search output mixes in place/hashtag rows
        rows.append({
            "username": username,
            "full_name": _clean_str(it.get("fullName")) or "",
            "bio": _clean_str(it.get("biography")) or "",
            "followers": _to_int(it.get("followersCount")),
            "verified": _as_bool(it.get("verified")),
            "private": _as_bool(it.get("private") or it.get("isPrivate")),
        })
    return rows


# ---------------------------------------------------------------------------
# Provider #0 — OFFICIAL Instagram Graph API (business_discovery)
#
#   GET https://graph.facebook.com/<IG_BUSINESS_ACCOUNT_ID>
#       ?fields=business_discovery.username(<target>){biography,name,
#           followers_count,media_count,
#           media.limit(N){caption,like_count,comments_count,timestamp,
#                          media_type,media_product_type}}
#       &access_token=<IG_ACCESS_TOKEN>
#
# Official, ToS-compliant, no scraping, no IP blocks. Requirements (from
# Meta's docs): the token needs instagram_basic + instagram_manage_insights
# + pages_read_engagement; the TARGET account must be an Instagram Business
# or Creator ("professional") account — personal accounts are NOT
# discoverable, and age-gated accounts return nothing.
#
# Honest gaps in the official API (documented, not bugs):
#   - `following` count is not exposed  -> served as 0
#   - verification badge is not exposed -> served as False
#   - business category is not public   -> served as None
# Errors: ValueError = account-level problem (handle not found / not a
# professional account); RuntimeError = token/app/HTTP problem (these two
# behave differently in the provider ladder: ValueError surfaces, RuntimeError
# falls through to the next provider).
# ---------------------------------------------------------------------------

_GRAPH_BASE = "https://graph.facebook.com"


def _graph_media_fields() -> str:
    """Fields requested on each discovered media object."""
    return "id,caption,like_count,comments_count,timestamp,media_type,media_product_type"


def _business_discovery_fields(username: str, media_limit: int) -> str:
    return (
        f"business_discovery.username({username})"
        f"{{biography,name,followers_count,media_count,"
        f"media.limit({media_limit}){{{_graph_media_fields()}}}}}"
    )


def _raise_graph_error(err: dict) -> None:
    """Translate a Graph API error object into a clear exception.
    code 190 / OAuthException -> token problem (RuntimeError, recoverable by
    the ladder); rate-limit codes -> RuntimeError; username/account problems
    -> ValueError (surfaced to the user)."""
    code = int(err.get("code") or 0)
    msg = str(err.get("message") or "Unknown Graph API error")
    type_ = str(err.get("type") or "")
    low = msg.lower()

    if code == 190 or (type_ == "OAuthException" and "access token" in low):
        raise RuntimeError(
            "Instagram Graph API rejected the access token (code 190). "
            "IG_ACCESS_TOKEN is missing, expired, or lacks the required "
            "permissions (instagram_basic, instagram_manage_insights, "
            "pages_read_engagement). Generate a long-lived token — see the "
            "setup steps in backend/.env.example."
        )
    if code in (4, 17, 32, 613) or "rate limit" in low:
        raise RuntimeError(
            f"Instagram Graph API rate limit hit (code {code}): {msg}"
        )
    if (
        "does not exist" in low
        or "is not a business" in low
        or ("username" in low and "business" in low)
        or "cannot be found" in low
    ):
        raise ValueError(
            f"Instagram account '@{msg}' — the official API reports this "
            "handle either does not exist, is private, or is NOT a "
            "Business/Creator account. The Graph API can only discover "
            "professional accounts; personal accounts need another provider."
        )
    raise RuntimeError(f"Instagram Graph API error (code {code}): {msg}")


def _days_ago_from_iso(ts: str) -> int:
    """Graph API ISO-8601 timestamp ('2026-08-20T12:34:56+0000') -> whole
    days elapsed. Unparseable timestamps count as 0 days old."""
    if not ts:
        return 0
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, int((time.time() - dt.timestamp()) / 86400))
    except (TypeError, ValueError, OverflowError):
        return 0


def _parse_business_discovery_payload(payload: dict, requested: str) -> ProfileData:
    """Graph API response -> ProfileData/Post. Parsing lives in its own
    function so it can be tested with recorded payloads without network."""
    if not isinstance(payload, dict):
        raise RuntimeError("Instagram Graph API returned an unexpected payload shape.")
    if isinstance(payload.get("error"), dict) and payload["error"]:
        _raise_graph_error(payload["error"])

    bd = payload.get("business_discovery")
    if not isinstance(bd, dict) or not bd:
        # A 200 with no business_discovery and no error object means the
        # target handle was not discovered.
        raise ValueError(
            f"Instagram account '@{requested}' was not returned by the "
            "Graph API — the handle either does not exist, is private, or "
            "is not a Business/Creator account (the official API can only "
            "discover professional accounts)."
        )

    followers = int(bd.get("followers_count") or 0)
    if followers == 0 and bd.get("followers_count") is None:
        # Professional accounts always expose followers_count; a missing one
        # means the target is not actually a professional account.
        raise ValueError(
            f"Instagram account '@{requested}' exposes no follower count "
            "via the Graph API — it is probably not a Business/Creator "
            "account. Switch it to a professional account in the Instagram "
            "app, or use a different data provider."
        )

    username = requested.lower()
    full_name = str(bd.get("name") or "").strip() or \
        username.replace("_", " ").replace(".", " ").title()

    posts: List[Post] = []
    media = bd.get("media") or {}
    for item in (media.get("data") or []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        caption = str(item.get("caption") or "")
        raw_type = str(item.get("media_type") or "IMAGE").upper()
        if raw_type == "VIDEO" and str(item.get("media_product_type") or "").upper() == "REELS":
            media_type = "reel"
        elif raw_type == "VIDEO":
            media_type = "video"
        elif raw_type == "CAROUSEL_ALBUM":
            media_type = "carousel"
        else:
            media_type = "image"
        posts.append(Post(
            id=str(item["id"]),
            caption=caption[:600],
            likes=int(item.get("like_count") or 0),
            comments=int(item.get("comments_count") or 0),
            posted_days_ago=_days_ago_from_iso(item.get("timestamp") or ""),
            # Same shape as every other provider: lowercase '#tag' chips,
            # unique, ordered by first appearance, capped at 10.
            hashtags=list(dict.fromkeys(f"#{h.lower()}" for h in re.findall(r"#(\w+)", caption)))[:10],
            media_type=media_type,
            views=0,  # view_count needs extra insight permissions; not public
            posted_at=None,
        ))

    return ProfileData(
        username=username,
        full_name=full_name,
        bio=str(bd.get("biography") or ""),
        followers=followers,
        following=0,  # NOT exposed by the official API — honest zero
        posts_count=int(bd.get("media_count") or 0),
        is_verified=False,  # badge is not exposed by the official API
        is_business=True,   # business_discovery only sees professional accounts
        category=None,      # target's category is not a public field
        recent_posts=posts,
    )


async def fetch_live_profile(username: str) -> ProfileData:
    """Provider #0 — official Instagram Graph API business_discovery fetch.

    Returns a real ProfileData for the target handle. Raises:
      - ValueError  when the TARGET account is unusable (not found, private,
                    or not a Business/Creator account) — surfaced to the user.
      - RuntimeError when the TOKEN/app is the problem (invalid/expired,
                    missing permissions, rate limit, HTTP failure) — the
                    provider ladder falls through to the next provider.
    Env: IG_ACCESS_TOKEN, IG_BUSINESS_ACCOUNT_ID (your own professional
    account's numeric ID), optional IG_GRAPH_VERSION (default v25.0).
    """
    if not _has_graph_credentials():
        raise RuntimeError(
            "Instagram Graph API is not configured: set IG_ACCESS_TOKEN and "
            "IG_BUSINESS_ACCOUNT_ID in backend/.env (see .env.example)."
        )
    # Graph caps media.limit at 25; the project sample width (12) fits.
    media_limit = max(1, min(25, int(os.getenv("IG_PERMALINK_POSTS", "12"))))
    url = f"{_GRAPH_BASE}/{IG_GRAPH_VERSION}/{IG_BUSINESS_ACCOUNT_ID}"
    params = {
        "fields": _business_discovery_fields(username, media_limit),
        "access_token": IG_ACCESS_TOKEN,
    }
    try:
        async with httpx.AsyncClient(
            timeout=IG_GRAPH_TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(url, params=params)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Instagram Graph API request failed: {str(e)[:140]}")

    if resp.status_code != 200:
        try:
            err = resp.json().get("error") or {}
        except Exception:
            err = {}
        if err:
            _raise_graph_error(err)
        raise RuntimeError(
            f"Instagram Graph API returned HTTP {resp.status_code} for the "
            "business_discovery query."
        )
    try:
        payload = resp.json()
    except Exception:
        raise RuntimeError("Instagram Graph API returned a non-JSON response.")

    return _parse_business_discovery_payload(payload, username)


async def _fetch_live_profile(username: str) -> ProfileData:
    """Fetch a real profile. Primary: one 'details' run, which returns the
    profile fields plus its ~12 latest posts with real engagement. Fallback:
    a 'posts' run if the details run yielded no usable posts."""
    items = await _run_actor("details", username, results_limit=13)
    profile = _map_profile(items, username)
    _stash_related(username, items)

    # Transient scrape glitches can yield an empty profile shell; retry once.
    if profile.followers == 0 and not profile.recent_posts:
        try:
            items = await _run_actor("details", username, results_limit=13)
            retry = _map_profile(items, username)
            if retry.followers > 0 or retry.recent_posts:
                profile = retry
                _stash_related(username, items)
        except (RuntimeError, httpx.HTTPError):
            pass

    if not profile.recent_posts:
        try:
            items2 = await _run_actor("posts", username, results_limit=12)
            profile2 = _map_profile(items2, username)
        except (ValueError, RuntimeError, httpx.HTTPError):
            profile2 = None
        if profile2 and profile2.recent_posts:
            profile.recent_posts = profile2.recent_posts

    return profile


# ---------------------------------------------------------------------------
# Provider #2 — keyless direct Instagram fetch (web_profile_info)
#
# The same endpoint instagram.com's own frontend calls:
#   GET /api/v1/users/web_profile_info/?username=<handle>
# No login, no API key, no actor run. It cannot do competitor discovery or
# batched multi-profile runs like Apify, but it needs NO token — so real
# data keeps flowing when every Apify token is exhausted or none exists.
#
# Instagram rejects calls without full browser context, so this provider
# first "bootstraps" like a real browser: GET instagram.com to receive
# session cookies (csrftoken, ig_did, …) and the page's LSD token, then
# issues the API GET with matching UA/origin/referer + x-ig-app-id,
# x-fb-lsd and x-csrftoken headers. A plain HTML profile page parse is
# kept as an additional fallback.
# ---------------------------------------------------------------------------

IG_WEB_APP_ID = os.getenv("IG_WEB_APP_ID", "936619743392459")
DIRECT_FETCH_ENABLED = os.getenv("DIRECT_FETCH", "true").lower() in ("1", "true", "yes")
_DIRECT_TIMEOUT = int(os.getenv("DIRECT_FETCH_TIMEOUT", "15"))  # seconds
_DIRECT_HOSTS = ("https://www.instagram.com", "https://i.instagram.com")

_DIRECT_HEADERS = {
    "x-ig-app-id": IG_WEB_APP_ID,
    "x-requested-with": "XMLHttpRequest",
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "referer": "https://www.instagram.com/",
    "origin": "https://www.instagram.com",
}

# Outbound proxy for the keyless Instagram HTTP layer (httpx clients).
# Cloud hosts (Render/Railway/Fly) get their datacenter IPs hard-
# blocked by Instagram — 401/429 on every call — while the same code works
# from residential IPs. Setting IG_PROXY_URL (e.g. a Webshare/IPRoyal
# residential proxy) routes every Instagram request through it and restores
# live fetching on those hosts. Leave unset locally.
_IG_PROXY_URL = (os.getenv("IG_PROXY_URL") or "").strip() or None

# Relay-ladder cooldown: after a full pass where every public relay failed,
# skip the ladder entirely for this window (dead-relay latency is up to
# ~25s x N relays — pointless to re-pay on every throttled request).
_RELAY_DOWN_COOLDOWN = float(os.getenv("IG_RELAY_COOLDOWN", "300"))
_relay_down_until = 0.0


def _ig_httpx_proxy() -> Optional[str]:
    """Proxy URL for httpx clients (httpx handles auth inside the URL).
    Returns None when unset so clients behave exactly as before."""
    return _IG_PROXY_URL


def _extract_lsd_token(html: str) -> str:
    """Pull the page's LSD token (anti-CSRF token the frontend sends as
    x-fb-lsd). Returns '' when absent."""
    m = re.search(r'"LSD",\[\],\{"token":"([^"]+)"\}', html)
    if m:
        return m.group(1)
    m = re.search(r'"token":"([A-Za-z0-9_-]{20,})"', html)
    return m.group(1) if m else ""


# Bootstrapped anonymous-session cache (cookies + LSD token), shared across
# fetches for their TTL — one bootstrap per TTL window instead of per request.
_DIRECT_SESSION_TTL = float(os.getenv("IG_SESSION_TTL", "1800"))
_direct_session: Dict[str, Any] = {"cookies": None, "lsd": "", "ts": 0.0}


def _bootstrap_direct_session() -> tuple:
    """Return (cookie_jar, lsd_token), seeding them from instagram.com when
    missing/stale. Sync (httpx sync client) — call via asyncio.to_thread.
    Never raises: a failed bootstrap returns (None, '') and the API call
    is attempted with static headers only (which may still work)."""
    now = time.time()
    if _direct_session["cookies"] is not None and (now - _direct_session["ts"]) < _DIRECT_SESSION_TTL:
        return _direct_session["cookies"], _direct_session["lsd"]
    try:
        jar = httpx.Cookies()
        with httpx.Client(timeout=15, follow_redirects=True, proxy=_ig_httpx_proxy()) as client:
            resp = client.get("https://www.instagram.com/", headers={
                "user-agent": _DIRECT_HEADERS["user-agent"],
                "accept-language": "en-US,en;q=0.9",
            })
            if resp.status_code == 200:
                jar.update(resp.cookies)
                _direct_session["cookies"] = jar
                _direct_session["lsd"] = _extract_lsd_token(resp.text)
                _direct_session["ts"] = now
                return jar, _direct_session["lsd"]
    except Exception:
        pass
    return _direct_session["cookies"], _direct_session["lsd"]  # possibly stale


def _map_direct_user(user: Dict[str, Any], requested: str) -> ProfileData:
    """web_profile_info user object -> ProfileData.

    Reuses _map_post for the embedded latest-posts edges, so likes/comments/
    views/timestamps/media types map with the same tolerance as Apify rows."""
    username = str(user.get("username") or requested).strip().lower() or requested.lower()
    media = (user.get("edge_owner_to_timeline_media")
             or user.get("xdt_api__v1__feed__user_timeline_graphql_connection")
             or {})
    edges = (media.get("edges")) or []
    recent_posts = [
        p for p in (
            _map_post((e.get("node") if isinstance(e, dict) else None) or {}, i, username)
            for i, e in enumerate(edges)
        )
        if p
    ]
    # Modern GraphQL blobs (Relay prefetch, xdt_api v1) carry the stats as
    # flat scalar fields — follower_count/following_count/all_media_count —
    # while the legacy web_profile_info shape wraps them in edge_* objects.
    # Accept both or big accounts parsed 0 followers from a perfectly good
    # payload.
    followers = (
        _to_int(_dig(user, "edge_followed_by.count"))
        or _to_int(user.get("follower_count"))
    )
    following = (
        _to_int(_dig(user, "edge_follow.count"))
        or _to_int(user.get("following_count"))
    )
    posts_count = (
        _to_int(_dig(user, "edge_owner_to_timeline_media.count"))
        or _to_int(user.get("all_media_count"))
        or _to_int(user.get("media_count"))
    )
    return ProfileData(
        username=username,
        full_name=_clean_str(user.get("full_name"))
        or username.replace("_", " ").replace(".", " ").title(),
        bio=_clean_str(user.get("biography")) or "",
        followers=followers,
        following=following,
        posts_count=posts_count,
        is_verified=_as_bool(user.get("is_verified")),
        is_business=_as_bool(user.get("is_business_account")),
        category=_clean_str(user.get("category_name"))
        or _clean_str(user.get("business_category_name")),
        recent_posts=recent_posts,
    )


def _full_name_from_og_title(html: str, requested: str) -> str:
    """og:title looks like 'Name (@handle) • Instagram photos and videos' —
    extract the display name, fall back to a prettified handle."""
    m = re.search(r'<meta property="og:title" content="([^"]*)"', html)
    title = m.group(1) if m else ""
    title = re.sub(r"\s*•\s*Instagram.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\(@[^)]*\)\s*$", "", title).strip()
    return _clean_str(title) or requested.replace("_", " ").replace(".", " ").title()


def _stats_from_og_description(desc: str) -> Optional[Tuple[int, int, int]]:
    """og:description text -> (followers, following, posts); None if unusable."""
    if not desc:
        return None
    # Numbers are often abbreviated for large accounts: "687M Followers",
    # "1.2K Following", "8,584 Posts". Capture the optional K/M/B suffix.
    nums = re.findall(
        r"([\d,.]+)\s*([KMB])?\s+(Followers?|Following|Posts?)",
        desc, re.IGNORECASE,
    )
    if not nums:
        return None

    _MULT = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}

    def _num(digits, suffix):
        try:
            v = float(digits.replace(",", ""))
        except ValueError:
            return 0
        return int(v * _MULT.get((suffix or "").lower(), 1))

    followers = following = posts_count = 0
    for raw, suffix, label in nums:
        low = label.lower()
        if low.startswith("follower"):
            followers = _num(raw, suffix)
        elif low == "following":
            following = _num(raw, suffix)
        elif low.startswith("post"):
            posts_count = _num(raw, suffix)
    if followers == 0:
        return None
    return followers, following, posts_count


def _profile_stats_from_og(html: str) -> Optional[Tuple[int, int, int]]:
    """og:description meta tag -> (followers, following, posts).

    REAL numbers Instagram itself publishes, but abbreviated for big
    accounts ("104M Followers, 91 Following, 18K Posts") — so these are
    only used as a floor/backfill for stats the precise GraphQL blob did
    not carry. Returns None when the page exposes no usable description."""
    m = re.search(r'<meta property="og:description" content="([^"]*)"', html)
    desc = m.group(1) if m else ""
    return _stats_from_og_description(desc)


def _extract_profile_from_html(html: str, requested: str) -> Optional[ProfileData]:
    """Parse a instagram.com/<handle>/ page into a profile.

    Three tiers, most-precise first:
      1. Embedded GraphQL Relay JSON blob (xdt shape): EXACT counts —
         follower_count 104351749 where og-tags only show "104M".
      2. Embedded legacy page_data JSON (edge_followed_by shape).
      3. og:description meta tags — real stats but approximate for big
         accounts.
    Returns None when nothing usable is found."""
    if not html:
        return None

    # Preferred: embedded JSON with the full user object.
    #
    # Two shapes appear in the wild:
    #   - legacy web_profile_info / page_data: user has edge_followed_by etc.
    #   - modern GraphQL Relay prefetch (xdt): user carries flat scalar stats
    #     (follower_count, following_count, all_media_count) and NO edge_*
    #     wrappers — that is the blob with EXACT counts for big accounts
    #     (og-tags only show "104M Followers").
    og_cache: Optional[Tuple[int, int, int]] = None
    best: Optional[ProfileData] = None
    best_exact = -1
    for m in re.finditer(r'<script type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue

        candidates: List[Dict[str, Any]] = []

        def _find_user(obj):
            if isinstance(obj, dict):
                has_legacy = "edge_followed_by" in obj
                has_modern = ("follower_count" in obj and "username" in obj
                              and obj.get("username"))
                if has_legacy or has_modern:
                    candidates.append(obj)
                for v in obj.values():
                    _find_user(v)
            elif isinstance(obj, list):
                for v in obj:
                    _find_user(v)
            return None

        _find_user(data)
        if not candidates:
            continue

        # Prefer the candidate whose username matches the handle — profile
        # pages embed other accounts' user objects (tagged users, suggested
        # accounts) whose stats would poison the result.
        want = requested.strip().lstrip("@").lower()
        candidates.sort(
            key=lambda u: (
                str(u.get("username") or "").lower() != want,
                -_to_int(u.get("follower_count")
                         or _dig(u, "edge_followed_by.count")),
            )
        )
        for user in candidates:
            try:
                profile = _map_direct_user(user, requested)
            except Exception:
                continue
            # Count how many key stats came from precise sources (non-zero).
            exact = (
                (profile.followers > 0) + (profile.following > 0)
                + (profile.posts_count > 0) + bool(profile.bio)
            )
            if exact > best_exact:
                best, best_exact = profile, exact
        if best is not None and best.followers > 0:
            break

    if best is not None:
        # og-tags round big numbers ("104M Followers"); the GraphQL blob has
        # exact ones. Use og only to fill stats the blob did not carry (its
        # all_media_count can be null, e.g. when the grid is lazy-loaded).
        if best.followers == 0 or best.posts_count == 0:
            if og_cache is None:
                og_cache = _profile_stats_from_og(html)
            if og_cache is not None:
                o_f, o_fo, o_p = og_cache
                if best.followers == 0:
                    best.followers = o_f
                if best.following == 0:
                    best.following = o_fo
                if best.posts_count == 0:
                    best.posts_count = o_p
        return best

    # Last resort: og:description meta tags — real stats but APPROXIMATE for
    # big accounts ("104M Followers"), kept only as the floor.
    og = _profile_stats_from_og(html)
    if og is None:
        return None
    followers, following, posts_count = og
    return ProfileData(
        username=requested.lower(),
        full_name=_full_name_from_og_title(html, requested),
        bio="",
        followers=followers,
        following=following,
        posts_count=posts_count,
        is_verified=False,
        is_business=False,
        category=None,
        recent_posts=[],
    )


# ---------------------------------------------------------------------------
# GraphQL feed enrichment (provider #2 add-on)
#
# Instagram's classic feed query — the same one the logged-out web app uses
# when you click "load more" on a profile:
#   GET /graphql/query/?query_id=17842794232208280
#       &variables={"id":"<numeric_user_id>","first":12,"after":"<cursor>"}
# It returns the profile's latest posts WITH real engagement: likes,
# comments, view counts, timestamps and captions. Needs the numeric user id
# (present in the rendered profile DOM) plus the bootstrapped browser
# session (cookies + csrf). Best-effort enrichment: when it fails, the
# profile-stats-only result is still returned.
# ---------------------------------------------------------------------------

_GQL_FEED_QUERY_ID = os.getenv("IG_GQL_QUERY_ID", "17842794232208280")


def _extract_user_id(dom: str) -> str:
    """Numeric Instagram user id from a rendered profile page. The canonical
    source is the route marker 'profilePage_<id>'; fallback scans the Relay
    payload for the id sitting next to the username."""
    m = re.search(r"profilePage_(\d{5,})", dom)
    if m:
        return m.group(1)
    m = re.search(
        r'"username"\s*:\s*"[^"]*"\s*,\s*"id"\s*:\s*"(\d{5,})"', dom
    )
    return m.group(1) if m else ""


def _parse_graphql_feed(payload: Any, owner: str) -> List[Post]:
    """Map the feed-query response to Post rows (real likes/comments/views)."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") or {}
    user = data.get("user") or {}
    media = (
        user.get("edge_owner_to_timeline_media")
        or user.get("xdt_api__v1__feed__user_timeline_graphql_connection")
        or {}
    )
    edges = media.get("edges") or []
    posts: List[Post] = []
    for i, e in enumerate(edges):
        node = e.get("node") if isinstance(e, dict) else None
        if not isinstance(node, dict):
            continue
        p = _map_post(node, i, owner)
        if p:
            posts.append(p)
    return posts


def _gql_feed_sync(user_id: str, first: int = 12) -> Optional[Any]:
    """One feed-query call with the bootstrapped browser session (sync —
    call via asyncio.to_thread). Returns the parsed JSON payload, or None
    on any failure (enrichment must never break the profile fetch)."""
    if not user_id:
        return None
    try:
        cookies, _lsd = _bootstrap_direct_session()
        csrf = (cookies.get("csrftoken") if cookies is not None else "") or ""
        variables = json.dumps({"id": user_id, "first": first})
        headers = {
            "user-agent": _DIRECT_HEADERS["user-agent"],
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "x-requested-with": "XMLHttpRequest",
            "referer": "https://www.instagram.com/",
        }
        if csrf:
            headers["x-csrftoken"] = csrf
        with httpx.Client(
            timeout=_DIRECT_TIMEOUT, follow_redirects=True, cookies=cookies,
            proxy=_ig_httpx_proxy(),
        ) as client:
            resp = client.get(
                "https://www.instagram.com/graphql/query/",
                params={"query_id": _GQL_FEED_QUERY_ID, "variables": variables},
                headers=headers,
            )
        if resp.status_code != 200:
            return None
        payload = resp.json()
        # Instagram signals throttling/blocks with status != "ok".
        if str((payload or {}).get("status") or "").lower() != "ok":
            return None
        return payload
    except Exception:
        return None


async def _enrich_with_graphql_feed(profile: ProfileData, user_id: str) -> ProfileData:
    """Attach real post engagement (likes/comments/views/timestamps) from the
    classic GraphQL feed query to a profile fetched by the keyless fallback.
    Best-effort: returns the profile unchanged when the query fails."""
    if not user_id or not profile:
        return profile
    try:
        payload = await asyncio.wait_for(
            asyncio.to_thread(_gql_feed_sync, user_id, 12), timeout=12
        )
        if payload:
            posts = _parse_graphql_feed(payload, profile.username)
            if posts:
                profile.recent_posts = posts
    except (asyncio.TimeoutError, Exception):
        pass  # stats-only profile is still a valid result
    return profile


async def _fetch_direct_profile(username: str) -> ProfileData:
    """Provider #2: keyless GET to Instagram's web_profile_info endpoint.

    Flow: bootstrap browser context (cookies + LSD token) → GET the API with
    matching headers → on failure, HTML-parse the profile page as fallback.
    Works for public profiles with zero credentials, so it serves as the
    fallback when the Apify pool is exhausted/benched or unconfigured.

    Raises ValueError when the handle genuinely doesn't exist, RuntimeError
    when Instagram blocks/rate-limits every attempt."""
    perf = _Perf(f"direct @{username}")
    global _api_block_until
    # Pure-HTTP keyless ladder (no browser): bootstrapped session cookies +
    # LSD token, then Instagram's own web_profile_info endpoint. IG_FETCH_MODE
    # is retained for compatibility; any value other than "off" runs the HTTP
    # ladder (it is the only mode now).
    if os.getenv("IG_FETCH_MODE", "http").lower() == "off":
        raise RuntimeError("DIRECT fetch disabled via IG_FETCH_MODE=off.")
    cookies, lsd = await asyncio.to_thread(_bootstrap_direct_session)

    headers = dict(_DIRECT_HEADERS)
    if lsd:
        headers["x-fb-lsd"] = lsd
        headers["x-asbd-id"] = "129477"
    csrf = ""
    if cookies is not None:
        csrf = cookies.get("csrftoken") or ""
        if csrf:
            headers["x-csrftoken"] = csrf

    last_status = 0
    last_err = ""
    async with httpx.AsyncClient(
        timeout=_DIRECT_TIMEOUT, follow_redirects=True, cookies=cookies,
        proxy=_ig_httpx_proxy(),
    ) as client:
        for base in _DIRECT_HOSTS[:1]:  # www host only; i.instagram needs app auth
            try:
                resp = await client.get(
                    f"{base}/api/v1/users/web_profile_info/",
                    params={"username": username},
                    headers=headers,
                )
            except httpx.HTTPError as e:
                last_err = str(e)[:120]
                continue
            last_status = resp.status_code
            if resp.status_code in (401, 403, 429):
                last_err = f"throttled (HTTP {resp.status_code})"
                # Remember the block so later fetches skip this doomed call
                # (and its latency) for a cooldown window.
                _api_block_until = time.monotonic() + _API_BLOCK_COOLDOWN
                continue  # rate-limited/blocked — fall through to HTML mode
            resp.raise_for_status()
            try:
                payload = resp.json()
            except Exception:
                last_err = "non-JSON response (likely a login page)"
                continue
            user = ((payload or {}).get("data") or {}).get("user")
            if not isinstance(user, dict):
                # status ok + user null = the handle does not exist.
                raise ValueError(
                    f"Instagram profile '@{username}' not found. "
                    "Check the spelling of the handle."
                )
            profile = _map_direct_user(user, username)
            if profile.followers == 0 and not profile.recent_posts:
                raise ValueError(
                    f"Instagram profile '@{username}' returned no usable data "
                    "(private account, or Instagram refused the request)."
                )
            # Likes hidden by Instagram (or stripped by xdt nodes) get real
            # values from the posts' permalink pages before caching.
            perf.stage("web_profile_info OK")
            record_fetch_event("ok", f"@{username} via web_profile_info (keyless HTTP)")
            result = await _enrich_with_graphql_feed(
                profile, str(user.get("pk") or user.get("id") or "")
            )
            perf.stage("post enrichment done")
            return result

    # Fallback 1: plain-HTML profile page via HTTP (fast, when it works).
    html_floor: Optional[ProfileData] = None  # real stats, no posts
    try:
        async with httpx.AsyncClient(
            timeout=_DIRECT_TIMEOUT, follow_redirects=True,
            proxy=_ig_httpx_proxy(),
        ) as client:
            resp = await client.get(
                f"https://www.instagram.com/{username}/",
                headers={
                    "user-agent": _DIRECT_HEADERS["user-agent"],
                    "accept-language": "en-US,en;q=0.9",
                },
            )
        if resp.status_code == 200:
            profile = _extract_profile_from_html(resp.text, username)
            if profile is not None and profile.recent_posts:
                perf.stage("HTML floor has posts")
                return profile  # full data — done
            if profile is not None:
                html_floor = profile  # real stats, no posts — keep as floor
            last_err = f"{last_err}; HTML page had no parseable profile data"
        else:
            last_err = f"{last_err}; HTML fallback got HTTP {resp.status_code}"
    except httpx.HTTPError as e:
        last_err = f"{last_err}; HTML fallback failed: {str(e)[:100]}"

    # Nothing better than real stats without engagement — serve the HTML
    # floor rather than failing outright (it is real data).
    if html_floor is not None:
        record_fetch_event("ok", f"@{username} stats-only via HTML floor (no posts)")
        return html_floor

    # Fallback 2: keyless public-relay ladder — DIFFERENT egress IPs.
    # Datacenter hosts (Vercel/Render/Railway) get Instagram's own endpoints
    # hard-throttled (401/429 on every call regardless of headers) while the
    # same requests succeed from residential IPs. A CORS/reader relay fetches
    # instagram.com FROM THE RELAY'S IPs and hands back the page HTML, which
    # _extract_profile_from_html already parses (exact GraphQL-blob stats +
    # embedded posts when present). No key, no login, no browser. Each relay
    # failing just moves to the next; when every relay fails the request
    # still fails honestly below instead of ever serving simulated data.
    #
    # _relay_down_until: when a full relay pass fails, skip the ladder for a
    # cooldown window so throttled periods don't pay dead-relay latency on
    # every request.
    global _relay_down_until
    relay_floor: Optional[ProfileData] = None  # real stats, no posts
    if time.monotonic() >= _relay_down_until:
        relay_targets = (
            ("api.allorigins.win",
             "https://api.allorigins.win/raw?url="
             + quote(f"https://www.instagram.com/{username}/", safe="")),
            ("corsproxy.io",
             "https://corsproxy.io/?url="
             + quote(f"https://www.instagram.com/{username}/", safe="")),
            ("r.jina.ai",
             f"https://r.jina.ai/https://www.instagram.com/{username}/",
             {"x-return-format": "html"}),
        )
        for relay_host, relay_url, *extra_headers in relay_targets:
            try:
                async with httpx.AsyncClient(
                    timeout=max(_DIRECT_TIMEOUT, 25), follow_redirects=True,
                    proxy=_ig_httpx_proxy(),
                ) as client:
                    rresp = await client.get(relay_url, headers={
                        "user-agent": _DIRECT_HEADERS["user-agent"],
                        "accept-language": "en-US,en;q=0.9",
                        **(extra_headers[0] if extra_headers else {}),
                    })
            except httpx.HTTPError as e:
                last_err = f"{last_err}; relay {relay_host} failed: {str(e)[:80]}"
                continue
            if rresp.status_code != 200:
                last_err = f"{last_err}; relay {relay_host} got HTTP {rresp.status_code}"
                continue
            rprofile = _extract_profile_from_html(rresp.text, username)
            if rprofile is None:
                last_err = f"{last_err}; relay {relay_host} page had no parseable profile"
                continue
            if rprofile.recent_posts:
                perf.stage("relay HTML has posts")
                record_fetch_event("ok", f"@{username} via keyless relay {relay_host} (stats+posts)")
                return rprofile
            if relay_floor is None:
                relay_floor = rprofile  # real stats without posts — keep as floor
            last_err = f"{last_err}; relay {relay_host} served stats without posts"
        if relay_floor is None:
            _relay_down_until = time.monotonic() + _RELAY_DOWN_COOLDOWN

    if relay_floor is not None:
        record_fetch_event("ok", f"@{username} stats-only via keyless relay floor (no posts)")
        return relay_floor

    record_fetch_event(
        "blocked",
        f"keyless ladder failed for @{username} (HTTP {last_status or 'n/a'}): {last_err}",
    )
    raise RuntimeError(
        f"Keyless direct fetch failed for @{username} "
        f"(last HTTP status {last_status or 'n/a'}"
        f"{'; ' + last_err if last_err else ''}) — Instagram is rate-limiting "
        "or blocking this host's IPs (expected on Vercel/Render datacenter "
        "ranges). Fixes: set IG_PROXY_URL to a residential proxy, configure "
        "an Apify token, or IG_ACCESS_TOKEN + IG_BUSINESS_ACCOUNT_ID (the "
        "official Graph API allows datacenter IPs) — simulated data is "
        "never served."
    )


# ---------------------------------------------------------------------------
# Competitor discovery — related accounts + local cache mining + search
# ---------------------------------------------------------------------------

def _candidate_row(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize one relatedProfiles entry into a candidate dict."""
    if not isinstance(r, dict):
        return None
    username = str(r.get("username") or "").strip()
    if not username or not _USERNAME_RE.match(username):
        return None
    return {
        "username": username,
        "full_name": r.get("fullName") or "",
        "bio": r.get("biography") or "",
        "followers": _to_int(r.get("followersCount")),
        "verified": _as_bool(r.get("verified")),
        "private": _as_bool(r.get("private")),
    }


def _stash_related(username: str, items: List[Dict[str, Any]]) -> None:
    """Opportunistically remember relatedProfiles from a details run so
    discovery usually costs zero extra actor runs."""
    try:
        if items and isinstance(items[0], dict):
            rp = items[0].get("relatedProfiles")
            if isinstance(rp, list) and rp:
                _related_cache[username.lower()] = (time.monotonic(), rp)
    except Exception:
        pass


_STOPWORDS = {
    "the", "and", "for", "with", "your", "you", "our", "this", "that", "from",
    "into", "official", "page", "account", "follow", "posts", "post", "insta",
    "instagram", "https", "http", "www", "com", "dm", "all", "are", "was",
}


def _search_terms_for(username: str, profile_item: Optional[Dict[str, Any]]) -> List[str]:
    """Derive SHORT competitor-search terms from the account itself. Bio words
    carry the strongest niche signal, then full-name words, then username-stem
    words. Instagram user search works best on 1-word queries — long phrases
    return fuzzy junk. Works for ANY account — this is what makes discovery
    universal."""
    raw: List[str] = []
    if profile_item:
        bio = str(profile_item.get("biography") or "")
        raw.extend(bio.replace("\n", " ").split(" "))
        name = str(profile_item.get("fullName") or "").strip()
        if name and 2 <= len(name) <= 40:
            raw.extend(name.split())
    stem = username.split(".")[0].replace("_", " ").replace("-", " ").strip()
    raw.extend(stem.split())

    words: List[str] = []
    seen: set = set()
    for w in raw:
        w = w.strip("#|.,!?").strip()
        if (
            len(w) >= 4
            and w.lower() not in _STOPWORDS
            and not w.startswith(("@", "http", "www"))
            and w.isalpha()
            and w.lower() not in seen
        ):
            seen.add(w.lower())
            words.append(w.lower())
        if len(words) >= 8:
            break
    return words[:3]


def _rank_by_relevance(candidates: List[Dict[str, Any]], terms: List[str]) -> List[Dict[str, Any]]:
    """Soft relevance ordering: candidates sharing tokens with the account's
    own words come first; follower count breaks ties."""
    tokens = {t.lower() for q in terms for t in q.split()}

    def score(c: Dict[str, Any]) -> float:
        text = f"{c.get('username', '')} {c.get('full_name', '')} {c.get('bio', '')}".lower()
        overlap = sum(1 for t in tokens if t in text)
        followers = c.get("followers") or 0
        return overlap * 100 + (followers ** 0.25) / 10

    return sorted(candidates, key=score, reverse=True)


def _local_discover_sync(username: str, limit: int) -> List[Dict[str, Any]]:
    """Mine the LOCAL disk cache for competitor candidates — zero network, no
    tokens of any kind. Signals, all from previously fetched REAL data:

      1. @mentions in the target's own cached post captions (collab signal, x3)
      2. @mentions by other cached accounts, when the mentioned account is
         itself locally known (co-niche signal, x1)
      3. niche overlap with other cached accounts (shared hashtags/category)

    Only candidates whose own profile is cached within the profile TTL are
    returned, so the whole competitor-research flow can complete offline when
    the provider is unavailable."""
    uname = username.lower()
    profiles: Dict[str, Dict[str, Any]] = {}
    try:
        with _cache_conn() as conn:
            rows = conn.execute(
                "SELECT key, value, cached_at FROM cache WHERE key LIKE 'profile:%'"
            ).fetchall()
    except Exception:
        return []
    now = time.time()
    for key, value, ts in rows:
        try:
            d = json.loads(value)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        h = key.split(":", 1)[1].strip().lower()
        if h:
            profiles[h] = d

    if uname not in profiles:
        return []  # nothing locally known about this account to mine from

    def _captions(d: Dict[str, Any]):
        for p in d.get("recent_posts") or []:
            if isinstance(p, dict):
                yield p.get("caption") or ""

    weights: Dict[str, int] = {}

    def add(h: str, w: int) -> None:
        h = (h or "").strip().lstrip("@").lower()
        if not h or h == uname or not _USERNAME_RE.match(h):
            return
        if h in {"p", "reel", "reels", "explore", "stories", "tv"}:
            return
        weights[h] = weights.get(h, 0) + w

    # Signal 1: the target's own captions — who they tag/collab with.
    for cap in _captions(profiles[uname]):
        for h in re.findall(r"@([A-Za-z0-9._]{2,30})", cap):
            add(h, 3)

    # Signal 2: mentions by other cached accounts (only for handles we can
    # actually serve offline — mentioned-but-unknown handles would be dead
    # candidates without a provider).
    for h0, d in profiles.items():
        if h0 == uname:
            continue
        for cap in _captions(d):
            for h in re.findall(r"@([A-Za-z0-9._]{2,30})", cap):
                if h.lower() in profiles:
                    add(h, 1)

    # Signal 3: niche overlap with any other cached account.
    tgt_tags = set()
    for p in profiles[uname].get("recent_posts") or []:
        if isinstance(p, dict):
            tgt_tags.update(p.get("hashtags") or [])
    tgt_cat = (profiles[uname].get("category") or "").lower()
    for h0, d in profiles.items():
        if h0 == uname:
            continue
        overlap = 0
        for p in d.get("recent_posts") or []:
            if isinstance(p, dict):
                overlap += len(tgt_tags & set(p.get("hashtags") or []))
        if tgt_cat and (d.get("category") or "").lower() == tgt_cat:
            overlap += 2
        if overlap > 0:
            weights[h0] = weights.get(h0, 0) + overlap

    ranked = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)
    out: List[Dict[str, Any]] = []
    for h, _w in ranked:
        if h not in profiles or h == uname:
            continue
        # Candidate must be cached within the profile TTL so its research
        # stays grounded in reasonably fresh REAL data.
        row_ts = next((ts for key, _v, ts in rows if key == f"profile:{h}"), 0)
        if now - row_ts > _DISK_TTL_PROFILE:
            continue
        d = profiles[h]
        out.append({
            "username": h,
            "full_name": d.get("full_name") or "",
            "bio": (d.get("bio") or "")[:160],
            "followers": d.get("followers") or 0,
            "verified": bool(d.get("is_verified")),
            "private": False,
        })
        if len(out) >= max(limit, 1):
            break
    return out


async def _local_discover(username: str, limit: int) -> List[Dict[str, Any]]:
    """Async wrapper; never raises — an empty list means 'nothing local'."""
    try:
        return await asyncio.to_thread(_local_discover_sync, username, limit)
    except Exception:
        return []


async def discover_related_profiles(username: str, limit: int = 30) -> List[Dict[str, Any]]:
    """Find candidate competitors for ANY Instagram handle.

    Strategy (in order):
      1. Local cache mining (mentions/hashtag overlap) — free, instant.
      2. Instagram's related-accounts signal for the handle — usually free,
         captured during the main profile fetch.
      3. Keyword search over Instagram users, with terms derived from the
         account's username/full name/bio — so accounts with no related-
         accounts data still get competitors.
    """
    username = normalize_username(username)

    # Disk cache: discovered competitor lists drift slowly — serve instantly
    # for a day instead of re-running discovery actor calls.
    cache_key = f"related:{username}:{limit}"
    disk = await asyncio.to_thread(_cache_get, cache_key, _DISK_TTL_DISCOVERY)
    if isinstance(disk, list) and disk:
        return disk

    # Provider-free discovery: mine the local profile cache (mentions,
    # hashtag/category overlap). When this yields candidates, competitor
    # research completes with ZERO provider calls — the LLM does the
    # selection and analysis, the numbers come from previously fetched
    # real data. Tried before ANY paid provider run.
    local = await _local_discover(username, limit)
    if local:
        await asyncio.to_thread(_cache_set, cache_key, local)
        return local

    # No discovery actor token: only cache-mined candidates are possible,
    # but discovery itself never raises — return what we have.
    if not APIFY_TOKENS:
        return []

    profile_item: Optional[Dict[str, Any]] = None
    async with _CACHE_LOCK:
        entry = _related_cache.get(username)
    rp: Optional[List[Any]] = None
    if entry and (time.monotonic() - entry[0]) < _CACHE_TTL:
        rp = entry[1]
    elif entry:
        _related_cache.pop(username, None)

    if rp is None:
        try:
            items = await _run_actor("details", username, results_limit=1)
            if items and isinstance(items[0], dict):
                profile_item = items[0]
                _stash_related(username, items)
                rp = items[0].get("relatedProfiles")
        except (RuntimeError, httpx.HTTPError):
            raise
        rp = rp if isinstance(rp, list) else []

    candidates: List[Dict[str, Any]] = []
    for r in rp or []:
        row = _candidate_row(r)
        if row and row["username"].lower() != username.lower():
            candidates.append(row)
        if len(candidates) >= limit:
            await asyncio.to_thread(_cache_set, cache_key, candidates)
            return candidates

    # Fallback: keyword search — makes discovery work for every account.
    queries = _search_terms_for(username, profile_item)
    if queries:
        try:
            search_rows = await _run_search_actor(queries, limit=limit)
        except (RuntimeError, httpx.HTTPError):
            raise
        fresh = [
            row for row in search_rows
            if row["username"].lower() != username.lower()
            and row["username"].lower() not in {c["username"].lower() for c in candidates}
        ]
        fresh = _rank_by_relevance(fresh, queries)
        candidates.extend(fresh[: max(0, limit - len(candidates))])
    if candidates:
        await asyncio.to_thread(_cache_set, cache_key, candidates)
    return candidates


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------

def _cached(username: str) -> Optional[ProfileData]:
    entry = _profile_cache.get(username)
    if entry and (time.monotonic() - entry[0]) < _CACHE_TTL:
        return entry[1]
    if entry:
        _profile_cache.pop(username, None)
    return None


def _cached_profile_pool_sync(exclude: set, limit: int) -> List[Dict[str, Any]]:
    """Snapshot of every REAL account in the disk cache (within the profile
    TTL), shaped like discovery candidates. Used as a fallback rival pool
    when auto-discovery has nothing to offer — comparative readouts stay
    grounded in real data instead of dead-ending."""
    try:
        with _cache_conn() as conn:
            rows = conn.execute(
                "SELECT key, value, cached_at FROM cache WHERE key LIKE 'profile:%'"
                " ORDER BY cached_at DESC",
            ).fetchall()
    except Exception:
        return []
    now = time.time()
    out: List[Dict[str, Any]] = []
    for key, value, ts in rows:
        handle = key.split(":", 1)[1].strip().lower()
        if not handle or handle in exclude or handle in {o["username"] for o in out}:
            continue
        if now - ts > _DISK_TTL_PROFILE:
            continue
        try:
            d = json.loads(value)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        out.append({
            "username": handle,
            "full_name": d.get("full_name") or "",
            "bio": (d.get("bio") or "")[:160],
            "followers": d.get("followers") or 0,
            "verified": bool(d.get("is_verified")),
            "private": False,
        })
        if len(out) >= max(limit, 1):
            break
    return out


async def get_cached_profile_pool(exclude: Optional[set] = None, limit: int = 30) -> List[Dict[str, Any]]:
    """Async wrapper: real accounts already cached in this app, freshest
    first. Never raises; empty list means the cache has nothing usable."""
    try:
        return await asyncio.to_thread(_cached_profile_pool_sync, set(exclude or ()), limit)
    except Exception:
        return []


async def get_profiles_batch(usernames: List[str]) -> Dict[str, ProfileData]:
    """Fetch several profiles at once. In live mode the missing handles are
    fetched in a SINGLE actor run. Returns a map of lower-cased username ->
    ProfileData for every profile that could be served (missing keys =
    failed fetches the caller should warn about)."""
    wanted: List[str] = []
    for u in usernames:
        try:
            wanted.append(normalize_username(u))
        except ValueError:
            continue

    result: Dict[str, ProfileData] = {}
    missing: List[str] = []
    async with _CACHE_LOCK:
        for u in wanted:
            c = _cached(u)
            if c is not None:
                result[u] = c
            else:
                missing.append(u)

    if not missing:
        return result

    # Disk cache layer — profiles fetched in a previous process (or a previous
    # backend restart) come back instantly, no actor run, no cost.
    still_missing: List[str] = []
    for u in missing:
        disk = await asyncio.to_thread(_disk_profile_get, u)
        if disk is not None:
            result[u] = disk
            async with _CACHE_LOCK:
                _profile_cache[u] = (time.monotonic(), disk)
        else:
            still_missing.append(u)

    if not still_missing:
        return result

    # Provider #0 — official Graph API for every still-missing handle.
    # Per-handle failures (incl. non-professional accounts) are skipped and
    # surface as the batch's usual missing-key warnings; token failures fall
    # through to the existing providers.
    if _has_graph_credentials():
        for i, u in enumerate(still_missing):
            if i:
                await asyncio.sleep(0.5)  # pace app-level rate limits
            try:
                profile = await fetch_live_profile(u)
            except (ValueError, RuntimeError, httpx.HTTPError) as e:
                print(f"[graph] batch fetch failed for @{u}: {e}")
                continue
            await asyncio.to_thread(_disk_profile_set, u, profile)
            async with _CACHE_LOCK:
                _profile_cache[u] = (time.monotonic(), profile)
            result[u] = profile
        still_missing = [u for u in still_missing if u not in result]
        if not still_missing:
            return result

    # LIVE: one batched actor run for every uncached handle (cheapest path).
    failed = list(still_missing)
    if APIFY_TOKENS:
        try:
            items = await _run_actor_multi(still_missing, "details", results_limit=13)
        except (RuntimeError, httpx.HTTPError):
            pass  # pool unavailable (exhausted/benched/blocked) — direct next
        else:
            # Group dataset rows per profile: profile rows by their username,
            # post rows by their owner.
            groups: Dict[str, List[Dict[str, Any]]] = {}
            for it in items:
                if not isinstance(it, dict):
                    continue
                if it.get("followersCount") is not None or it.get("biography") is not None:
                    owner = str(it.get("username") or "").strip().lower()
                else:
                    owner = str(it.get("ownerUsername") or it.get("username") or "").strip().lower()
                if owner:
                    groups.setdefault(owner, []).append(it)

            failed = []
            for u in still_missing:
                group = groups.get(u, [])
                if not group:
                    failed.append(u)  # missing from the run — try direct
                    continue
                try:
                    profile = _map_profile(group, u)
                except ValueError:
                    failed.append(u)
                    continue
                _stash_related(u, group)
                await asyncio.to_thread(_disk_profile_set, u, profile)
                async with _CACHE_LOCK:
                    _profile_cache[u] = (time.monotonic(), profile)
                result[u] = profile

    # Provider #2 — keyless direct fetch for whatever the pool couldn't serve
    # (no tokens configured, pool exhausted, or handles missing from the run).
    # A ValueError (handle does not exist) SKIPS the handle in batch context —
    # the caller surfaces it as a warning — while single get_profile raises
    # the honest 400. One dead rival handle must not kill a research batch.
    if failed and DIRECT_FETCH_ENABLED:
        for i, u in enumerate(failed):
            if i:
                await asyncio.sleep(1.5)  # pace sequential unauthenticated hits
            try:
                profile = await _fetch_direct_profile(u)
            except (ValueError, RuntimeError, httpx.HTTPError):
                continue  # caller surfaces missing handles as warnings
            await asyncio.to_thread(_disk_profile_set, u, profile)
            async with _CACHE_LOCK:
                _profile_cache[u] = (time.monotonic(), profile)
            result[u] = profile

    return result


# ---------------------------------------------------------------------------
# Diagnostics — fetch-event ring buffer + environment snapshot (cloud debug)
# ---------------------------------------------------------------------------

_FETCH_EVENTS: deque = deque(maxlen=50)  # (at, kind, detail) — most recent last


def record_fetch_event(kind: str, detail: str) -> None:
    """Record one fetch outcome for /api/diagnostics.

    kind: 'ok' | 'fail' | 'blocked'. Kept tiny and never raises — diagnostics
    must not be able to break a fetch. Secrets are never recorded.
    """
    try:
        _FETCH_EVENTS.append(
            {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "kind": kind,
                "detail": str(detail)[:200],
            }
        )
    except Exception:
        pass


def diagnostics() -> dict:
    """Cloud-debug snapshot for /api/diagnostics.

    Reports the fetch-layer configuration (pure HTTP, no browser in the fetch
    path), provider state with secrets masked, and the last 50 fetch events
    — so a deployment that cannot fetch can be diagnosed from the outside.
    """

    return {
        "fetch_mode": "http-only (no browser in the fetch path)",
        "providers": {
            "apify_tokens_configured": len(APIFY_TOKENS),
            "graph_api_configured": _has_graph_credentials(),
            "direct_fetch_enabled": DIRECT_FETCH_ENABLED,
            "ig_proxy_set": bool((_IG_PROXY_URL or "").strip()),
        },
        "env": {
            "ig_fetch_mode": os.getenv("IG_FETCH_MODE", "http"),
            "ig_permalink_posts": os.getenv("IG_PERMALINK_POSTS", "12"),
        },
        "recent_fetch_events": list(_FETCH_EVENTS),
    }


async def get_profile(username: str) -> ProfileData:
    """Profile fetch with latency layers (REAL DATA ONLY):

      1. in-memory TTL cache (instant, per-process)
      2. persistent SQLite disk cache (instant, survives restarts)
      3. live providers: Graph API → Apify actor → keyless direct
         → keyless HTTP ladder (web_profile_info, GraphQL feed, HTML)

    Every failure surfaces honestly (ValueError = handle does not exist,
    RuntimeError = all providers blocked). Simulated data is never served.
    """
    username = normalize_username(username)

    async with _CACHE_LOCK:
        cached = _cached(username)
    if cached is not None:
        return cached

    # Layer 2: disk (offloaded to a thread; SQLite is sync).
    disk = await asyncio.to_thread(_disk_profile_get, username)
    if disk is not None:
        record_fetch_event(
            "ok",
            f"@{username} served from disk cache"
            + (f" (age {disk.data_age_hours:.0f}h)" if getattr(disk, 'data_age_hours', None) is not None else ""),
        )
        # Self-heal: a cached REAL profile without posts (stats-only row from
        # an earlier throttled fetch) is re-fetched ONCE per process per
        # handle, so real engagement fills in as soon as a provider can
        # serve it — without slowing down ordinary cache hits.
        if (
            not disk.recent_posts
            and (APIFY_TOKENS or DIRECT_FETCH_ENABLED)
            and username not in _selfheal_attempted
        ):
            _selfheal_attempted.add(username)
            try:
                refreshed = None
                if APIFY_TOKENS:
                    try:
                        refreshed = await _fetch_live_profile(username)
                    except (RuntimeError, httpx.HTTPError):
                        refreshed = None  # pool exhausted/benched — try direct
                if refreshed is None and DIRECT_FETCH_ENABLED:
                    refreshed = await _fetch_direct_profile(username)
                if refreshed is not None and refreshed.recent_posts:
                    await asyncio.to_thread(_disk_profile_set, username, refreshed)
                    async with _CACHE_LOCK:
                        _profile_cache[username] = (time.monotonic(), refreshed)
                    return refreshed
            except (RuntimeError, httpx.HTTPError, ValueError):
                pass  # keep serving the cached real profile
        async with _CACHE_LOCK:
            _profile_cache[username] = (time.monotonic(), disk)
        return disk

    # Stale-while-revalidate: an expired cached profile within 48h PAST its
    # TTL serves INSTANTLY (real data, age-badged) while a background task
    # refreshes it from a live provider. The user stops waiting on cold
    # fetches for handles seen recently; the cache self-heals.
    if APIFY_TOKENS or DIRECT_FETCH_ENABLED or _has_graph_credentials():
        stale = await asyncio.to_thread(_disk_profile_get_any, username)
        _swr_window_h = (_DISK_TTL_PROFILE / 3600) + 48
        if stale is not None and (stale.data_age_hours or 0) <= _swr_window_h:
            async with _CACHE_LOCK:
                _profile_cache[username] = (time.monotonic(), stale)
            if username not in _refresh_inflight:
                _refresh_inflight.add(username)

                async def _bg_refresh() -> None:
                    try:
                        fresh = None
                        if _has_graph_credentials():
                            try:
                                fresh = await fetch_live_profile(username)
                            except (ValueError, RuntimeError, httpx.HTTPError):
                                fresh = None
                        if fresh is None and APIFY_TOKENS:
                            try:
                                fresh = await _fetch_live_profile(username)
                            except (RuntimeError, httpx.HTTPError):
                                fresh = None
                        if fresh is None and DIRECT_FETCH_ENABLED:
                            try:
                                fresh = await _fetch_direct_profile(username)
                            except (ValueError, RuntimeError, httpx.HTTPError):
                                fresh = None
                        if fresh is not None and fresh.recent_posts:
                            await asyncio.to_thread(_disk_profile_set, username, fresh)
                            async with _CACHE_LOCK:
                                _profile_cache[username] = (time.monotonic(), fresh)
                    except Exception:
                        pass  # background refresh must never crash the app
                    finally:
                        _refresh_inflight.discard(username)

                asyncio.create_task(_bg_refresh())
            return stale

    # Layer 3 — Provider #0: OFFICIAL Instagram Graph API (business_discovery).
    # ToS-compliant, no scraping. When credentials are configured this is the
    # PREFERRED source. An unusable TARGET account (not found / private / not
    # a professional account) raises ValueError immediately with a clear
    # message; a TOKEN/app failure only logs and falls through to the next
    # provider so one expired key never takes the app down.
    if _has_graph_credentials():
        try:
            profile = await fetch_live_profile(username)
        except ValueError:
            raise  # honest, account-level error — surfaced to the user
        except (RuntimeError, httpx.HTTPError) as e:
            print(f"[graph] provider #0 failed for @{username}: {e}")
        else:
            await asyncio.to_thread(_disk_profile_set, username, profile)
            async with _CACHE_LOCK:
                _profile_cache[username] = (time.monotonic(), profile)
            return profile

    if bool(APIFY_TOKENS):
        # Layer 3: live fetch via the Apify pool. On ANY provider-level
        # failure (tokens exhausted/benched, actor timeout, network error)
        # fall through to provider #2 — the keyless direct endpoint — before
        # surfacing an error. Real data keeps flowing when Apify runs dry.
        try:
            profile = await _fetch_live_profile(username)
        except (RuntimeError, httpx.HTTPError):
            profile = None
        else:
            await asyncio.to_thread(_disk_profile_set, username, profile)
            async with _CACHE_LOCK:
                _profile_cache[username] = (time.monotonic(), profile)
            return profile

    # Provider #2 — keyless direct fetch (Chrome-rendered page + HTTP
    # fallbacks). Reached when live mode has NO Apify tokens, or when the
    # pool run just failed (e.g. monthly credit exhausted). Needs zero
    # credentials for public profiles, so unknown handles no longer get fake
    # numbers just because Apify credits ran out.
    if DIRECT_FETCH_ENABLED:
        try:
            profile = await _fetch_direct_profile(username)
        except ValueError:
            raise  # handle genuinely doesn't exist — honest error
        except (RuntimeError, httpx.HTTPError) as direct_err:
            record_fetch_event(
                "blocked", f"@{username}: every provider failed — {str(direct_err)[:150]}"
            )
            print(
                f"[fetch] @{username}: every real-data provider failed "
                f"({str(direct_err)[:120]}) — raising an honest error"
            )
            raise
        else:
            await asyncio.to_thread(_disk_profile_set, username, profile)
            async with _CACHE_LOCK:
                _profile_cache[username] = (time.monotonic(), profile)
            return profile

    raise RuntimeError(
        "All real-data providers are disabled (DIRECT_FETCH_ENABLED=false and "
        "no Apify/Graph tokens) — refusing to serve simulated data."
    )
